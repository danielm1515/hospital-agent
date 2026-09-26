"""Sub-project 13: the patient's PDF, forwarded by the Session Service to the document-service
(design §5.3). Through the real Session Service and State Manager; only the document-service is
a fake - a class with `submit` that returns scripted answers and records its calls."""
import logging

import pytest
from fastapi.testclient import TestClient

from hospital_agent import data_log, repository
from hospital_agent.api.app import UPLOAD_BODY_LIMIT, create_app
from hospital_agent.auth import demo_password
from hospital_agent.document_intake import IntakeAnswer, IntakeUnavailable
from hospital_agent.naming import Event, State
from hospital_agent.session import CaseNotFound, NotWaitingForDocument, SessionService, UploadOutcome
from tests.driver import Driver

PATIENT, OTHER = "P-10041", "P-20000"
PDF = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF"
FILENAME = "my-ecg-scan.pdf"


class FakeIntake:
    def __init__(self, *answers, raises=None, before=None):
        self.answers, self.raises, self.before, self.calls = list(answers), raises, before, []

    def submit(self, patient_id, filename, data):
        self.calls.append((patient_id, filename, data))
        if self.before:
            self.before()
        if self.raises:
            raise self.raises
        return self.answers.pop(0)


def accepted(doc_type, doc_id="DOC-A1B2C3D4E5F6"):
    return IntakeAnswer("ACCEPTED", doc_id, doc_type, None)


def awaiting(sm, app_engine) -> Driver:
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["CBC", "ECG"], held=["CBC"])
    d.missing_information(z3_result="unsat")
    assert d.state is State.AWAITING_PATIENT_INPUT
    return d


def uploads(engine, case_id):
    with engine.connect() as conn:
        return data_log.entries(conn, case_id, data_log.DataKind.UPLOADED_DOCUMENT)


def document_events(engine, case_id):
    with engine.connect() as conn:
        return [r for r in repository.load_trace(conn, case_id) if r.event == Event.DOCUMENT_UPLOADED.value]


def unchanged(d, engine, version):
    assert d.case.state is State.AWAITING_PATIENT_INPUT
    assert d.case.state_version == version
    assert d.case.held_documents == ["CBC"]
    assert uploads(engine, d.case_id) == []
    assert document_events(engine, d.case_id) == []


# --- rule 5: an effective type ------------------------------------------------------------------

def test_an_accepted_required_document_goes_down_the_existing_upload_path(sm, app_engine):
    d = awaiting(sm, app_engine)
    intake = FakeIntake(accepted("ECG"))
    outcome = SessionService(sm, document_intake=intake).upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    assert outcome == UploadOutcome("accepted", "ECG")
    assert intake.calls == [(PATIENT, FILENAME, PDF)]
    assert d.case.state is State.CLASSIFYING
    assert d.case.held_documents == ["CBC", "ECG"]  # HOLD_DOCUMENT appended the type
    entry = uploads(app_engine, d.case_id)[-1]
    assert entry.content == "DOC-A1B2C3D4E5F6 ECG ACCEPTED"  # the reference line, never the PDF
    [row] = document_events(app_engine, d.case_id)
    assert (row.record_type, row.state_after, row.content_hash) == (
        "Transition", State.CLASSIFYING.value, entry.content_hash)


def test_an_accepted_document_that_is_not_required_changes_nothing(sm, app_engine):
    d = awaiting(sm, app_engine)
    version = d.case.state_version
    outcome = SessionService(sm, document_intake=FakeIntake(accepted("URINALYSIS"))).upload_pdf(
        PATIENT, d.case_id, PDF, FILENAME)
    assert outcome == UploadOutcome("not_required", "URINALYSIS")
    unchanged(d, app_engine, version)


def test_an_accepted_document_already_held_changes_nothing(sm, app_engine):
    d = awaiting(sm, app_engine)
    version = d.case.state_version
    outcome = SessionService(sm, document_intake=FakeIntake(accepted("CBC"))).upload_pdf(
        PATIENT, d.case_id, PDF, FILENAME)
    assert outcome == UploadOutcome("already_received", "CBC")
    unchanged(d, app_engine, version)


