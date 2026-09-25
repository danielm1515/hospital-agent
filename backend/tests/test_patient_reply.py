"""The patient's side of sub-project 15 (design §7.5, §9, §10): replying, and what the patient sees."""
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent import data_log
from hospital_agent.document_intake import IntakeAnswer
from hospital_agent.human_review import HumanReviewService, NotInReview
from hospital_agent.naming import State
from hospital_agent.session import CaseNotFound, EventRejected, NotWaitingForReply, ReplyKindMismatch, SessionService
from tests.test_patient_request_fsm import escalated
from tests.test_patient_request_fsm import request as raw_request
from tests.test_pdf_upload import PDF, FakeIntake, accepted
from tests.test_review_requests import ADMIN, NURSE, ask, staff_messages


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


def test_a_wrong_type_pdf_leaves_no_reply_row_and_no_new_audit_row(sm, app_engine):
    d, session, reviews = setup(sm, app_engine, accepted("CBC"))
    ask(reviews, d, kind="document", document_type="URINALYSIS")
    before = len(d.trace())
    wrong = session.reply_pdf(d.patient_id, d.case_id, PDF, "cbc.pdf")
    assert wrong.code == "wrong_document_type"
    with d.engine.connect() as conn:
        assert data_log.entries(conn, d.case_id, data_log.DataKind.PATIENT_REPLY) == []
    assert len(d.trace()) == before


def test_a_rejected_document_changes_nothing(sm, app_engine):
    d, session, reviews = setup(sm, app_engine, IntakeAnswer("DOCUMENT_UNREADABLE", None, None, None))
    ask(reviews, d, kind="document", document_type="URINALYSIS")
    assert session.reply_pdf(d.patient_id, d.case_id, PDF, "x.pdf").code == "unreadable"
    assert d.state is State.AWAITING_PATIENT_REPLY


def test_reply_pdf_on_a_question_case_is_a_kind_mismatch_and_never_calls_the_intake(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, template_id="clarify_appointment")
    with pytest.raises(ReplyKindMismatch):
        session.reply_pdf(d.patient_id, d.case_id, PDF, "x.pdf")
    assert session.document_intake.calls == []


def test_a_reply_for_another_patients_case_is_not_found(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, template_id="clarify_appointment")
    with pytest.raises(CaseNotFound):
        session.reply_text("P-SOMEONE-ELSE", d.case_id, "hello")


def test_a_reply_over_the_length_limit_is_refused_with_its_own_code(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, template_id="clarify_appointment")
    with pytest.raises(EventRejected) as refused:
        session.reply_text(d.patient_id, d.case_id, "א" * 2001)
    assert refused.value.reason == "reply_too_long"
    assert d.state is State.AWAITING_PATIENT_REPLY


def test_a_reply_that_raises_leaves_no_readable_entry(sm, app_engine, monkeypatch):
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, template_id="clarify_appointment")

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(sm, "apply", boom)
    with pytest.raises(RuntimeError):
        session.reply_text(d.patient_id, d.case_id, "כן, בהחלט")
    with d.engine.connect() as conn:
        [entry] = data_log.entries(conn, d.case_id, data_log.DataKind.PATIENT_REPLY)
    assert entry.content is None and entry.deleted_at is not None


def test_a_reply_that_loses_a_race_is_not_waiting_for_reply(sm, app_engine, monkeypatch):
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, template_id="clarify_appointment")
    stale = session.case_for_patient(d.patient_id, d.case_id)  # still AwaitingPatientReply/question
    session.reply_text(d.patient_id, d.case_id, "התשובה הראשונה")  # wins the race for real
    assert d.state is State.AWAITING_HUMAN_REVIEW
    monkeypatch.setattr(session, "case_for_patient", lambda *a, **k: stale)
    with pytest.raises(NotWaitingForReply):
        session.reply_text(d.patient_id, d.case_id, "תשובה שנייה שאיחרה")
    with d.engine.connect() as conn:
        late = [e for e in data_log.entries(conn, d.case_id, data_log.DataKind.PATIENT_REPLY)
                if e.content is None]
    assert late  # the late reply's own entry was tombstoned, not just left uncommitted


