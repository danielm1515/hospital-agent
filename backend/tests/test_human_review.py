"""The Human Review Service (spec §1, §3 human rows, §12.4-§12.5; sub-project 5 design §5)."""
from datetime import UTC, datetime, timedelta

import pytest

from sqlalchemy import func, select

from hospital_agent import data_log, repository
from hospital_agent.db import approvals
from hospital_agent.human_review import (
    RESUMABLE,
    ContextChanged,
    DecisionRejected,
    HumanReviewService,
    NotInReview,
)
from hospital_agent.naming import Component, EscalationKind, Event, State
from hospital_agent.session import CaseNotFound, SessionService
from tests.driver import Driver

REQUEST = "When is my appointment and which documents do I need?"
NURSE = {"reviewer_id": "coordinator_nurse", "reviewer_role": "clinical_staff"}


class Wake:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


@pytest.fixture
def wake():
    return Wake()


@pytest.fixture
def session(sm):
    return SessionService(sm)


@pytest.fixture
def review(sm, session, wake):
    return HumanReviewService(sm, session, wake=wake)


def medical_question(sm, app_engine) -> Driver:
    d = Driver(sm, app_engine)
    d.submit()
    d.validate("Should I stop taking my blood thinner?")
    d.medical_question()
    assert d.case.escalation_kind is EscalationKind.MEDICAL_QUESTION
    return d


def retry_exhausted(sm, app_engine) -> Driver:
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    d.retry_exhausted()
    assert d.case.escalation_kind is EscalationKind.RETRY_EXHAUSTED
    return d


def z3_counterexample(sm, app_engine) -> Driver:
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"])
    sm.escalation.signal(d.case_id, EscalationKind.Z3_COUNTEREXAMPLE, State.ASSESSING_READINESS,
                         Component.READINESS_CHECK, reasons=["hours_until:20"])
    assert d.case.escalation_kind is EscalationKind.Z3_COUNTEREXAMPLE
    return d


def decide(review, case_id, decision, **fields):
    ref = review.context(case_id).shown_context_ref
    arguments = {**NURSE, "reason": "reviewed by staff", "shown_context_ref": ref, **fields}
    return review.decide(case_id=case_id, decision=decision, **arguments)


def approvals_of(app_engine, case_id) -> int:
    """How many approval rows the case has - nothing may be written before the checks pass."""
    with app_engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(approvals)
                            .where(approvals.c.case_id == case_id)).scalar_one()


def approval_of(app_engine, result):
    with app_engine.connect() as conn:
        row = [r for r in repository.load_trace(conn, result.case_id) if r.audit_id == result.audit_id][0]
        return repository.load_approval(conn, row.approval_id)


# --- queue ---------------------------------------------------------------------------------

def test_resumable_is_the_approve_rows_of_section_3():
    assert set(RESUMABLE) == {EscalationKind.PATIENT_VERIFICATION_FAILED, EscalationKind.RETRY_EXHAUSTED,
                              EscalationKind.POLICY_REVIEW, EscalationKind.Z3_COUNTEREXAMPLE,
                              EscalationKind.PATIENT_SLA_EXPIRED}


def test_the_queue_lists_escalations_with_their_allowed_decisions(review, sm, app_engine):
    medical = medical_question(sm, app_engine)
    retry = retry_exhausted(sm, app_engine)
    z3 = z3_counterexample(sm, app_engine)
    Driver(sm, app_engine).to_classified()  # not in review

    items = {item.case_id: item for item in review.queue()}
    assert list(items) == [medical.case_id, retry.case_id, z3.case_id]  # oldest update first

    assert items[medical.case_id].escalation_kind == "MedicalQuestion"
    assert items[medical.case_id].escalated_from_state == "Classifying"
    assert items[medical.case_id].allowed_decisions == ["resolve", "reject"]
    assert items[medical.case_id].required_fields == []
    assert items[medical.case_id].patient_id == "P-10041"

    assert items[retry.case_id].allowed_decisions == ["approve", "resolve", "reject"]
    assert items[retry.case_id].required_fields == []

    assert items[z3.case_id].allowed_decisions == ["approve", "resolve", "reject"]
    assert items[z3.case_id].required_fields == ["patient_deadline"]
    assert items[z3.case_id].reasons == ["hours_until:20"]


# --- context -------------------------------------------------------------------------------

def test_the_context_is_deterministic(review, sm, app_engine):
    d = medical_question(sm, app_engine)
    first, second = review.context(d.case_id), review.context(d.case_id)
    assert first == second
    assert first.shown_context_ref.startswith("ctx-") and len(first.shown_context_ref) == 4 + 64
    assert (first.state, first.escalation_kind, first.escalated_from_state) == (
        "AwaitingHumanReview", "MedicalQuestion", "Classifying")
    assert [entry["content"] for entry in first.data] == ["Should I stop taking my blood thinner?"]
    assert [row["event"] for row in first.trace][-1] == "MEDICAL_QUESTION_DETECTED"


