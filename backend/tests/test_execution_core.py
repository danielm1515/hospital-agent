"""The State Manager's execution writes (Execution design §3): the intent row, the start, the outcome."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from hospital_agent import repository
from hospital_agent.db import cases
from hospital_agent.execution.verify import REVERIFICATION_FAILED
from hospital_agent.naming import Component, EscalationKind, Event, State
from hospital_agent.policy.service import decision_event
from hospital_agent.policy.temporal import trace_rows
from hospital_agent.state_manager import ExecutionOutcome, ExecutionStateError
from tests.driver import Driver
from tests.test_policy_d_tests import MEDICAL, at_step, content_approval, insert_approval


def execution(d: Driver, execution_id: str):
    with d.engine.connect() as conn:
        return repository.load_execution(conn, execution_id)


def to_allowed(d: Driver) -> None:
    """Step 1 (CheckAppointment) accepted by the real Policy Service."""
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    assert d.state is State.RETRIEVING_DATA


def test_policy_allowed_records_an_intent_bound_to_the_decision(sm, app_engine):
    d = Driver(sm, app_engine)
    to_allowed(d)
    case, row = d.case, execution(d, d.last_execution_id)
    assert (row.status, row.action, row.step, row.state_version) == ("intent", "CheckAppointment", 1, case.state_version)
    assert (row.plan_hash, row.idempotency_key) == (case.plan_hash, f"{case.case_id}:1:0:1")
    assert row.decision_token and not row.medical_content_flag
    assert case.attempt_count == 0  # incremented only when the call starts


def test_a_decision_for_another_state_version_is_blocked(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    decision = d.policy.decide(d.case, d.request())
    stale = replace(decision, decided_state_version=decision.decided_state_version - 1)
    event, payload = decision_event(stale)
    result = sm.apply(d.case_id, event, payload, Component.POLICY_SERVICE)
    assert (result.committed, d.state) == (False, State.PLANNING)


def test_start_writes_the_audit_pair_and_counts_the_attempt(sm, app_engine):
    d = Driver(sm, app_engine)
    to_allowed(d)
    start = sm.start_execution(d.case_id, d.last_execution_id)
    assert start.started and start.execution.execution_id == d.last_execution_id
    started, recorded = d.trace()[-2:]
    assert [(r.record_type, r.event, r.state_after) for r in (started, recorded)] == [
        ("ExecutionStarted", "TOOL_EXECUTION_STARTED", "RetrievingData"),
        ("ExecutionStarted", "AUDIT_RECORDED", "RetrievingData"),
    ]
    assert started.guards == {"InPlan": True, "IdentityVerified": True, "PatientContextPresent": True,
                              "AttemptsAvailable": True, "medical_content_flag": False}
    assert started.execution_id == recorded.execution_id == d.last_execution_id
    assert (d.case.attempt_count, started.attempt_number) == (1, 1)
    assert execution(d, d.last_execution_id).status == "started"


def test_a_decision_starts_only_once(sm, app_engine):
    d = Driver(sm, app_engine)
    to_allowed(d)
    sm.start_execution(d.case_id, d.last_execution_id)
    again = sm.start_execution(d.case_id, d.last_execution_id)
    assert (again.started, again.reason) == (False, REVERIFICATION_FAILED)
    assert d.trace()[-1].record_type == "Blocked" and d.case.attempt_count == 1


def test_a_start_after_the_last_attempt_is_refused(sm, app_engine):
    d = Driver(sm, app_engine)
    to_allowed(d)
    with app_engine.begin() as conn:
        conn.execute(update(cases).where(cases.c.case_id == d.case_id).values(attempt_count=3))
    refused = sm.start_execution(d.case_id, d.last_execution_id)
    assert (refused.started, refused.reason) == (False, "attempts_exhausted")
    assert execution(d, d.last_execution_id).status == "failed"


def test_the_outcome_is_recorded_with_the_event_that_follows(sm, app_engine):
    d = Driver(sm, app_engine)
    to_allowed(d)
    sm.start_execution(d.case_id, d.last_execution_id)
    appointment_at = datetime.now(UTC) + timedelta(hours=96)
    sm.apply(d.case_id, Event.DATA_RETRIEVED, {"execution_id": d.last_execution_id, "appointment_at": appointment_at},
             Component.TOOL_EXECUTOR, execution_outcome=ExecutionOutcome(d.last_execution_id, "succeeded"))
    outcome, transition = d.trace()[-2:]
    assert (outcome.record_type, outcome.state_after, outcome.outcome) == ("ExecutionSucceeded", "RetrievingData", "success")
    assert (transition.event, transition.state_after) == ("DATA_RETRIEVED", "Planning")
    assert outcome not in trace_rows(d.trace())  # not a transition of the temporal trace
    assert execution(d, d.last_execution_id).status == "succeeded"
    assert d.case.appointment_at == appointment_at


def test_an_outcome_needs_a_running_execution(sm, app_engine):
    d = Driver(sm, app_engine)
    to_allowed(d)
    before = len(d.trace())
    with pytest.raises(ExecutionStateError):
        sm.apply(d.case_id, Event.DATA_RETRIEVED, {"execution_id": d.last_execution_id}, Component.TOOL_EXECUTOR,
                 execution_outcome=ExecutionOutcome(d.last_execution_id, "succeeded"))
    assert len(d.trace()) == before and d.state is State.RETRIEVING_DATA


def test_d20_case_resolved_is_blocked_while_the_delivery_has_no_outcome(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    d.allow()
    sm.start_execution(d.case_id, d.last_execution_id)
    payload = {"execution_id": d.last_execution_id}
    early = sm.apply(d.case_id, Event.CASE_RESOLVED, payload, Component.RESPONSE_DELIVERY)
    assert (early.committed, early.reason, d.state) == (False, "guard_failed", State.DELIVERING)
    sm.apply(d.case_id, Event.CASE_RESOLVED, payload, Component.RESPONSE_DELIVERY,
             execution_outcome=ExecutionOutcome(d.last_execution_id, "succeeded"))
    assert d.state is State.COMPLETED


def test_a_medical_message_consumes_its_content_approval_when_it_starts(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    request = d.request(outgoing_message=MEDICAL)
    approval_id = insert_approval(app_engine, content_approval(d, request.execution_id))
    d.allow(execution_id=request.execution_id, outgoing_message=MEDICAL, approval_id=approval_id)
    assert execution(d, request.execution_id).medical_content_flag
    assert sm.start_execution(d.case_id, request.execution_id).started
    assert d.trace()[-2].guards["ContentApprovalValid"] is True
    with app_engine.connect() as conn:
        assert repository.load_approval(conn, approval_id).consumed_at is not None


def test_readiness_without_an_appointment_time_escalates(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    for result in ({}, {"required_documents": ["referral"], "held_documents": []}):
        d.retrieve_step(**result)
        d.advance()
    d.retrieve_step()
    d.assess()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.Z3_COUNTEREXAMPLE)