def test_a_duplicate_of_a_required_document_is_the_document_already_delivered(sm, app_engine):
    """Design §4.1: a retry after a timeout - the original was accepted, the case never heard."""
    d = awaiting(sm, app_engine)
    intake = FakeIntake(IntakeAnswer("DUPLICATE_DOCUMENT", "DOC-NEW000000001", "ECG", "DOC-ORIG00000001"))
    outcome = SessionService(sm, document_intake=intake).upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    assert outcome == UploadOutcome("accepted", "ECG")
    assert d.case.state is State.CLASSIFYING and d.case.held_documents == ["CBC", "ECG"]
    assert uploads(app_engine, d.case_id)[-1].content == "DOC-ORIG00000001 ECG ACCEPTED"


# --- rule 6: no effective type --------------------------------------------------------------------

@pytest.mark.parametrize("answer, code", [
    (IntakeAnswer("NON_MEDICAL_DOCUMENT", "DOC-1", None, None), "not_medical"),
    (IntakeAnswer("DOCUMENT_UNREADABLE", "DOC-1", None, None), "unreadable"),
    (IntakeAnswer("DOCUMENT_EXPIRED", "DOC-1", "ECG", None), "expired"),
    (IntakeAnswer("PATIENT_MISMATCH", "DOC-1", "ECG", None), "not_yours"),
    (IntakeAnswer("SOMETHING_NEW", "DOC-1", "ECG", None), "unreadable"),
    (IntakeAnswer("DUPLICATE_DOCUMENT", "DOC-1", "ECG", None), "unreadable"),
    (IntakeAnswer("DUPLICATE_DOCUMENT", "DOC-1", None, "DOC-ORIG"), "unreadable"),
    (IntakeAnswer("ACCEPTED", "DOC-1", None, None), "unreadable"),
])
def test_a_rejection_is_its_code_and_no_event(sm, app_engine, answer, code):
    d = awaiting(sm, app_engine)
    version = d.case.state_version
    outcome = SessionService(sm, document_intake=FakeIntake(answer)).upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    assert outcome == UploadOutcome(code, None)
    unchanged(d, app_engine, version)


# --- sub-project 17 task 2: a reason maps to a finer patient code, no event either way -------------

@pytest.mark.parametrize("result, reason, code", [
    ("DOCUMENT_UNREADABLE", "unknown_type", "unrecognised_type"),
    ("DOCUMENT_UNREADABLE", "future_date", "bad_date"),
    ("DOCUMENT_UNREADABLE", "no_text_layer", "unreadable_scan"),
    ("DOCUMENT_UNREADABLE", "not_supported_format", "unsupported_format"),
    ("DOCUMENT_UNREADABLE", "too_large", "too_large"),
    ("DOCUMENT_UNREADABLE", "parse_error", "unreadable"),
    ("DOCUMENT_UNREADABLE", "too_many_pages", "unreadable"),
    ("DOCUMENT_UNREADABLE", "too_much_text", "unreadable"),
    ("DOCUMENT_UNREADABLE", "classifier_unparsable", "unreadable"),
    ("DOCUMENT_UNREADABLE", None, "unreadable"),
    ("DOCUMENT_EXPIRED", "no_date", "no_date"),
    ("DOCUMENT_EXPIRED", "too_old", "expired"),
    ("DOCUMENT_EXPIRED", None, "expired"),
])
def test_a_reason_maps_to_a_finer_code_with_no_event(sm, app_engine, result, reason, code):
    d = awaiting(sm, app_engine)
    version = d.case.state_version
    answer = IntakeAnswer(result, "DOC-1", None, None, reason)
    outcome = SessionService(sm, document_intake=FakeIntake(answer)).upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    assert outcome == UploadOutcome(code, None)
    unchanged(d, app_engine, version)