def test_the_context_ref_changes_after_a_new_audit_row(review, sm, app_engine):
    d = medical_question(sm, app_engine)
    before = review.context(d.case_id).shown_context_ref
    blocked = sm.apply(d.case_id, Event.HUMAN_APPROVED, {"approval_id": "APPR-NONE"}, Component.EXTERNAL)
    assert not blocked.committed
    assert review.context(d.case_id).shown_context_ref != before


def test_the_context_excludes_tombstoned_content_and_rejected_uploads(review, session, sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"])
    d.missing_information(z3_result="unsat")
    session.upload_document("P-10041", d.case_id, "blood_test", "FOREIGN RESULTS",
                            document_extra={"patient_id": "P-OTHER"})
    with app_engine.begin() as conn:  # an upload no DOCUMENT_UPLOADED row accepted, not tombstoned
        data_log.record(conn, d.case_id, "P-10041", data_log.DataKind.UPLOADED_DOCUMENT, "NEVER ACCEPTED",
                        sm.clock())
    session.upload_document("P-10041", d.case_id, "blood_test", "ACCEPTED RESULTS")

    contents = [entry["content"] for entry in review.context(d.case_id).data]
    assert contents == [REQUEST, "ACCEPTED RESULTS"]

    request_entry = review.context(d.case_id).data[0]
    before = review.context(d.case_id).shown_context_ref
    assert review.tombstone(d.case_id, request_entry["entry_id"])
    after = review.context(d.case_id)
    assert [entry["content"] for entry in after.data] == ["ACCEPTED RESULTS"]
    assert after.shown_context_ref != before
    assert "FOREIGN RESULTS" not in repr(after) and "NEVER ACCEPTED" not in repr(after)


def test_the_context_of_an_unknown_case(review):
    with pytest.raises(CaseNotFound):
        review.context("CASE-DOES-NOT-EXIST")


# --- decide --------------------------------------------------------------------------------

def test_resolving_a_medical_question_completes_the_case(review, sm, app_engine, wake):
    d = medical_question(sm, app_engine)
    result = decide(review, d.case_id, "resolve")
    assert result.committed and d.state is State.COMPLETED
    approval = approval_of(app_engine, result)
    assert approval.consumed_at is not None
    assert (approval.approval_type, approval.decision, approval.escalation_kind) == (
        "WorkflowDecision", "resolve", "MedicalQuestion")
    assert (approval.reviewer_id, approval.reviewer_role) == ("coordinator_nurse", "clinical_staff")
    assert approval.shown_context_ref.startswith("ctx-")
    assert approval.valid_until - approval.granted_at == timedelta(hours=1)
    assert approval.approval_id.startswith("APPR-") and len(approval.approval_id) == 5 + 12
    assert wake.calls == 1


def test_rejecting_fails_the_case(review, sm, app_engine):
    d = medical_question(sm, app_engine)
    assert decide(review, d.case_id, "reject").committed and d.state is State.FAILED


def test_approving_retry_exhausted_opens_a_new_retry_cycle(review, sm, app_engine):
    d = retry_exhausted(sm, app_engine)
    result = decide(review, d.case_id, "approve")
    assert result.committed and d.state is State.PLANNING
    assert (d.case.retry_cycle, d.case.attempt_count) == (1, 0)


def test_approving_a_z3_counterexample_sets_the_new_deadline(review, sm, app_engine):
    d = z3_counterexample(sm, app_engine)
    deadline = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    with pytest.raises(DecisionRejected) as rejected:
        decide(review, d.case_id, "approve")
    assert rejected.value.reason == "patient_deadline_required"
    assert approvals_of(app_engine, d.case_id) == 0
    assert decide(review, d.case_id, "approve", patient_deadline=deadline).committed
    assert (d.state, d.case.patient_deadline) == (State.AWAITING_PATIENT_INPUT, deadline)


def test_approving_identity_revalidates_the_case(review, session, sm, app_engine, wake):
    case_id = session.submit_request("P-30000", REQUEST, identity_verified=False)
    result = decide(review, case_id, "approve", verified_identity_ref="ID-DESK-17")
    assert result.committed and result.state_after is State.RECEIVED
    case = sm.load(case_id)
    assert (case.state, case.identity_verified) == (State.CLASSIFYING, True)
    assert approval_of(app_engine, result).verified_identity_ref == "ID-DESK-17"
    with app_engine.connect() as conn:  # the stored text was reused, not recorded again
        assert len(data_log.entries(conn, case_id, data_log.DataKind.REQUEST_TEXT)) == 1
    assert wake.calls == 1


def test_a_committed_decision_is_never_reported_as_an_error(review, session, sm, app_engine, wake):
    """The request text was deleted, so the case cannot be revalidated - the approve still stands."""
    case_id = session.submit_request("P-30000", REQUEST, identity_verified=False)
    with app_engine.connect() as conn:
        [entry] = data_log.entries(conn, case_id, data_log.DataKind.REQUEST_TEXT)
    assert review.tombstone(case_id, entry.entry_id)

    result = decide(review, case_id, "approve", verified_identity_ref="ID-DESK-17")
    assert result.committed and result.state_after is State.RECEIVED
    assert sm.load(case_id).state is State.RECEIVED  # it waits for the staff, visibly
    assert approval_of(app_engine, result).consumed_at is not None
    assert wake.calls == 1


def test_approving_identity_needs_the_verified_identity_ref(review, session, sm, app_engine):
    case_id = session.submit_request("P-30000", REQUEST, identity_verified=False)
    for missing in (None, ""):
        with pytest.raises(DecisionRejected) as rejected:
            decide(review, case_id, "approve", verified_identity_ref=missing)
        assert rejected.value.reason == "verified_identity_ref_required"
    assert sm.load(case_id).state is State.AWAITING_HUMAN_REVIEW
    assert approvals_of(app_engine, case_id) == 0  # a missing field is refused before any write


def test_approving_a_medical_question_is_not_allowed(review, sm, app_engine):
    d = medical_question(sm, app_engine)
    with pytest.raises(DecisionRejected) as rejected:
        decide(review, d.case_id, "approve")
    assert rejected.value.reason == "decision_not_allowed"
    assert d.state is State.AWAITING_HUMAN_REVIEW
    assert approvals_of(app_engine, d.case_id) == 0


@pytest.mark.parametrize(("decision", "reason", "code"), [
    ("escalate", "reviewed", "invalid_decision"),
    ("resolve", "", "reason_required"),
    ("resolve", "   ", "reason_required"),
])
def test_invalid_input_is_rejected(review, sm, app_engine, decision, reason, code):
    d = medical_question(sm, app_engine)
    with pytest.raises(DecisionRejected) as rejected:
        decide(review, d.case_id, decision, reason=reason)
    assert rejected.value.reason == code
    assert approvals_of(app_engine, d.case_id) == 0  # invalid input is refused before any write


def test_a_stale_context_ref_is_rejected(review, sm, app_engine):
    d = medical_question(sm, app_engine)
    with pytest.raises(ContextChanged):
        decide(review, d.case_id, "resolve", shown_context_ref="ctx-stale")
    assert d.state is State.AWAITING_HUMAN_REVIEW
    assert approvals_of(app_engine, d.case_id) == 0  # the context is checked before any write
    with app_engine.connect() as conn:
        assert all(row.approval_id is None for row in repository.load_trace(conn, d.case_id))


def test_a_case_not_in_review(review, sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    with pytest.raises(NotInReview):
        decide(review, d.case_id, "resolve")
    with pytest.raises(CaseNotFound):
        review.decide(case_id="CASE-DOES-NOT-EXIST", decision="resolve", reason="x", shown_context_ref="ctx-x",
                      **NURSE)


def test_the_reviewer_role_is_stored_as_given_and_the_guard_judges_it(review, sm, app_engine, wake):
    d = medical_question(sm, app_engine)
    with pytest.raises(DecisionRejected) as rejected:
        decide(review, d.case_id, "resolve", reviewer_id="P-10041", reviewer_role="patient")
    assert rejected.value.reason == "workflow_decision_invalid"
    assert d.state is State.AWAITING_HUMAN_REVIEW
    with app_engine.connect() as conn:
        blocked = repository.load_trace(conn, d.case_id)[-1]
        assert blocked.record_type == "Blocked"
        approval = repository.load_approval(conn, blocked.approval_id)
    assert (approval.reviewer_role, approval.consumed_at) == ("patient", None)
    admin = decide(review, d.case_id, "resolve", reviewer_id="admin_coordinator", reviewer_role="admin_staff")
    assert admin.committed and approval_of(app_engine, admin).reviewer_role == "admin_staff"


def test_a_policy_review_approval_is_bound_to_the_plan_step(review, sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    sm.apply(d.case_id, Event.POLICY_HUMAN_REVIEW_REQUIRED, {"policy_result": "RequireHumanReview"},
             Component.POLICY_SERVICE)
    assert d.case.escalation_kind is EscalationKind.POLICY_REVIEW
    case = d.case
    result = decide(review, d.case_id, "approve")
    assert result.committed and d.state is State.PLANNING
    approval = approval_of(app_engine, result)
    assert (approval.plan_hash, approval.current_step) == (case.plan_hash, case.current_step)


# --- tombstone -----------------------------------------------------------------------------

def test_tombstone_once(review, session, sm, app_engine):
    case_id = session.submit_request("P-10041", REQUEST, identity_verified=True)
    other = session.submit_request("P-20000", REQUEST, identity_verified=True)
    with app_engine.connect() as conn:
        [entry] = data_log.entries(conn, case_id, data_log.DataKind.REQUEST_TEXT)
    assert review.tombstone(other, entry.entry_id) is False  # not that case's entry
    assert review.tombstone(case_id, entry.entry_id) is True
    assert review.tombstone(case_id, entry.entry_id) is False
    assert review.tombstone(case_id, "DATA-DOES-NOT-EXIST") is False
    with app_engine.connect() as conn:
        [entry] = data_log.entries(conn, case_id, data_log.DataKind.REQUEST_TEXT)
    assert entry.content is None and entry.content_hash
