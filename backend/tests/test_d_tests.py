"""The §16 D-tests that the Core can already prove (design §10)."""
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from hospital_agent.naming import Component, EscalationKind, Event, State
from hospital_agent.state_manager import StateManager
from tests.driver import Driver
from tests.fakes import BarrierMonitor, fake_ports


def test_d14_a_completed_case_cannot_be_reopened(sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    d.validate()
    d.medical_question()
    d.human(Event.HUMAN_RESOLVED_CASE, d.approval("resolve"))
    assert d.state is State.COMPLETED

    for event, source in [(Event.REQUEST_VALIDATED, Component.SESSION_SERVICE),
                          (Event.HUMAN_APPROVED, Component.EXTERNAL),
                          (Event.DOCUMENT_UPLOADED, Component.EXTERNAL)]:
        result = sm.apply(d.case_id, event, {"text": "x", "identity_verified": True}, source)
        assert (result.committed, result.reason, result.state_after) == (False, "guard_failed", State.COMPLETED)
    assert d.state is State.COMPLETED


def test_d15_concurrent_transient_failures_commit_only_once(sm, app_engine):
    setup = Driver(sm, app_engine)
    setup.to_classified()
    setup.plan()
    setup.propose()
    setup.allow()
    assert setup.state is State.RETRIEVING_DATA
    version = setup.case.state_version

    racing = StateManager(app_engine, BarrierMonitor("TOOL_TRANSIENT_FAILURE", parties=2), fake_ports())
    results = []

    def fail_once():
        d = Driver(racing, app_engine)
        d.case_id = setup.case_id
        results.append(d.transient_failure())

    threads = [threading.Thread(target=fail_once) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert sorted(r.committed for r in results) == [False, True]
    loser = next(r for r in results if not r.committed)
    assert loser.reason == "guard_failed"  # re-processed against Planning: no such row
    assert (setup.state, setup.case.state_version) == (State.PLANNING, version + 1)
    kinds = [(e.record_type, e.event) for e in setup.trace()][-2:]
    assert sorted(kinds) == [("Blocked", "TOOL_TRANSIENT_FAILURE"), ("Transition", "TOOL_TRANSIENT_FAILURE")]


def _to_delivering(d: Driver) -> None:
    d.to_assessing_readiness(required=["referral"], held=["referral"])
    d.readiness_passed()
    d.plan_delivery()
    d.propose()
    d.allow()
    assert d.state is State.DELIVERING


def test_d20_case_resolved_needs_a_successful_delivery(sm, app_engine):
    d = Driver(sm, app_engine)
    _to_delivering(d)
    failed = d.deliver(status="failed")
    assert (failed.committed, failed.reason, d.state) == (False, "guard_failed", State.DELIVERING)
    missing = sm.apply(d.case_id, Event.CASE_RESOLVED, {}, Component.RESPONSE_DELIVERY)
    assert (missing.committed, d.state) == (False, State.DELIVERING)
    assert d.deliver(status="succeeded").committed and d.state is State.COMPLETED


def _to_awaiting_patient_input(d: Driver) -> None:
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"])
    d.missing_information(z3_result="unsat")
    assert d.state is State.AWAITING_PATIENT_INPUT


def test_d25_invalid_upload_stays_in_awaiting_patient_input(sm, app_engine):
    d = Driver(sm, app_engine)
    _to_awaiting_patient_input(d)
    for bad in [{"patient_id": "P-OTHER"}, {"format": "exe"},
                {"expires_at": datetime.now(UTC) - timedelta(days=1)}]:
        result = d.upload("blood_test", **bad)
        assert result.committed and result.state_after is State.AWAITING_PATIENT_INPUT
    assert d.case.held_documents == ["referral"]
    assert d.upload("blood_test").state_after is State.CLASSIFYING


def test_d32_ready_needs_every_required_document(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"])
    result = d.readiness_passed()
    assert (result.committed, result.reason, d.state) == (False, "guard_failed", State.ASSESSING_READINESS)


def test_d34_identity_approval_needs_verified_identity_ref(sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    d.verification_failed()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.PATIENT_VERIFICATION_FAILED)

    without = d.human(Event.HUMAN_APPROVED, d.approval("approve"))
    assert (without.committed, without.reason, d.state) == (False, "identity_not_established", State.AWAITING_HUMAN_REVIEW)

    with_ref = d.human(Event.HUMAN_APPROVED, d.approval("approve", verified_identity_ref="ID-DESK-17"))
    assert with_ref.committed and d.state is State.RECEIVED
    assert d.case.identity_verified is True
    # Session Service no longer has to attest identity: it is in State now.
    assert d.validate(identity_verified=False).state_after is State.CLASSIFYING


def test_d35_z3_counterexample_approval_needs_a_new_patient_deadline(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"])
    sm.escalation.signal(d.case_id, EscalationKind.Z3_COUNTEREXAMPLE, State.ASSESSING_READINESS,
                         Component.READINESS_CHECK)
    assert d.state is State.AWAITING_HUMAN_REVIEW

    without = d.human(Event.HUMAN_APPROVED, d.approval("approve"))
    assert (without.committed, without.reason) == (False, "patient_deadline_missing")

    deadline = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    assert d.human(Event.HUMAN_APPROVED, d.approval("approve", patient_deadline=deadline)).committed
    assert (d.state, d.case.patient_deadline) == (State.AWAITING_PATIENT_INPUT, deadline)


def test_an_approval_is_single_use(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    d.retry_exhausted()
    approval_id = d.approval("approve")
    assert d.human(Event.HUMAN_APPROVED, approval_id).committed
    d.propose()
    d.allow()
    d.retry_exhausted()
    replay = d.human(Event.HUMAN_APPROVED, approval_id)
    assert (replay.committed, replay.reason) == (False, "workflow_decision_invalid")


def test_policy_review_approval_stays_open_for_the_next_policy_decision(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    sm.apply(d.case_id, Event.POLICY_HUMAN_REVIEW_REQUIRED, {"policy_result": "RequireHumanReview"},
             Component.POLICY_SERVICE)
    case = d.case
    approval_id = d.approval("approve", plan_hash=case.plan_hash, current_step=case.current_step)
    assert d.human(Event.HUMAN_APPROVED, approval_id).committed
    assert d.state is State.PLANNING
    with app_engine.connect() as conn:
        consumed = conn.execute(text("SELECT consumed_at FROM approvals WHERE approval_id = :id"),
                                {"id": approval_id}).scalar_one()
    assert consumed is None  # design §12.1: consumed by the next Policy decision (sub-project 2)