def test_the_log_line_carries_the_real_reason_and_no_content(sm, app_engine, caplog):
    d = awaiting(sm, app_engine)
    answer = IntakeAnswer("DOCUMENT_UNREADABLE", "DOC-SECRET00000001", None, None, "no_text_layer")
    with caplog.at_level(logging.INFO, logger="hospital_agent.session"):
        SessionService(sm, document_intake=FakeIntake(answer)).upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    lines = [r.getMessage() for r in caplog.records if r.name == "hospital_agent.session"]
    assert "pdf upload: unreadable_scan (no_text_layer)" in lines
    for secret in (PATIENT, FILENAME, "DOC-SECRET00000001"):
        assert secret not in caplog.text


def test_no_reason_logs_only_the_code(sm, app_engine, caplog):
    d = awaiting(sm, app_engine)
    answer = IntakeAnswer("NON_MEDICAL_DOCUMENT", "DOC-1", None, None)
    with caplog.at_level(logging.INFO, logger="hospital_agent.session"):
        SessionService(sm, document_intake=FakeIntake(answer)).upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    lines = [r.getMessage() for r in caplog.records if r.name == "hospital_agent.session"]
    assert "pdf upload: not_medical" in lines
    assert not any("(" in line for line in lines)


# --- rules 1 and 3: checked first; an unavailable service records nothing ---------------------------

def test_a_case_that_is_not_waiting_for_a_document_is_refused_before_anything_is_sent(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    intake = FakeIntake(accepted("ECG"))
    with pytest.raises(NotWaitingForDocument):
        SessionService(sm, document_intake=intake).upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    assert intake.calls == []
    assert uploads(app_engine, d.case_id) == []


def test_another_patients_case_is_not_found_before_anything_is_sent(sm, app_engine):
    d = awaiting(sm, app_engine)
    intake = FakeIntake(accepted("ECG"))
    for case_id in (d.case_id, "CASE-DOES-NOT-EXIST"):
        with pytest.raises(CaseNotFound):
            SessionService(sm, document_intake=intake).upload_pdf(OTHER, case_id, PDF, FILENAME)
    assert intake.calls == []


def test_a_case_that_moved_on_while_the_service_answered_is_not_waiting_any_more(sm, app_engine):
    d = awaiting(sm, app_engine)
    service = SessionService(sm)
    moved = lambda: service.upload_document(PATIENT, d.case_id, "ECG", "ECG: text upload")  # noqa: E731
    pdf = SessionService(sm, document_intake=FakeIntake(accepted("ECG"), before=moved))
    with pytest.raises(NotWaitingForDocument):
        pdf.upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    assert [e.content for e in uploads(app_engine, d.case_id)] == ["ECG: text upload"]
    assert len(document_events(app_engine, d.case_id)) == 1


def test_an_unavailable_document_service_records_nothing(sm, app_engine):
    d = awaiting(sm, app_engine)
    version = d.case.state_version
    with pytest.raises(IntakeUnavailable):
        SessionService(sm, document_intake=FakeIntake(raises=IntakeUnavailable("no_answer"))).upload_pdf(
            PATIENT, d.case_id, PDF, FILENAME)
    unchanged(d, app_engine, version)


# --- rule 7: the application log -------------------------------------------------------------------

def test_the_application_log_holds_only_the_outcome_code(sm, app_engine, caplog):
    d = awaiting(sm, app_engine)
    intake = FakeIntake(IntakeAnswer("PATIENT_MISMATCH", "DOC-REJ000000001", "ECG", None),
                        accepted("ECG", "DOC-ACC000000001"))
    service = SessionService(sm, document_intake=intake)
    with caplog.at_level(logging.DEBUG):
        service.upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
        service.upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    session_lines = [r.getMessage() for r in caplog.records if r.name == "hospital_agent.session"]
    assert any("not_yours" in line for line in session_lines)
    assert any("accepted" in line for line in session_lines)
    text = caplog.text + "".join(str(r.args) for r in caplog.records)
    for secret in (PATIENT, FILENAME, "DOC-REJ000000001", "DOC-ACC000000001", "ECG ACCEPTED"):
        assert secret not in text


# --- the view ----------------------------------------------------------------------------------------

def test_the_view_says_which_upload_the_screen_offers(sm, app_engine):
    d = awaiting(sm, app_engine)
    assert SessionService(sm, document_intake=FakeIntake()).patient_view(d.case_id).document_upload == "file"
    assert SessionService(sm).patient_view(d.case_id).document_upload == "text"


# --- through the API ------------------------------------------------------------------------------

def api(app_engine, intake):
    return TestClient(create_app(app_engine, document_intake=intake))


def token(client, user_id=PATIENT):
    answer = client.post("/api/auth/login", json={"user_id": user_id, "password": demo_password()})
    return {"Authorization": f"Bearer {answer.json()['token']}"}


def post_pdf(client, case_id, data=PDF, headers=None):
    return client.post(f"/api/patient/requests/{case_id}/documents/file", headers=headers or token(client),
                       files={"file": (FILENAME, data, "application/pdf")})


def api_awaiting(client, app_engine) -> Driver:
    return awaiting(client.app.state.session.sm, app_engine)


def test_the_route_answers_the_outcome_and_the_patient_view(app_engine):
    intake = FakeIntake(accepted("ECG"))
    with api(app_engine, intake) as client:
        d = api_awaiting(client, app_engine)
        response = post_pdf(client, d.case_id)
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"upload", "request"}
        assert body["upload"] == {"code": "accepted", "document_type": "ECG"}
        assert body["request"]["case_id"] == d.case_id
        assert body["request"]["status"] == "in_progress"
        assert body["request"]["document_upload"] == "file"
    assert intake.calls == [(PATIENT, FILENAME, PDF)]