def test_the_reply_request_deadline_matches_what_the_reviewer_set(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    deadline = datetime.now(UTC) + timedelta(hours=5)
    ask(reviews, d, template_id="clarify_appointment", deadline=deadline)
    assert session.patient_view(d.case_id).reply_request.deadline == deadline


def test_the_reply_request_names_the_requested_document_type(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, kind="document", document_type="URINALYSIS")
    assert session.patient_view(d.case_id).reply_request.document_type == "URINALYSIS"


def test_a_closing_template_is_shown_on_closed(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    reviews.decide(case_id=d.case_id, decision="resolve", reason="done",
                   shown_context_ref=reviews.context(d.case_id).shown_context_ref,
                   message={"template_id": "close_handled"}, **ADMIN)
    view = session.patient_view(d.case_id)
    assert (view.status, view.message) == ("closed", "פנייתך טופלה על ידי הצוות.")


def test_a_rejecting_closing_message_is_shown_on_closed(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    reviews.decide(case_id=d.case_id, decision="reject", reason="out of scope",
                   shown_context_ref=reviews.context(d.case_id).shown_context_ref,
                   message={"template_id": "close_out_of_scope"}, **ADMIN)
    view = session.patient_view(d.case_id)
    assert (view.status, view.message) == (
        "closed", "פנייתך אינה בתחום שהמערכת מטפלת בו. לשאלות אחרות ניתן לפנות למוקד.")


def test_clinical_free_text_is_shown_because_its_approval_was_consumed(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, NURSE, text="נא לציין אם יש לך רגישות ליוד")
    assert session.patient_view(d.case_id).reply_request.message == "נא לציין אם יש לך רגישות ליוד"


def test_a_staff_message_that_is_neither_a_template_nor_approved_is_never_shown(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, template_id="clarify_general")
    with d.engine.begin() as conn:  # a forged entry whose hash is not on any committed row
        data_log.record(conn, d.case_id, d.patient_id, data_log.DataKind.STAFF_MESSAGE, "טקסט שלא אושר",
                        datetime.now(UTC))
    texts = [e.text for e in session.patient_view(d.case_id).conversation]
    assert "טקסט שלא אושר" not in texts


def test_a_template_text_whose_hash_is_uncommitted_is_still_hidden(sm, app_engine):
    """Pins the `content_hash in committed` clause on its own: the text IS one of the fixed
    templates (so the template check alone would let it through), but its hash never rode on
    a committed Transition row, so §7.5's first condition must still hide it."""
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, template_id="clarify_appointment")
    with d.engine.begin() as conn:  # a forged entry: real template text, no committed row names it
        data_log.record(conn, d.case_id, d.patient_id, data_log.DataKind.STAFF_MESSAGE,
                        "פנייתך טופלה על ידי הצוות.", datetime.now(UTC))
    texts = [e.text for e in session.patient_view(d.case_id).conversation]
    assert "פנייתך טופלה על ידי הצוות." not in texts


def test_a_committed_non_template_unapproved_text_is_still_hidden(sm, app_engine):
    """Pins the "template-or-approved" clause on its own: the hash IS on a committed
    PATIENT_REPLY_REQUESTED row (built directly through the State Manager, like
    test_patient_request_fsm does, bypassing the Human Review Service entirely), but the text
    is neither a template nor backed by any ContentApproval, so §7.5's second condition must
    still hide it."""
    d, session, reviews = setup(sm, app_engine)
    text = "טקסט לא מאושר וגם לא תבנית"
    with d.engine.begin() as conn:
        data_log.record(conn, d.case_id, d.patient_id, data_log.DataKind.STAFF_MESSAGE, text, datetime.now(UTC))
    result = raw_request(sm, d, kind="question", content_hash=data_log.content_hash(text))
    assert result.committed
    texts = [e.text for e in session.patient_view(d.case_id).conversation]
    assert text not in texts


def test_a_tombstoned_staff_message_neither_crashes_the_view_nor_appears_in_it(sm, app_engine):
    """An APPROVED (non-template) clinical message, not a template one: its hash stays in the
    approved set after a tombstone (a ContentApproval is about the hash, not the Data Log row's
    current content), so this is the case that actually depends on the `e.content is not None`
    guard - a template text is already screened out on its own, once tombstoned, by
    is_template_text(None) being false."""
    d, session, reviews = setup(sm, app_engine)
    text = "נא לציין אם יש לך רגישות ליוד"
    ask(reviews, d, NURSE, text=text)
    [message] = staff_messages(d)
    with d.engine.begin() as conn:
        data_log.tombstone(conn, message.entry_id, datetime.now(UTC))
    view = session.patient_view(d.case_id)  # must not raise
    assert view.reply_request.message is None
    assert all(text not in e.text for e in view.conversation)


def test_a_question_reply_that_looks_like_a_document_reference_is_shown_verbatim(sm, app_engine):
    """A coincidental match to the reply_pdf reference-line shape, typed as a plain text answer
    to a QUESTION request, must never be mistaken for a document upload: what makes a reply a
    document reply is what was asked (a document_request template message), never the reply's
    own shape - it is shown exactly as the patient wrote it."""
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, template_id="clarify_appointment")
    session.reply_text(d.patient_id, d.case_id, "ok CBC ACCEPTED")
    view = session.patient_view(d.case_id)
    assert [e.text for e in view.conversation if e.sender == "patient"] == ["ok CBC ACCEPTED"]


def test_a_one_character_document_id_still_renders_as_the_label_not_the_raw_line(sm, app_engine):
    """document_intake._ID allows a 1-character id; the old shape-based regex ({2,64}) would
    have let a genuine such reply fall through to the raw reference line. Detection by what was
    asked has no such lower bound, so it renders correctly regardless of the id's length."""
    d, session, reviews = setup(sm, app_engine, accepted("URINALYSIS", doc_id="D"))
    ask(reviews, d, kind="document", document_type="URINALYSIS")
    outcome = session.reply_pdf(d.patient_id, d.case_id, PDF, "urine.pdf")
    assert outcome.code == "accepted"
    view = session.patient_view(d.case_id)
    patient_texts = [e.text for e in view.conversation if e.sender == "patient"]
    assert patient_texts == ["הועלה המסמך: בדיקת שתן"]
    assert "D URINALYSIS ACCEPTED" not in patient_texts
