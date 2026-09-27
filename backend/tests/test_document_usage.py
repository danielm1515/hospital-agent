"""Sub-project 19, Task 3 (design D5): the document-service's own LLM call, reported on its
201 answer as `llm_usage`, read strictly by `map_answer` and recorded by the Session Service
against the case (`source=document_service`). Bookkeeping never changes an upload: an invalid
`llm_usage` is dropped with a code-only WARNING, and no recorder means nothing is recorded."""
import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from hospital_agent.api import app as app_module
from hospital_agent.auth import demo_password
from hospital_agent.db import llm_usage
from hospital_agent.document_intake import DocumentUsage, IntakeAnswer, IntakeUnavailable, map_answer
from hospital_agent.execution.http import HttpResponse
from hospital_agent.llm.usage import MAX_TOKENS, LLMUsage
from hospital_agent.llm_costs import UsageRecorder
from hospital_agent.naming import State
from hospital_agent.session import NotWaitingForDocument, SessionService, UploadOutcome
from tests.fakes import SpyRecorder
from tests.test_patient_reply import setup as reply_setup
from tests.test_pdf_upload import FILENAME, PATIENT, PDF, FakeIntake, accepted, awaiting
from tests.test_review_requests import ask

ACCEPTED = {"document_id": "DOC-3F2A1B9C0D4E", "document_type": "CBC", "result": "ACCEPTED"}
USAGE = {"call": "classify", "model": "gpt-5.6-luna", "input_tokens": 900, "cached_input_tokens": 100,
         "output_tokens": 40}


def created(**body):
    return HttpResponse(201, json.dumps(body).encode())


def read(llm_usage_value):
    return map_answer(created(**ACCEPTED, llm_usage=llm_usage_value))


# --- map_answer: strict reading of `llm_usage` ------------------------------------------------

def test_a_classify_usage_is_read_as_document_classify():
    assert read(USAGE).llm_usage == DocumentUsage("DocumentClassify", "gpt-5.6-luna", LLMUsage(900, 100, 40))


def test_a_vision_usage_is_read_as_document_vision():
    answer = read({**USAGE, "call": "vision", "model": "fake"})
    assert answer.llm_usage == DocumentUsage("DocumentVision", "fake", LLMUsage(900, 100, 40))


def test_null_counts_are_a_call_with_no_usage():
    """The provider gave no usable usage: the call was made, its tokens are unknown."""
    answer = read({**USAGE, "input_tokens": None, "cached_input_tokens": None, "output_tokens": None})
    assert answer.llm_usage == DocumentUsage("DocumentClassify", "gpt-5.6-luna", None)


def test_a_null_llm_usage_is_no_call(caplog):
    with caplog.at_level(logging.WARNING):
        assert read(None).llm_usage is None
    assert caplog.records == []


def test_an_answer_without_the_field_is_no_call(caplog):
    """An older document-service: nothing to record, and nothing wrong either."""
    with caplog.at_level(logging.WARNING):
        assert map_answer(created(**ACCEPTED)).llm_usage is None
    assert caplog.records == []


def test_the_limits_themselves_are_accepted():
    answer = read({**USAGE, "model": "a" * 64, "input_tokens": MAX_TOKENS, "cached_input_tokens": MAX_TOKENS,
                   "output_tokens": 0})
    assert answer.llm_usage.usage == LLMUsage(MAX_TOKENS, MAX_TOKENS, 0)
    assert read({**USAGE, "model": "gpt-5.6_luna:2026-09.v1"}).llm_usage.model == "gpt-5.6_luna:2026-09.v1"


INVALID = [
    pytest.param("classify", id="not-an-object"),
    pytest.param([USAGE], id="a-list"),
    pytest.param({**USAGE, "call": "translate"}, id="unknown-call"),
    pytest.param({**USAGE, "call": "Classify"}, id="call-case"),
    pytest.param({**USAGE, "call": None}, id="null-call"),
    pytest.param({key: value for key, value in USAGE.items() if key != "model"}, id="missing-model"),
    pytest.param({key: value for key, value in USAGE.items() if key != "output_tokens"}, id="missing-count"),
    pytest.param({"call": "classify", "model": "fake"}, id="missing-all-counts"),
    pytest.param({**USAGE, "model": ""}, id="empty-model"),
    pytest.param({**USAGE, "model": "a" * 65}, id="long-model"),
    pytest.param({**USAGE, "model": "gpt 5"}, id="space-in-model"),
    pytest.param({**USAGE, "model": "gpt-5\n"}, id="newline-after-model"),
    pytest.param({**USAGE, "model": "מודל"}, id="non-ascii-model"),
    pytest.param({**USAGE, "model": 5}, id="numeric-model"),
    pytest.param({**USAGE, "input_tokens": -1}, id="negative"),
    pytest.param({**USAGE, "output_tokens": MAX_TOKENS + 1}, id="above-cap"),
    pytest.param({**USAGE, "input_tokens": True}, id="bool"),
    pytest.param({**USAGE, "output_tokens": False}, id="bool-false"),
    pytest.param({**USAGE, "input_tokens": 900.0}, id="float"),
    pytest.param({**USAGE, "input_tokens": "900"}, id="string"),
    pytest.param({**USAGE, "cached_input_tokens": 901}, id="cached-above-input"),
    pytest.param({**USAGE, "cached_input_tokens": None}, id="some-counts-null"),
]