def test_the_route_answers_a_rejection_with_200_and_its_code(app_engine):
    with api(app_engine, FakeIntake(IntakeAnswer("DOCUMENT_EXPIRED", "DOC-1", "ECG", None))) as client:
        d = api_awaiting(client, app_engine)
        response = post_pdf(client, d.case_id)
        assert response.status_code == 200
        assert response.json()["upload"] == {"code": "expired", "document_type": None}
        assert response.json()["request"]["status"] == "needs_document"
        assert "DOC-1" not in response.text  # the document-service's id stays on the server


def test_without_a_client_the_file_route_is_not_enabled_and_the_view_says_text(app_engine):
    with api(app_engine, None) as client:
        d = api_awaiting(client, app_engine)
        response = post_pdf(client, d.case_id)
        assert response.status_code == 404 and response.json() == {"detail": "file_upload_not_enabled"}
        view = client.get(f"/api/patient/requests/{d.case_id}", headers=token(client)).json()
        assert view["document_upload"] == "text"


def test_another_patients_case_is_404_and_a_case_not_waiting_is_409(app_engine):
    intake = FakeIntake(accepted("ECG"))
    with api(app_engine, intake) as client:
        d = api_awaiting(client, app_engine)
        response = post_pdf(client, d.case_id, headers=token(client, OTHER))
        assert response.status_code == 404 and response.json() == {"detail": "case_not_found"}
        busy = Driver(client.app.state.session.sm, app_engine)
        busy.to_classified()
        response = post_pdf(client, busy.case_id)
        assert response.status_code == 409 and response.json() == {"detail": "not_waiting_for_document"}
    assert intake.calls == []


def test_an_unavailable_document_service_is_503(app_engine):
    with api(app_engine, FakeIntake(raises=IntakeUnavailable("status_503"))) as client:
        d = api_awaiting(client, app_engine)
        response = post_pdf(client, d.case_id)
        assert response.status_code == 503 and response.json() == {"detail": "document_service_unavailable"}


def test_a_classifier_unavailable_provider_failure_is_also_503(app_engine):
    """Sub-project 17 task 2 decision 1: a provider failure is 503 classifier_unavailable from
    the document-service, and the patient sees the same 503 document_service_unavailable as any
    other unavailable answer - never a verdict on the file."""
    with api(app_engine, FakeIntake(raises=IntakeUnavailable("classifier_unavailable"))) as client:
        d = api_awaiting(client, app_engine)
        response = post_pdf(client, d.case_id)
        assert response.status_code == 503 and response.json() == {"detail": "document_service_unavailable"}


