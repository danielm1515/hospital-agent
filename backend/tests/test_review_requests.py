"""The staff's side of sub-project 15 (design §7, §8, §10): a request to the patient, a closing
message, and the queue - through the real Human Review Service and State Manager."""
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent import data_log, repository
from hospital_agent.human_review import DecisionRejected, HumanReviewService
from hospital_agent.naming import Component, Event, State
from hospital_agent.session import SessionService
from hospital_agent.state_manager import TransitionResult
from tests.test_patient_request_fsm import escalated

NURSE = dict(reviewer_id="coordinator_nurse", reviewer_role="clinical_staff")
ADMIN = dict(reviewer_id="admin_coordinator", reviewer_role="admin_staff")


class NoIntake:  # a configured document-service; these tests never upload
    def submit(self, patient_id, filename, data):
        raise AssertionError("not called")


def service(sm, *, documents=True) -> HumanReviewService:
    return HumanReviewService(sm, SessionService(sm, document_intake=NoIntake() if documents else None))


def ask(reviews, d, who=ADMIN, **fields):
    fields.setdefault("kind", "question")
    fields.setdefault("reason", "the request is unclear")
    fields.setdefault("shown_context_ref", reviews.context(d.case_id).shown_context_ref)
    return reviews.request(case_id=d.case_id, **who, **fields)


def staff_messages(d):
    with d.engine.connect() as conn:
        return [e for e in data_log.entries(conn, d.case_id, data_log.DataKind.STAFF_MESSAGE)]


