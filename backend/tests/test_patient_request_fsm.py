"""The extension rows (sub-project 15, design §5): a staff request, the patient's reply, the
deadline, and the case never going back to the agent - through the real State Manager."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from hospital_agent import guards
from hospital_agent.execution.sla import SlaWorker
from hospital_agent.naming import Component, EscalationKind, Event, State
from tests.driver import Driver


def escalated(sm, app_engine) -> Driver:
    """A RetryExhausted case - a kind a person may resume, so "approve" would go back to the agent."""
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    d.retry_exhausted()
    assert d.state is State.AWAITING_HUMAN_REVIEW
    return d


def request(sm, d, *, kind="question", document=None, deadline=None, decision="request",
            content_hash="HASH-MESSAGE"):
    approval_id = d.approval(decision, patient_deadline=deadline or datetime.now(UTC) + timedelta(hours=24))
    payload = {"approval_id": approval_id, "reply_kind": kind, "requested_document": document,
               "content_hash": content_hash}
    return sm.apply(d.case_id, Event.PATIENT_REPLY_REQUESTED, payload, Component.EXTERNAL)


def reply(sm, d, *, kind="question", document_type=None, source=Component.SESSION_SERVICE):
    payload = {"reply_kind": kind, "content_hash": "HASH-REPLY"}
    if document_type is not None:
        payload["document_type"] = document_type
    return sm.apply(d.case_id, Event.PATIENT_REPLY_SUBMITTED, payload, source)


def test_a_request_waits_for_the_patient_with_the_deadline_the_reviewer_signed(sm, app_engine):
    d = escalated(sm, app_engine)
    deadline = datetime.now(UTC) + timedelta(hours=30)
    result = request(sm, d, deadline=deadline)
    assert result.committed and result.state_after is State.AWAITING_PATIENT_REPLY
    case = d.case
    assert (case.human_engaged, case.reply_kind, case.requested_document) == (True, "question", None)
    assert case.patient_deadline == deadline
    assert case.escalation_kind is EscalationKind.RETRY_EXHAUSTED


def test_the_reply_returns_the_case_to_review_with_its_escalation(sm, app_engine):
    d = escalated(sm, app_engine)
    request(sm, d)
    result = reply(sm, d)
    assert result.committed and result.state_after is State.AWAITING_HUMAN_REVIEW
    case = d.case
    assert case.escalation_kind is EscalationKind.RETRY_EXHAUSTED
    assert (case.reply_kind, case.requested_document, case.patient_deadline) == (None, None, None)
    assert case.human_engaged is True


def test_a_reply_that_does_not_come_through_the_session_service_is_blocked(sm, app_engine):
    d = escalated(sm, app_engine)
    request(sm, d)
    result = reply(sm, d, source=Component.EXTERNAL)
    assert not result.committed and d.state is State.AWAITING_PATIENT_REPLY


def test_a_document_reply_must_be_the_requested_type(sm, app_engine):
    d = escalated(sm, app_engine)
    request(sm, d, kind="document", document="URINALYSIS")
    assert not reply(sm, d, kind="document", document_type="CBC").committed
    assert not reply(sm, d, kind="question").committed
    assert reply(sm, d, kind="document", document_type="URINALYSIS").committed


@pytest.mark.parametrize("kind, document", [("question", "CBC"), ("document", None), ("document", "X-RAY"),
                                            ("chat", None)])
def test_a_request_names_a_catalog_document_exactly_when_it_asks_for_one(sm, app_engine, kind, document):
    d = escalated(sm, app_engine)
    assert not request(sm, d, kind=kind, document=document).committed


@pytest.mark.parametrize("offset", [timedelta(minutes=-1), timedelta(days=7, minutes=1)])
def test_the_deadline_is_ahead_and_within_seven_days(sm, app_engine, offset):
    d = escalated(sm, app_engine)
    assert not request(sm, d, deadline=datetime.now(UTC) + offset).committed


def test_the_deadline_is_not_after_a_known_appointment(sm, app_engine):
    d = escalated(sm, app_engine)
    appointment = datetime.now(UTC) + timedelta(hours=10)
    with app_engine.begin() as conn:
        conn.execute(text("UPDATE cases SET appointment_at = :a WHERE case_id = :c"),
                     {"a": appointment, "c": d.case_id})
    assert not request(sm, d, deadline=appointment + timedelta(minutes=1)).committed
    assert request(sm, d, deadline=appointment).committed


def test_a_resolve_approval_cannot_make_a_request(sm, app_engine):
    d = escalated(sm, app_engine)
    result = request(sm, d, decision="resolve")
    assert not result.committed and result.reason == "approval_decision_mismatch"


def test_approve_after_a_request_is_refused_as_human_engaged(sm, app_engine):
    d = escalated(sm, app_engine)
    request(sm, d)
    reply(sm, d)
    result = d.human(Event.HUMAN_APPROVED, d.approval("approve"))
    assert not result.committed
    assert result.reason == "human_engaged"
    assert result.temporal_violation is None  # the guard refuses first - T13 is the backstop (Task 3)
    assert d.state is State.AWAITING_HUMAN_REVIEW


def test_t13_is_the_state_manager_backstop_when_the_guard_is_bypassed(sm, app_engine, monkeypatch):
    """M6: WorkflowDecisionValid is what actually refuses this in production
    (test_approve_after_a_request_is_refused_as_human_engaged above), but the case a person has
    written to must never reach an agent state even if that guard were ever wrong or bypassed -
    T13 is the State Manager's own backstop, evaluated independently of the guard that failed."""
    d = escalated(sm, app_engine)
    request(sm, d)
    reply(sm, d)
    monkeypatch.setitem(guards.GUARDS, "WorkflowDecisionValid", lambda ctx: None)  # let it through
    result = d.human(Event.HUMAN_APPROVED, d.approval("approve"))
    # T13 blocks the transition into Planning before it ever commits (the case is already in
    # AwaitingHumanReview, so - unlike a violation caught mid-flow, e.g. test_temporal_violation_
    # blocks_and_escalates - there is no legal HUMAN_REVIEW_REQUIRED row to re-escalate into the
    # state it never left; the backstop still holds by refusing the transition outright).
    assert not result.committed
    assert result.reason == "temporal_violation:T13"
    assert result.temporal_violation == "T13"
    assert not result.escalation.committed
    case = d.case
    assert case.state is State.AWAITING_HUMAN_REVIEW
    assert case.escalation_kind is EscalationKind.RETRY_EXHAUSTED  # never overwritten - nothing committed