def test_a_file_over_10_mb_is_413(app_engine):
    intake = FakeIntake(accepted("ECG"))
    with api(app_engine, intake) as client:
        d = api_awaiting(client, app_engine)
        response = post_pdf(client, d.case_id, data=b"x" * (10 * 1024 * 1024 + 1))
        assert response.status_code == 413 and response.json() == {"detail": "too_large"}
    assert intake.calls == []


def test_a_missing_file_is_422(app_engine):
    with api(app_engine, FakeIntake()) as client:
        d = api_awaiting(client, app_engine)
        response = client.post(f"/api/patient/requests/{d.case_id}/documents/file", headers=token(client),
                               data={"other": "x"})
        assert response.status_code == 422 and response.json() == {"detail": "invalid_body"}


def test_a_staff_token_is_403(app_engine):
    with api(app_engine, FakeIntake()) as client:
        d = api_awaiting(client, app_engine)
        response = post_pdf(client, d.case_id, headers=token(client, "coordinator_nurse"))
        assert response.status_code == 403 and response.json() == {"detail": "patients_only"}


# --- the upload-size middleware: refused before the body is read -------------------------------------

class Tripwire:
    """An ASGI app standing in for the router: it records whether it ran at all."""

    def __init__(self):
        self.ran = False


async def _asgi_call(app, method, path, headers):
    sent, received = [], []

    async def receive():
        received.append(True)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()], "http_version": "1.1",
             "scheme": "http", "server": ("test", 80), "client": ("test", 1), "root_path": ""}
    await app(scope, receive, send)
    return sent, received


def _run(app, method, path, headers):
    import asyncio
    return asyncio.run(_asgi_call(app, method, path, headers))


def _guard():
    from hospital_agent.api.app import UploadSizeLimit
    tripwire = Tripwire()

    async def inner(scope, receive, send):
        tripwire.ran = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    return UploadSizeLimit(inner), tripwire


@pytest.mark.parametrize("path", [
    "/api/patient/requests/CASE-1/documents/file",
    "/api/patient/requests/CASE-1/reply/file",  # sub-project 15: the same middleware guards it
])
@pytest.mark.parametrize("headers, status, code", [
    ({}, 411, "length_required"),
    ({"Content-Length": "abc"}, 411, "length_required"),
    ({"Content-Length": "-1"}, 411, "length_required"),
    ({"Content-Length": str(10 * 1024 * 1024 + 64 * 1024 + 1)}, 413, "too_large"),
])
def test_the_middleware_refuses_before_the_body_is_read(path, headers, status, code):
    import json
    guard, tripwire = _guard()
    sent, received = _run(guard, "POST", path, headers)
    assert not tripwire.ran and received == []
    assert sent[0]["status"] == status
    assert (b"content-type", b"application/json") in sent[0]["headers"]
    assert json.loads(sent[1]["body"]) == {"detail": code}


@pytest.mark.parametrize("method, path, headers", [
    ("POST", "/api/patient/requests/CASE-1/documents/file", {"Content-Length": str(UPLOAD_BODY_LIMIT)}),
    ("POST", "/api/patient/requests/CASE-1/reply/file", {"Content-Length": str(UPLOAD_BODY_LIMIT)}),
    ("POST", "/api/patient/requests/CASE-1/documents", {}),          # the text upload: not this middleware's
    ("POST", "/api/patient/requests", {"Content-Length": str(UPLOAD_BODY_LIMIT + 1)}),
    ("GET", "/api/patient/requests/CASE-1/documents/file", {}),
    ("GET", "/api/patient/requests/CASE-1/reply/file", {}),
])
def test_the_middleware_passes_everything_else(method, path, headers):
    guard, tripwire = _guard()
    _run(guard, method, path, headers)
    assert tripwire.ran


