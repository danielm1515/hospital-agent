"""Sub-project 13: CheckDocuments against the owner's document-service (design §5.2)."""
import json

import pytest

from hospital_agent.execution.document_service import (
    DocumentServiceGateway, build_document_gateway, map_documents,
)
from hospital_agent.execution.gateway import ERROR, OK, TRANSIENT_FAILURE, MockGateway, ToolResult
from hospital_agent.execution.http import HttpResponse

KEY = "doc-key-1a2b"


def listing(*items):
    return HttpResponse(200, json.dumps({"documents": list(items)}).encode())


def item(doc_type, result="ACCEPTED", doc_id="DOC-1"):
    return {"document_id": doc_id, "document_type": doc_type, "document_date": "2026-09-15",
            "result": result, "valid_until": "2026-12-14", "uploaded_at": "2026-09-22T10:00:00+00:00"}


def test_held_documents_are_the_types_of_the_accepted_ones_sorted_and_unique():
    answer = listing(item("ECG"), item("CBC"), item("CBC", doc_id="DOC-2"), item("URINALYSIS", "DOCUMENT_EXPIRED"),
                     item(None, "NON_MEDICAL_DOCUMENT"), item("CBC", "DUPLICATE_DOCUMENT"))
    assert map_documents(answer) == ToolResult(OK, {"held_documents": ["CBC", "ECG"]})


def test_no_documents_holds_nothing():
    assert map_documents(listing()) == ToolResult(OK, {"held_documents": []})


@pytest.mark.parametrize("answer", [
    HttpResponse(200, b"not json"), HttpResponse(200, b"[]"), HttpResponse(200, b'{"documents": {}}'),
    listing({"document_type": "CBC"}), listing({"result": "ACCEPTED", "document_type": 5}),
    listing({"result": "ACCEPTED", "document_type": ""}), HttpResponse(404, b'{"error": "not_found"}'),
    HttpResponse(302, b""), HttpResponse(400, b'{"error": "validation_error"}'),
])
def test_anything_else_is_an_invalid_response(answer):
    assert map_documents(answer) == ToolResult(ERROR, {"error": "invalid_response"})


@pytest.mark.parametrize("status, error", [(500, "unavailable"), (502, "unavailable"), (503, "unavailable"), (504, "timeout")])
def test_a_server_side_failure_is_transient(status, error):
    assert map_documents(HttpResponse(status, b'{"error": "x"}')) == ToolResult(TRANSIENT_FAILURE, {"error": error})


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_is_unauthorized(status):
    assert map_documents(HttpResponse(status, b'{"error": "unauthorized"}')) == ToolResult(ERROR, {"error": "unauthorized"})


class Transport:
    def __init__(self, answer=None, raises=None):
        self.answer, self.raises, self.requests = answer, raises, []

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append((method, url, dict(headers), body, timeout))
        if self.raises:
            raise self.raises
        return self.answer


def test_only_the_patient_id_leaves_quoted_with_the_key():
    transport = Transport(listing())
    gw = DocumentServiceGateway("http://docs.test/", KEY, transport=transport)
    gw.call("CheckDocuments", {"patient_id": "P 1/x"}, "CASE:2:0:1")
    [(method, url, headers, body, timeout)] = transport.requests
    assert (method, url, body) == ("GET", "http://docs.test/api/v1/patients/P%201%2Fx/documents", None)
    assert headers == {"X-API-Key": KEY, "Accept": "application/json"}
    assert timeout == 10.0


def test_the_other_actions_are_the_fallbacks():
    fallback = MockGateway()
    gw = DocumentServiceGateway("http://docs.test", KEY, fallback=fallback, transport=Transport(listing()))
    assert gw.call("LoadInstructions", {}, "k") == MockGateway().call("LoadInstructions", {}, "k")
    assert gw.idempotent("CheckDocuments") == fallback.idempotent("CheckDocuments")


@pytest.mark.parametrize("raised, expected", [
    (TimeoutError(), ToolResult(TRANSIENT_FAILURE, {"error": "timeout"})),
    (ConnectionRefusedError(), ToolResult(TRANSIENT_FAILURE, {"error": "unavailable"})),
])
def test_no_answer_is_transient(raised, expected):
    gw = DocumentServiceGateway("http://docs.test", KEY, transport=Transport(raises=raised))
    assert gw.call("CheckDocuments", {"patient_id": "P-1"}, "k") == expected


def test_a_missing_patient_id_is_never_sent():
    transport = Transport(listing())
    assert DocumentServiceGateway("http://docs.test", KEY, transport=transport).call("CheckDocuments", {}, "k").kind == ERROR
    assert transport.requests == []


def test_the_key_and_url_never_show():
    gw = DocumentServiceGateway("http://docs.test", KEY, transport=Transport(raises=OSError(KEY)))
    result = gw.call("CheckDocuments", {"patient_id": "P-1"}, "k")
    assert KEY not in repr(result) and KEY not in repr(gw) and "docs.test" not in repr(gw)


@pytest.mark.parametrize("env", [{}, {"DOCUMENT_SERVICE_URL": " "}, {"DOCUMENT_API_KEY": KEY}])
def test_without_a_url_it_is_the_fallback(env):
    fallback = MockGateway()
    gw, source = build_document_gateway(env, fallback=fallback)
    assert gw is fallback and source == "mock"


def test_a_url_without_a_key_refuses():
    assert build_document_gateway({"DOCUMENT_SERVICE_URL": "http://h:8090"}) == (None, "disabled: DOCUMENT_API_KEY is not set")


@pytest.mark.parametrize("url", ["h:8090", "ftp://h", "http://"])
def test_a_non_http_url_refuses(url):
    gw, status = build_document_gateway({"DOCUMENT_SERVICE_URL": url, "DOCUMENT_API_KEY": KEY})
    assert gw is None and status == "disabled: DOCUMENT_SERVICE_URL is not an http(s) URL"


def test_a_url_and_a_key_give_the_document_service():
    gw, source = build_document_gateway({"DOCUMENT_SERVICE_URL": "http://h:8090", "DOCUMENT_API_KEY": KEY})
    assert isinstance(gw, DocumentServiceGateway) and source == "document-service"