@pytest.mark.parametrize("value", INVALID)
def test_an_invalid_usage_is_dropped_with_a_code_only_warning(value, caplog):
    with caplog.at_level(logging.WARNING, logger="hospital_agent.document_intake"):
        answer = read(value)
    assert answer.llm_usage is None
    # the upload's own answer is untouched
    assert (answer.result, answer.document_id, answer.document_type) == ("ACCEPTED", "DOC-3F2A1B9C0D4E", "CBC")
    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [("WARNING", "llm_usage_invalid")]


# --- the Session Service records it -----------------------------------------------------------

def classified(doc_type="ECG", usage=LLMUsage(900, 0, 40), call="DocumentClassify", model="fake"):
    return IntakeAnswer("ACCEPTED", "DOC-A1B2C3D4E5F6", doc_type, None,
                        llm_usage=DocumentUsage(call, model, usage))


def rows(engine):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(llm_usage).order_by(llm_usage.c.usage_id)).mappings()]


def test_an_upload_records_the_document_services_call_against_the_case(sm, app_engine):
    d = awaiting(sm, app_engine)
    spy = SpyRecorder()
    session = SessionService(sm, document_intake=FakeIntake(classified()), usage_recorder=spy)
    assert session.upload_pdf(PATIENT, d.case_id, PDF, FILENAME) == UploadOutcome("accepted", "ECG")
    assert spy.rows == [(d.case_id, "document_service", "DocumentClassify", "fake", "ok", LLMUsage(900, 0, 40))]


def test_a_rejected_upload_is_still_recorded_since_the_call_was_billed(sm, app_engine):
    d = awaiting(sm, app_engine)
    spy = SpyRecorder()
    answer = IntakeAnswer("DOCUMENT_UNREADABLE", "DOC-1", None, None, reason="classifier_unparsable",
                          llm_usage=DocumentUsage("DocumentVision", "gpt-5.6-luna", LLMUsage(1200, 0, 3)))
    session = SessionService(sm, document_intake=FakeIntake(answer), usage_recorder=spy)
    assert session.upload_pdf(PATIENT, d.case_id, PDF, FILENAME).code == "unreadable"
    assert spy.rows == [(d.case_id, "document_service", "DocumentVision", "gpt-5.6-luna", "ok", LLMUsage(1200, 0, 3))]


def test_an_upload_whose_case_moved_on_is_still_recorded(sm, app_engine):
    d = awaiting(sm, app_engine)
    spy = SpyRecorder()
    moved = lambda: SessionService(sm).upload_document(PATIENT, d.case_id, "ECG", "ECG: text upload")  # noqa: E731
    intake = FakeIntake(classified(), before=moved)
    session = SessionService(sm, document_intake=intake, usage_recorder=spy)
    with pytest.raises(NotWaitingForDocument):
        session.upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    assert [row[:3] for row in spy.rows] == [(d.case_id, "document_service", "DocumentClassify")]


def test_a_null_llm_usage_records_nothing(sm, app_engine):
    d = awaiting(sm, app_engine)
    spy = SpyRecorder()
    session = SessionService(sm, document_intake=FakeIntake(accepted("ECG")), usage_recorder=spy)
    assert session.upload_pdf(PATIENT, d.case_id, PDF, FILENAME).code == "accepted"
    assert spy.rows == []


def test_without_a_recorder_nothing_is_recorded(sm, app_engine):
    d = awaiting(sm, app_engine)
    session = SessionService(sm, document_intake=FakeIntake(classified()))
    assert session.upload_pdf(PATIENT, d.case_id, PDF, FILENAME).code == "accepted"
    assert rows(app_engine) == []


def test_a_recorder_that_raises_never_changes_the_upload(sm, app_engine, caplog):
    class Broken:
        def record(self, *args):
            raise RuntimeError("secret P-10041 detail")

    d = awaiting(sm, app_engine)
    session = SessionService(sm, document_intake=FakeIntake(classified()), usage_recorder=Broken())
    with caplog.at_level(logging.WARNING):
        assert session.upload_pdf(PATIENT, d.case_id, PDF, FILENAME) == UploadOutcome("accepted", "ECG")
    assert d.case.state is State.CLASSIFYING
    assert "llm_usage_write_failed error=RuntimeError" in caplog.text
    assert "P-10041" not in caplog.text and "secret" not in caplog.text