def test_the_limit_is_ten_megabytes_plus_the_multipart_overhead():
    assert UPLOAD_BODY_LIMIT == 10 * 1024 * 1024 + 64 * 1024


def test_an_oversized_content_length_is_413_through_the_app_without_the_route_running(app_engine):
    intake = FakeIntake(accepted("ECG"))
    with api(app_engine, intake) as client:
        d = api_awaiting(client, app_engine)
        big = b"x" * (UPLOAD_BODY_LIMIT + 1)
        response = client.post(f"/api/patient/requests/{d.case_id}/documents/file", headers=token(client),
                               content=big)
        assert response.status_code == 413 and response.json() == {"detail": "too_large"}
    assert intake.calls == []


# --- the multipart body, parsed with the standard library (no python-multipart) ----------------------

def test_the_file_reaches_the_document_service_byte_for_byte(app_engine):
    binary = bytes(range(256)) * 64 + b"\r\n--not-a-boundary\r\n\r\n" + b"\n\r\x00" * 100
    intake = FakeIntake(accepted("ECG"))
    with api(app_engine, intake) as client:
        d = api_awaiting(client, app_engine)
        response = client.post(f"/api/patient/requests/{d.case_id}/documents/file", headers=token(client),
                               files={"file": ("בדיקה.pdf", binary, "application/pdf")},
                               data={"note": "ignored"})
        assert response.status_code == 200
    assert intake.calls == [(PATIENT, "בדיקה.pdf", binary)]


@pytest.mark.parametrize("kwargs", [
    {"json": {"file": "x"}},
    {"content": b"--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.pdf\"\r\n\r\nx",
     "headers_extra": {"Content-Type": "multipart/form-data; boundary=b"}},        # no closing delimiter
    {"files": {"other": ("a.pdf", b"x", "application/pdf")}},                     # no part named file
])
def test_a_body_that_is_not_one_file_part_is_422(app_engine, kwargs):
    intake = FakeIntake(accepted("ECG"))
    with api(app_engine, intake) as client:
        d = api_awaiting(client, app_engine)
        headers = {**token(client), **kwargs.pop("headers_extra", {})}
        response = client.post(f"/api/patient/requests/{d.case_id}/documents/file", headers=headers, **kwargs)
        assert response.status_code == 422 and response.json() == {"detail": "invalid_body"}
    assert intake.calls == []


# --- fix round 1: hostile multipart bodies and Content-Length values --------------------------------

def _raw_post(client, case_id, body, boundary="X"):
    return client.post(f"/api/patient/requests/{case_id}/documents/file", content=body,
                       headers={**token(client), "Content-Type": f"multipart/form-data; boundary={boundary}"})


def test_a_part_without_headers_is_422_not_500(app_engine):
    intake = FakeIntake(accepted("ECG"))
    with TestClient(create_app(app_engine, document_intake=intake), raise_server_exceptions=False) as client:
        d = api_awaiting(client, app_engine)
        for body in (b"--X\r\n\r\nDATA\r\n--X--", b"--X\r\nDATA\r\n--X--", b"--X\r\n\r\n\r\n--X--"):
            response = _raw_post(client, d.case_id, body)
            assert response.status_code == 422 and response.json() == {"detail": "invalid_body"}
    assert intake.calls == []


def test_a_body_of_thousands_of_tiny_parts_is_refused_quickly(app_engine):
    import time
    intake = FakeIntake(accepted("ECG"))
    part = b'--X\r\nContent-Disposition: form-data; name="n"\r\n\r\nv\r\n'
    body = part * 10_000 + b"--X--"
    with api(app_engine, intake) as client:
        d = api_awaiting(client, app_engine)
        started = time.monotonic()
        response = _raw_post(client, d.case_id, body)
        elapsed = time.monotonic() - started
    assert response.status_code == 422 and response.json() == {"detail": "invalid_body"}
    assert elapsed < 1.0
    assert intake.calls == []


