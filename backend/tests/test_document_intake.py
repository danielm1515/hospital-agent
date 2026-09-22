"""Sub-project 13: the Session Service's client for the document-service's upload (design §5.3)."""
import http.client
import json
import re
import socket

import pytest

from hospital_agent.document_intake import (
    DocumentIntakeClient, IntakeAnswer, IntakeUnavailable, build_intake_client,
)
from hospital_agent.execution.http import HttpResponse

KEY = "intake-key-9f8e"
BASE = "http://documents.test:8090"
PDF = b"%PDF-1.7\n...binary...\n%%EOF"


class Transport:
    def __init__(self, answer=None, raises=None):
        self.answer, self.raises, self.requests = answer, raises, []

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append((method, url, dict(headers), body, timeout))
        if self.raises:
            raise self.raises
        return self.answer


def created(**body):
    return HttpResponse(201, json.dumps(body).encode())


ACCEPTED = {"document_id": "DOC-3F2A1B9C0D4E", "document_type": "CBC", "document_date": "2026-09-15",
            "result": "ACCEPTED"}


def client(transport):
    return DocumentIntakeClient(BASE + "/", KEY, transport=transport)


def test_the_upload_is_one_multipart_part_named_file_with_the_key_and_the_quoted_patient():
    transport = Transport(created(**ACCEPTED))
    client(transport).submit("P 10041/x", "cbc.pdf", PDF)
    [(method, url, headers, body, timeout)] = transport.requests
    assert method == "POST"
    assert url == "http://documents.test:8090/api/v1/patients/P%2010041%2Fx/documents"
    assert headers["X-API-Key"] == KEY
    assert headers["Accept"] == "application/json"
    match = re.fullmatch(r"multipart/form-data; boundary=([0-9a-f]{32})", headers["Content-Type"])
    assert match
    boundary = match.group(1).encode()
    assert body == (b"--" + boundary + b"\r\n"
                    b'Content-Disposition: form-data; name="file"; filename="cbc.pdf"\r\n'
                    b"Content-Type: application/pdf\r\n\r\n" + PDF + b"\r\n--" + boundary + b"--\r\n")
    assert timeout == 75.0


def test_the_timeout_is_above_the_document_services_documented_worst_case():
    assert DocumentIntakeClient(BASE, KEY).timeout == 75.0 > 70


@pytest.mark.parametrize("filename, sent", [
    ('a"b\r\nX-Injected: 1.pdf', "abX-Injected: 1.pdf"),
    ("בדיקת דם.pdf", "document.pdf"),
    ("", "document.pdf"),
    ("x" * 300, "x" * 100),
])
def test_the_file_name_cannot_break_the_part_header(filename, sent):
    transport = Transport(created(**ACCEPTED))
    client(transport).submit("P-10041", filename, PDF)
    body = transport.requests[0][3]
    header = body.split(b"\r\n\r\n", 1)[0]
    assert header.count(b"\r\n") == 2  # boundary line, Content-Disposition, Content-Type - nothing injected
    assert f'filename="{sent}"'.encode() in header


def test_a_boundary_that_occurs_in_the_file_is_never_used(monkeypatch):
    from hospital_agent import document_intake
    boundaries = iter(["a" * 32, "b" * 32])
    monkeypatch.setattr(document_intake, "_boundary", lambda: next(boundaries))
    transport = Transport(created(**ACCEPTED))
    client(transport).submit("P-10041", "f.pdf", b"xx" + b"a" * 32 + b"yy")
    assert transport.requests[0][2]["Content-Type"].endswith("boundary=" + "b" * 32)


def test_an_accepted_answer():
    answer = client(Transport(created(**ACCEPTED))).submit("P-10041", "cbc.pdf", PDF)
    assert answer == IntakeAnswer(result="ACCEPTED", document_id="DOC-3F2A1B9C0D4E", document_type="CBC",
                                  duplicate_of=None)


