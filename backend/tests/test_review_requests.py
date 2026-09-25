"""The staff's side of sub-project 15 (design §7, §8, §10): a request to the patient, a closing
message, and the queue - through the real Human Review Service and State Manager."""
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent import data_log, repository
from hospital_agent.human_review import DecisionRejected, HumanReviewService
from hospital_agent.naming import Component, Event, State
from hospital_agent.session import SessionService
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
    assert ask(reviews, d, NURSE, text="נא לפרט באיזו מחלקה התור").committed


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
    assert "approve" in next(i for i in reviews.queue() if i.case_id == d.case_id).allowed_decisions
    ask(reviews, d, template_id="clarify_general")
    sm.apply(d.case_id, Event.PATIENT_REPLY_SUBMITTED, {"reply_kind": "question", "content_hash": "H"},
             Component.SESSION_SERVICE)
    item = next(i for i in reviews.queue() if i.case_id == d.case_id)
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
    assert next(i for i in reviews.queue() if i.case_id == d.case_id).returned_by == "reply_timeout"


def test_the_templates_are_served(sm):
    ids = {t["template_id"] for t in service(sm).templates()}
    assert {"clarify_general", "document_request", "close_no_reply"} <= ids