def test_a_file_part_after_the_first_64_parts_is_not_looked_for(app_engine):
    filler = b"".join(b'--X\r\nContent-Disposition: form-data; name="n%d"\r\n\r\nv\r\n' % i for i in range(64))
    body = filler + b'--X\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n\r\nPDF\r\n--X--'
    intake = FakeIntake(accepted("ECG"))
    with api(app_engine, intake) as client:
        d = api_awaiting(client, app_engine)
        assert _raw_post(client, d.case_id, body).status_code == 422
    assert intake.calls == []


def test_the_parse_runs_off_the_event_loop(app_engine, monkeypatch):
    import asyncio
    from hospital_agent.api import routes_patient
    seen, real = [], routes_patient._file_part

    def spy(*args):
        try:
            asyncio.get_running_loop()
            seen.append("event loop")
        except RuntimeError:
            seen.append("worker thread")
        return real(*args)
    monkeypatch.setattr(routes_patient, "_file_part", spy)
    with api(app_engine, FakeIntake(accepted("ECG"))) as client:
        d = api_awaiting(client, app_engine)
        assert post_pdf(client, d.case_id).status_code == 200
    assert seen == ["worker thread"]


@pytest.mark.parametrize("value, status, code", [
    ("9" * 5000, 413, "too_large"),
    ("abc", 411, "length_required"),
    ("-5", 411, "length_required"),
    ("1e3", 411, "length_required"),
    ("", 411, "length_required"),
])
def test_an_absurd_content_length_never_500s(value, status, code):
    import json
    guard, tripwire = _guard()
    sent, received = _run(guard, "POST", "/api/patient/requests/CASE-1/documents/file", {"Content-Length": value})
    assert not tripwire.ran and received == []
    assert sent[0]["status"] == status and json.loads(sent[1]["body"]) == {"detail": code}


def test_a_non_201_answer_is_logged_with_its_status_code_only(sm, app_engine, caplog):
    d = awaiting(sm, app_engine)
    service = SessionService(sm, document_intake=FakeIntake(raises=IntakeUnavailable("status_400")))
    with caplog.at_level(logging.INFO, logger="hospital_agent.session"):
        with pytest.raises(IntakeUnavailable):
            service.upload_pdf(PATIENT, d.case_id, PDF, FILENAME)
    lines = [r.getMessage() for r in caplog.records if r.name == "hospital_agent.session"]
    assert "pdf upload: document_service_unavailable (status_400)" in lines
    assert PATIENT not in caplog.text and FILENAME not in caplog.text


def test_a_small_length_with_thousands_of_leading_zeros_passes():
    guard, tripwire = _guard()
    _run(guard, "POST", "/api/patient/requests/CASE-1/documents/file", {"Content-Length": "0" * 5000 + "12"})
    assert tripwire.ran


# --- final-fix wave: the text route refuses once the file route is configured ------------------

def test_the_text_route_refuses_once_the_file_route_is_configured(app_engine):
    """The text route would let anyone hand-craft an arbitrary "held" document, bypassing the
    document-service's intake entirely, once the case's screen already offers the real upload."""
    intake = FakeIntake(accepted("ECG"))
    with api(app_engine, intake) as client:
        d = api_awaiting(client, app_engine)
        response = client.post(f"/api/patient/requests/{d.case_id}/documents", headers=token(client),
                               json={"document_id": "CBC", "format": "pdf", "content": "normal"})
        assert response.status_code == 409 and response.json() == {"detail": "use_file_upload"}
    assert intake.calls == []  # nothing was even attempted through the document-service


def test_the_text_route_still_works_without_a_document_intake_client(app_engine):
    with api(app_engine, None) as client:
        d = api_awaiting(client, app_engine)  # missing "ECG" (held is only ["CBC"])
        response = client.post(f"/api/patient/requests/{d.case_id}/documents", headers=token(client),
                               json={"document_id": "ECG", "format": "pdf", "content": "normal"})
        assert response.status_code == 200
        assert response.json()["status"] == "in_progress"