@pytest.mark.parametrize("event, target", [(Event.HUMAN_RESOLVED_CASE, State.COMPLETED),
                                           (Event.HUMAN_REJECTED, State.FAILED)])
def test_resolve_and_reject_still_close_after_a_reply(sm, app_engine, event, target):
    d = escalated(sm, app_engine)
    request(sm, d)
    reply(sm, d)
    decision = {Event.HUMAN_RESOLVED_CASE: "resolve", Event.HUMAN_REJECTED: "reject"}[event]
    result = d.human(event, d.approval(decision))
    assert result.committed and result.state_after is target


def test_an_unanswered_request_goes_back_to_review_with_its_escalation(sm, app_engine):
    d = escalated(sm, app_engine)
    request(sm, d)
    with app_engine.begin() as conn:
        conn.execute(text("UPDATE cases SET patient_deadline = now() - interval '1 minute' WHERE case_id = :c"),
                     {"c": d.case_id})
    [result] = SlaWorker(sm).tick()
    assert result.committed and result.state_after is State.AWAITING_HUMAN_REVIEW
    case = d.case
    assert case.escalation_kind is EscalationKind.RETRY_EXHAUSTED  # never PatientSlaExpired (design §5.2)
    assert (case.reply_kind, case.patient_deadline, case.human_engaged) == (None, None, True)
