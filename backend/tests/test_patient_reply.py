"""The patient's side of sub-project 15 (design §7.5, §9, §10): replying, and what the patient sees."""
import pytest

from hospital_agent.document_intake import IntakeAnswer
from hospital_agent.human_review import HumanReviewService, NotInReview
from hospital_agent.naming import State
from hospital_agent.session import NotWaitingForReply, ReplyKindMismatch, SessionService
from tests.test_patient_request_fsm import escalated
from tests.test_pdf_upload import PDF, FakeIntake, accepted
from tests.test_review_requests import ADMIN, NURSE, ask


def setup(sm, app_engine, *answers):
    d = escalated(sm, app_engine)
    session = SessionService(sm, document_intake=FakeIntake(*answers))
    return d, session, HumanReviewService(sm, session)


def test_a_question_waits_for_a_text_reply_and_the_patient_sees_the_thread(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, template_id="clarify_appointment")
    view = session.patient_view(d.case_id)
    assert view.status == "needs_reply"
    assert view.reply_request.kind == "question"
    assert view.reply_request.message.startswith("האם הפנייה נוגעת לתור קיים")
    assert view.reply_request.deadline is not None
    session.reply_text(d.patient_id, d.case_id, "כן, התור ב־3 באוקטובר בקרדיולוגיה")
    view = session.patient_view(d.case_id)
    assert view.status == "in_review" and view.reply_request is None
    assert [(e.sender, e.text) for e in view.conversation] == [
        ("staff", "האם הפנייה נוגעת לתור קיים? אם כן, נא לציין את התאריך או את המחלקה."),
        ("patient", "כן, התור ב־3 באוקטובר בקרדיולוגיה"),
    ]


def test_a_reply_is_refused_when_nothing_was_asked_or_something_else_was(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    with pytest.raises(NotWaitingForReply):
        session.reply_text(d.patient_id, d.case_id, "hello")
    ask(reviews, d, kind="document", document_type="URINALYSIS")
    with pytest.raises(ReplyKindMismatch):
        session.reply_text(d.patient_id, d.case_id, "hello")
    with pytest.raises(NotInReview):  # the case waits for the patient, not for a reviewer
        ask(reviews, d, template_id="clarify_general")


def test_a_document_reply_must_be_the_requested_type(sm, app_engine):
    d, session, reviews = setup(sm, app_engine, accepted("CBC"), accepted("URINALYSIS"))
    ask(reviews, d, kind="document", document_type="URINALYSIS")
    wrong = session.reply_pdf(d.patient_id, d.case_id, PDF, "cbc.pdf")
    assert (wrong.code, wrong.document_type) == ("wrong_document_type", "CBC")
    assert d.state is State.AWAITING_PATIENT_REPLY
    right = session.reply_pdf(d.patient_id, d.case_id, PDF, "urine.pdf")
    assert (right.code, right.document_type) == ("accepted", "URINALYSIS")
    assert d.state is State.AWAITING_HUMAN_REVIEW
    view = session.patient_view(d.case_id)
    assert view.conversation[-1].sender == "patient"
    assert view.conversation[-1].text == "הועלה המסמך: בדיקת שתן"


def test_a_rejected_document_changes_nothing(sm, app_engine):
    d, session, reviews = setup(sm, app_engine, IntakeAnswer("DOCUMENT_UNREADABLE", None, None, None))
    ask(reviews, d, kind="document", document_type="URINALYSIS")
    assert session.reply_pdf(d.patient_id, d.case_id, PDF, "x.pdf").code == "unreadable"
    assert d.state is State.AWAITING_PATIENT_REPLY


def test_a_closing_template_is_shown_on_closed(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    reviews.decide(case_id=d.case_id, decision="resolve", reason="done",
                   shown_context_ref=reviews.context(d.case_id).shown_context_ref,
                   message={"template_id": "close_handled"}, **ADMIN)
    view = session.patient_view(d.case_id)
    assert (view.status, view.message) == ("closed", "פנייתך טופלה על ידי הצוות.")


def test_clinical_free_text_is_shown_because_its_approval_was_consumed(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, NURSE, text="נא לציין אם יש לך רגישות ליוד")
    assert session.patient_view(d.case_id).reply_request.message == "נא לציין אם יש לך רגישות ליוד"


def test_a_staff_message_that_is_neither_a_template_nor_approved_is_never_shown(sm, app_engine):
    from datetime import UTC, datetime

    from hospital_agent import data_log
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, template_id="clarify_general")
    with d.engine.begin() as conn:  # a forged entry whose hash is not on any committed row
        data_log.record(conn, d.case_id, d.patient_id, data_log.DataKind.STAFF_MESSAGE, "טקסט שלא אושר",
                        datetime.now(UTC))
    texts = [e.text for e in session.patient_view(d.case_id).conversation]
    assert "טקסט שלא אושר" not in texts