def test_a_duplicate_answer_carries_the_original():
    answer = client(Transport(created(document_id="DOC-9B1C2D3E4F5A", document_type="CBC",
                                      document_date="2026-09-15", result="DUPLICATE_DOCUMENT",
                                      duplicate_of="DOC-3F2A1B9C0D4E"))).submit("P-10041", "cbc.pdf", PDF)
    assert answer == IntakeAnswer("DUPLICATE_DOCUMENT", "DOC-9B1C2D3E4F5A", "CBC", "DOC-3F2A1B9C0D4E")


def test_a_rejection_without_a_type():
    answer = client(Transport(created(document_id="DOC-1", document_type=None, document_date=None,
                                      result="NON_MEDICAL_DOCUMENT"))).submit("P-10041", "x.pdf", PDF)
    assert answer == IntakeAnswer("NON_MEDICAL_DOCUMENT", "DOC-1", None, None)


@pytest.mark.parametrize("exc", [OSError("down"), socket.timeout("slow"), TimeoutError(),
                                 http.client.RemoteDisconnected("gone"), http.client.BadStatusLine("x")])
def test_no_answer_is_unavailable(exc):
    with pytest.raises(IntakeUnavailable):
        client(Transport(raises=exc)).submit("P-10041", "x.pdf", PDF)


@pytest.mark.parametrize("answer", [
    HttpResponse(500, b'{"error": "x"}'), HttpResponse(502, b""), HttpResponse(503, b'{"error": "storage_unavailable"}'),
    HttpResponse(504, b""), HttpResponse(401, b'{"error": "unauthorized"}'), HttpResponse(403, b""),
    HttpResponse(400, b'{"error": "validation_error"}'), HttpResponse(413, b'{"error": "too_large"}'),
    HttpResponse(200, json.dumps(ACCEPTED).encode()), HttpResponse(302, b""),
    HttpResponse(201, b"not json"), HttpResponse(201, b"[]"),
    created(document_id="DOC-1", document_type="CBC"),                       # no result
    created(**{**ACCEPTED, "result": 5}), created(**{**ACCEPTED, "result": ""}),
    created(**{**ACCEPTED, "document_id": None}), created(**{**ACCEPTED, "document_id": "DOC 1"}),
    created(**{**ACCEPTED, "document_type": 7}), created(**{**ACCEPTED, "document_type": ""}),
    created(**{**ACCEPTED, "document_type": "C B C"}),
    created(**{**ACCEPTED, "result": "DUPLICATE_DOCUMENT", "duplicate_of": 3}),
    created(**{**ACCEPTED, "result": "DUPLICATE_DOCUMENT", "duplicate_of": "../x"}),
])
def test_anything_but_the_contract_is_unavailable(answer):
    with pytest.raises(IntakeUnavailable):
        client(Transport(answer)).submit("P-10041", "x.pdf", PDF)


def test_the_key_and_the_url_never_leave_the_client():
    transport = Transport(raises=OSError(f"cannot reach {BASE} with {KEY}"))
    intake = client(transport)
    assert KEY not in repr(intake) and "documents.test" not in repr(intake)
    with pytest.raises(IntakeUnavailable) as raised:
        intake.submit("P-10041", "x.pdf", PDF)
    assert KEY not in str(raised.value) and "documents.test" not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


# --- configuration ------------------------------------------------------------------------------

@pytest.mark.parametrize("env", [
    {}, {"DOCUMENT_SERVICE_URL": ""}, {"DOCUMENT_SERVICE_URL": BASE},
    {"DOCUMENT_SERVICE_URL": BASE, "DOCUMENT_API_KEY": "  "},
    {"DOCUMENT_SERVICE_URL": "ftp://documents.test", "DOCUMENT_API_KEY": KEY},
    {"DOCUMENT_SERVICE_URL": "documents.test:8090", "DOCUMENT_API_KEY": KEY},
    {"DOCUMENT_API_KEY": KEY},
])
def test_no_client_without_a_usable_configuration(env):
    assert build_intake_client(env) is None


def test_a_client_from_the_same_two_variables_as_the_gateway():
    intake = build_intake_client({"DOCUMENT_SERVICE_URL": BASE, "DOCUMENT_API_KEY": KEY})
    assert isinstance(intake, DocumentIntakeClient)
    assert intake.timeout == 75.0