def test_any_staff_member_asks_with_a_template(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    result = ask(reviews, d, template_id="clarify_did_you_mean", param="preparation")
    assert result.committed and d.state is State.AWAITING_PATIENT_REPLY
    [message] = staff_messages(d)
    assert message.content == "האם התכוונת להוראות ההכנה לתור? נשמח לאישור או לפירוט."
    assert d.case.patient_deadline - datetime.now(UTC) > timedelta(hours=23)  # the 24 h default


def test_free_text_needs_clinical_staff(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    with pytest.raises(DecisionRejected) as refused:
        ask(reviews, d, text="נא לפרט")
    assert refused.value.reason == "clinical_staff_only"
    assert staff_messages(d) == []
    result = ask(reviews, d, NURSE, text="נא לפרט באיזו מחלקה התור")
    assert result.committed
    # The clinical free text is bound the same way the clinical answer is (design §7.3): the
    # Transition row's content_hash is the staff message's, and the AnswerClinicalQuestion
    # ContentApproval it rode in on is consumed in the same transaction.
    [message] = staff_messages(d)
    requested = [r for r in d.trace() if r.event == "PATIENT_REPLY_REQUESTED" and r.record_type == "Transition"]
    assert requested[-1].content_hash == message.content_hash
    with d.engine.connect() as conn:
        [approval] = list(repository.content_approvals_for(conn, d.case_id, "AnswerClinicalQuestion"))
    assert approval.consumed_at is not None


def test_a_document_request_uses_its_template_and_needs_the_document_service(sm, app_engine):
    d = escalated(sm, app_engine)
    with pytest.raises(DecisionRejected) as refused:
        ask(service(sm, documents=False), d, kind="document", document_type="URINALYSIS")
    assert refused.value.reason == "document_service_not_configured"
    reviews = service(sm)
    assert ask(reviews, d, kind="document", document_type="URINALYSIS").committed
    assert (d.case.reply_kind, d.case.requested_document) == ("document", "URINALYSIS")
    assert staff_messages(d)[-1].content == "נא להעלות את המסמך: בדיקת שתן."


@pytest.mark.parametrize("fields, code", [
    ({}, "invalid_request"),  # neither a template nor text
    ({"template_id": "clarify_general", "text": "x"}, "invalid_request"),
    ({"template_id": "close_handled"}, "invalid_template"),  # a closing template is not a question
    ({"template_id": "clarify_did_you_mean", "param": "gossip"}, "invalid_param"),
    ({"kind": "document", "document_type": "X-RAY"}, "invalid_request"),
    ({"kind": "document", "document_type": "URINALYSIS", "param": "x"}, "unexpected_param"),
    ({"kind": "chat", "template_id": "clarify_general"}, "invalid_request"),
    ({"template_id": "clarify_general", "reason": " "}, "reason_required"),
    ({"template_id": "clarify_general", "deadline": datetime.now(UTC) - timedelta(minutes=1)}, "invalid_deadline"),
    ({"template_id": "clarify_general", "deadline": datetime.now(UTC) + timedelta(days=8)}, "invalid_deadline"),
])
def test_input_that_cannot_be_a_request_is_refused_before_anything_is_written(sm, app_engine, fields, code):
    d = escalated(sm, app_engine)
    with pytest.raises(DecisionRejected) as refused:
        ask(service(sm), d, **fields)
    assert refused.value.reason == code
    assert staff_messages(d) == [] and d.state is State.AWAITING_HUMAN_REVIEW


def test_approve_is_gone_once_a_person_has_written(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    assert "approve" in next(i for i in reviews.queue()[0] if i.case_id == d.case_id).allowed_decisions
    ask(reviews, d, template_id="clarify_general")
    sm.apply(d.case_id, Event.PATIENT_REPLY_SUBMITTED, {"reply_kind": "question", "content_hash": "H"},
             Component.SESSION_SERVICE)
    item = next(i for i in reviews.queue()[0] if i.case_id == d.case_id)
    assert item.allowed_decisions == ["resolve", "reject"] and item.required_fields == []
    assert item.human_engaged and item.returned_by == "patient_reply"
    with pytest.raises(DecisionRejected) as refused:
        reviews.decide(case_id=d.case_id, decision="approve", reason="retry",
                       shown_context_ref=reviews.context(d.case_id).shown_context_ref, **NURSE)
    assert refused.value.reason == "human_engaged"


def test_a_closing_template_rides_on_resolve(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    result = reviews.decide(case_id=d.case_id, decision="resolve", reason="out of scope",
                            shown_context_ref=reviews.context(d.case_id).shown_context_ref,
                            message={"template_id": "close_out_of_scope"}, **ADMIN)
    assert result.committed and d.state is State.COMPLETED
    [message] = staff_messages(d)
    closing = [r for r in d.trace() if r.event == "HUMAN_RESOLVED_CASE" and r.record_type == "Transition"]
    assert closing[-1].content_hash == message.content_hash


def test_approve_carries_no_message(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    with pytest.raises(DecisionRejected) as refused:
        reviews.decide(case_id=d.case_id, decision="approve", reason="retry",
                       shown_context_ref=reviews.context(d.case_id).shown_context_ref,
                       message={"template_id": "close_handled"}, **NURSE)
    assert refused.value.reason == "message_not_allowed"


def test_a_clinical_closing_message_is_consumed_with_reject(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    result = reviews.decide(case_id=d.case_id, decision="reject", reason="not ours",
                            shown_context_ref=reviews.context(d.case_id).shown_context_ref,
                            message={"text": "נא לפנות ישירות למרפאה"}, **NURSE)
    assert result.committed and d.state is State.FAILED
    with d.engine.connect() as conn:
        [approval] = [a for a in repository.content_approvals_for(conn, d.case_id, "AnswerClinicalQuestion")]
    assert approval.consumed_at is not None


def test_a_request_that_runs_out_of_time_is_marked_in_the_queue(sm, app_engine):
    from sqlalchemy import text

    from hospital_agent.execution.sla import SlaWorker
    d = escalated(sm, app_engine)
    reviews = service(sm)
    ask(reviews, d, template_id="clarify_general")
    with app_engine.begin() as conn:
        conn.execute(text("UPDATE cases SET patient_deadline = now() - interval '1 minute' WHERE case_id = :c"),
                     {"c": d.case_id})
    SlaWorker(sm).tick()
    assert next(i for i in reviews.queue()[0] if i.case_id == d.case_id).returned_by == "reply_timeout"


def test_the_templates_are_served(sm):
    ids = {t["template_id"] for t in service(sm).templates()}
    assert {"clarify_general", "document_request", "close_no_reply"} <= ids


# --- fail-closed on a message: the two exits _apply() must tombstone (fix round 1) ----------
# Modelled on tests/test_clinical_answer.py:333 and :359 (`test_a_blocked_transition_...` /
# `test_a_raising_transition_...`): sm.apply() can either return a not-committed result, or
# raise (ReprocessLimitExceeded, the Temporal Monitor unavailable). Either way, a staff
# message that rode a ContentApproval must not stay readable if its event never committed.

def test_a_blocked_request_tombstones_the_free_text_message(sm, app_engine, monkeypatch):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    real_apply = sm.apply

    def blocked_request(case_id, event, payload=None, source=Component.EXTERNAL, **kwargs):
        if event is Event.PATIENT_REPLY_REQUESTED and (payload or {}).get("message_approval_id"):
            case = sm.load(case_id)
            return TransitionResult(case_id=case_id, committed=False, state_before=case.state,
                                    state_after=case.state, reason="test_blocked")
        return real_apply(case_id, event, payload, source, **kwargs)

    monkeypatch.setattr(sm, "apply", blocked_request)

    with pytest.raises(DecisionRejected) as refused:
        ask(reviews, d, NURSE, text="נא לפרט באיזו מחלקה התור")
    assert refused.value.reason == "test_blocked"
    [message] = staff_messages(d)
    assert message.content is None
    assert message.deleted_at is not None


def test_a_raising_request_tombstones_the_free_text_message_too(sm, app_engine, monkeypatch):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    real_apply = sm.apply

    class _MonitorUnavailable(RuntimeError):
        pass

    def raising_request(case_id, event, payload=None, source=Component.EXTERNAL, **kwargs):
        if event is Event.PATIENT_REPLY_REQUESTED and (payload or {}).get("message_approval_id"):
            raise _MonitorUnavailable("temporal monitor unavailable")
        return real_apply(case_id, event, payload, source, **kwargs)

    monkeypatch.setattr(sm, "apply", raising_request)

    with pytest.raises(_MonitorUnavailable):
        ask(reviews, d, NURSE, text="נא לפרט באיזו מחלקה התור")
    [message] = staff_messages(d)
    assert message.content is None
    assert message.deleted_at is not None


def test_a_blocked_closing_message_tombstones_the_free_text_message(sm, app_engine, monkeypatch):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    real_apply = sm.apply

    def blocked_reject(case_id, event, payload=None, source=Component.EXTERNAL, **kwargs):
        if event is Event.HUMAN_REJECTED and (payload or {}).get("message_approval_id"):
            case = sm.load(case_id)
            return TransitionResult(case_id=case_id, committed=False, state_before=case.state,
                                    state_after=case.state, reason="test_blocked")
        return real_apply(case_id, event, payload, source, **kwargs)

    monkeypatch.setattr(sm, "apply", blocked_reject)

    with pytest.raises(DecisionRejected) as refused:
        reviews.decide(case_id=d.case_id, decision="reject", reason="not ours",
                       shown_context_ref=reviews.context(d.case_id).shown_context_ref,
                       message={"text": "נא לפנות ישירות למרפאה"}, **NURSE)
    assert refused.value.reason == "test_blocked"
    [message] = staff_messages(d)
    assert message.content is None
    assert message.deleted_at is not None


def test_a_raising_closing_message_tombstones_the_free_text_message_too(sm, app_engine, monkeypatch):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    real_apply = sm.apply

    class _MonitorUnavailable(RuntimeError):
        pass

    def raising_reject(case_id, event, payload=None, source=Component.EXTERNAL, **kwargs):
        if event is Event.HUMAN_REJECTED and (payload or {}).get("message_approval_id"):
            raise _MonitorUnavailable("temporal monitor unavailable")
        return real_apply(case_id, event, payload, source, **kwargs)

    monkeypatch.setattr(sm, "apply", raising_reject)

    with pytest.raises(_MonitorUnavailable):
        reviews.decide(case_id=d.case_id, decision="reject", reason="not ours",
                       shown_context_ref=reviews.context(d.case_id).shown_context_ref,
                       message={"text": "נא לפנות ישירות למרפאה"}, **NURSE)
    [message] = staff_messages(d)
    assert message.content is None
    assert message.deleted_at is not None


# --- the deadline (fix round 1) --------------------------------------------------------------

def test_a_naive_deadline_is_refused_not_a_typeerror(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    naive = datetime.now() + timedelta(hours=5)  # no tzinfo
    with pytest.raises(DecisionRejected) as refused:
        ask(reviews, d, template_id="clarify_general", deadline=naive)
    assert refused.value.reason == "invalid_deadline"


def test_the_default_deadline_respects_a_close_appointment(sm, app_engine):
    from sqlalchemy import text
    d = escalated(sm, app_engine)
    appointment = datetime.now(UTC) + timedelta(hours=10)
    with app_engine.begin() as conn:
        conn.execute(text("UPDATE cases SET appointment_at = :a WHERE case_id = :c"),
                     {"a": appointment, "c": d.case_id})
    reviews = service(sm)
    result = ask(reviews, d, template_id="clarify_general")  # no deadline given
    assert result.committed
    assert datetime.now(UTC) < d.case.patient_deadline <= appointment


def test_a_request_after_the_appointment_has_passed_is_refused_distinctly(sm, app_engine):
    """M2: even the default deadline can never be legal once the appointment is behind us -
    the refusal must name the real problem, not invalid_deadline."""
    from sqlalchemy import text
    d = escalated(sm, app_engine)
    past = datetime.now(UTC) - timedelta(minutes=1)
    with app_engine.begin() as conn:
        conn.execute(text("UPDATE cases SET appointment_at = :a WHERE case_id = :c"),
                     {"a": past, "c": d.case_id})
    reviews = service(sm)
    with pytest.raises(DecisionRejected) as refused:
        ask(reviews, d, template_id="clarify_general")  # no deadline given
    assert refused.value.reason == "appointment_passed"
    assert staff_messages(d) == [] and d.state is State.AWAITING_HUMAN_REVIEW