def test_the_real_recorder_writes_an_unpriced_row_for_the_fake_model(sm, app_engine):
    """The FakeClassifier's model is `fake`: no price, so the cost is NULL - never a guess."""
    d = awaiting(sm, app_engine)
    session = SessionService(sm, document_intake=FakeIntake(classified()), usage_recorder=UsageRecorder(app_engine))
    session.upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    [row] = rows(app_engine)
    assert (row["case_id"], row["source"], row["call"], row["model"], row["outcome"]) == (
        d.case_id, "document_service", "DocumentClassify", "fake", "ok")
    assert (row["input_tokens"], row["cached_input_tokens"], row["output_tokens"]) == (900, 0, 40)
    assert (row["price_input_per_mtok"], row["cost_usd"]) == (None, None)


def test_the_real_recorder_prices_the_configured_model(sm, app_engine):
    d = awaiting(sm, app_engine)
    session = SessionService(sm, document_intake=FakeIntake(classified(model="gpt-5.6-luna", usage=LLMUsage(
        1000, 0, 100))), usage_recorder=UsageRecorder(app_engine))
    session.upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    [row] = rows(app_engine)
    assert str(row["cost_usd"]) == "0.00032000"  # 1000 x 0.20/1M + 100 x 1.20/1M


def test_a_call_without_usage_is_a_row_with_null_tokens(sm, app_engine):
    d = awaiting(sm, app_engine)
    session = SessionService(sm, document_intake=FakeIntake(classified(model="gpt-5.6-luna", usage=None)),
                             usage_recorder=UsageRecorder(app_engine))
    session.upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    [row] = rows(app_engine)
    assert (row["input_tokens"], row["cost_usd"]) == (None, None)
    assert row["price_input_per_mtok"] is not None  # priced model, nothing billed to report


def test_a_reply_upload_records_the_call_too(sm, app_engine):
    spy = SpyRecorder()
    d, session, reviews = reply_setup(sm, app_engine, classified("URINALYSIS"))
    session.usage_recorder = spy
    ask(reviews, d, kind="document", document_type="URINALYSIS")
    assert session.reply_pdf(d.patient_id, d.case_id, PDF, "urine.pdf").code == "accepted"
    assert spy.rows == [(d.case_id, "document_service", "DocumentClassify", "fake", "ok", LLMUsage(900, 0, 40))]


def test_a_reply_upload_with_a_null_usage_records_nothing(sm, app_engine):
    spy = SpyRecorder()
    d, session, reviews = reply_setup(sm, app_engine, accepted("URINALYSIS"))
    session.usage_recorder = spy
    ask(reviews, d, kind="document", document_type="URINALYSIS")
    session.reply_pdf(d.patient_id, d.case_id, PDF, "urine.pdf")
    assert spy.rows == []


def test_an_unavailable_document_service_records_nothing(sm, app_engine):
    d = awaiting(sm, app_engine)
    spy = SpyRecorder()
    session = SessionService(sm, document_intake=FakeIntake(raises=IntakeUnavailable("classifier_unavailable")),
                             usage_recorder=spy)
    with pytest.raises(IntakeUnavailable):
        session.upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    assert spy.rows == []


# --- the app wires the recorder (brief addition C) --------------------------------------------

def test_the_server_gives_the_session_service_a_recorder_on_its_engine(monkeypatch, app_engine):
    monkeypatch.setenv("DATABASE_URL", app_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(app_module, "select_provider", lambda: None)
    with TestClient(app_module.create_app()) as client:
        recorder = client.app.state.session.usage_recorder
        assert isinstance(recorder, UsageRecorder) and recorder.engine is client.app.state.engine


def test_an_upload_through_the_api_is_recorded_by_the_injected_recorder(app_engine):
    spy = SpyRecorder()
    with TestClient(app_module.create_app(app_engine, document_intake=FakeIntake(classified()),
                                          usage_recorder=spy)) as client:
        d = awaiting(client.app.state.session.sm, app_engine)
        login = client.post("/api/auth/login", json={"user_id": PATIENT, "password": demo_password()})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        response = client.post(f"/api/patient/requests/{d.case_id}/documents/file", headers=headers,
                               files={"file": (FILENAME, PDF, "application/pdf")})
        assert response.status_code == 200
        assert "llm" not in response.text and "cost" not in response.text  # the patient never sees a cost
    assert [row[:3] for row in spy.rows] == [(d.case_id, "document_service", "DocumentClassify")]
