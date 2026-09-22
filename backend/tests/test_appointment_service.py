"""Sub-project 10: CheckAppointment against the owner's appointment-service (design §2)."""
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hospital_agent.execution.appointment_service import (
    TIMEOUT_SECONDS, AppointmentServiceGateway, HttpResponse, map_response, urllib_transport,
)
from hospital_agent.execution.gateway import ERROR, KNOWN_TOOL_ERRORS, OK, TRANSIENT_FAILURE, MockGateway, ToolResult

KEY = "test-key-9f8e7d"
FOUND = {"found": True, "appointment": {"appointment_id": "APT-1", "patient_id": "P-10041",
                                         "department": "Neurology", "doctor_name": "Dr. Cohen",
                                         "appointment_at": "2026-10-03T10:30:00+03:00",
                                         "location": "Building B, Floor 2", "status": "Scheduled",
                                         "required_documents": ["COAGULATION_TESTS", "CBC", "ECG"]}}
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)  # before FOUND's appointment_at


def response(status: int, body) -> HttpResponse:
    return HttpResponse(status, body if isinstance(body, bytes) else json.dumps(body).encode())


class FakeTransport:
    def __init__(self, answer=None, raises: BaseException | None = None):
        self.answer, self.raises, self.requests = answer, raises, []

    def __call__(self, url, headers, timeout):
        self.requests.append((url, dict(headers), timeout))
        if self.raises is not None:
            raise self.raises
        return self.answer


def gateway(answer=None, raises=None, **kwargs):
    transport = FakeTransport(answer, raises)
    kwargs.setdefault("clock", lambda: NOW)
    return AppointmentServiceGateway("http://appointments.test/", KEY, transport=transport, **kwargs), transport


# --- the answer mapping, row by row (design §2.6) ------------------------------------------

def test_a_found_appointment_is_ok_with_an_aware_time():
    result = map_response(response(200, FOUND), now=NOW)
    assert result == ToolResult(OK, {"appointment_at": datetime(2026, 10, 3, 10, 30,
                                                                tzinfo=timezone(timedelta(hours=3))),
                                     "required_documents": ["CBC", "COAGULATION_TESTS", "ECG"]})
    assert result.data["appointment_at"].utcoffset() == timedelta(hours=3)


def test_a_found_appointment_carries_its_required_documents_sorted():
    assert map_response(response(200, FOUND), now=NOW).data["required_documents"] == ["CBC", "COAGULATION_TESTS", "ECG"]


def test_an_appointment_with_no_requirements_needs_nothing():
    body = {"found": True, "appointment": {**FOUND["appointment"], "required_documents": []}}
    assert map_response(response(200, body), now=NOW).data["required_documents"] == []


@pytest.mark.parametrize("value", [None, "CBC", [""], [1], ["CBC", None]])
def test_malformed_requirements_are_an_invalid_response(value):
    appointment = {**FOUND["appointment"], "required_documents": value}
    assert map_response(response(200, {"found": True, "appointment": appointment}), now=NOW) == \
        ToolResult(ERROR, {"error": "invalid_response"})


def test_an_appointment_service_without_the_field_is_an_invalid_response():
    """An older appointment-service (before sub-project 11) cannot say what is required: fail closed."""
    appointment = {k: v for k, v in FOUND["appointment"].items() if k != "required_documents"}
    assert map_response(response(200, {"found": True, "appointment": appointment}), now=NOW).data == {"error": "invalid_response"}


@pytest.mark.parametrize("answer, error", [
    (response(200, {"found": False, "appointment": None}), "not_found"),
    (response(404, {"error": "patient_not_found", "message": "x"}), "patient_not_found"),
    (response(404, {"detail": "Not Found"}), "invalid_response"),   # a wrong URL, not a patient
    (response(401, {"error": "unauthorized"}), "unauthorized"),
    (response(403, b"forbidden"), "unauthorized"),
    (response(400, {"error": "validation_error"}), "invalid_response"),
    (response(302, b""), "invalid_response"),
    (response(200, b"not json"), "invalid_response"),
    (response(200, [1, 2]), "invalid_response"),
    (response(200, {"found": "yes", "appointment": None}), "invalid_response"),
    (response(200, {"found": True, "appointment": None}), "invalid_response"),
    (response(200, {"found": True, "appointment": {"appointment_id": "APT-1"}}), "invalid_response"),
    (response(200, {"found": True, "appointment": {"appointment_at": "tomorrow"}}), "invalid_response"),
    (response(200, {"found": True, "appointment": {"appointment_at": "2026-10-03T10:30:00"}}), "invalid_response"),
    (response(200, {"found": False, "appointment": FOUND["appointment"]}), "invalid_response"),
    # design §2.6 / row 76: usable only if Scheduled and still in the future.
    (response(200, {"found": True, "appointment": {**FOUND["appointment"], "status": "Cancelled"}}), "not_found"),
    (response(200, {"found": True, "appointment": {k: v for k, v in FOUND["appointment"].items()
                                                    if k != "status"}}), "not_found"),
    (response(200, {"found": True, "appointment": {**FOUND["appointment"],
                                                    "appointment_at": "2026-09-22T14:59:00+03:00"}}), "not_found"),
    (response(200, {"found": True, "appointment": {**FOUND["appointment"],
                                                    "appointment_at": "2026-09-22T15:00:00+03:00"}}), "not_found"),
])
def test_every_other_answer_is_an_error_with_a_known_code(answer, error):
    assert map_response(answer, now=NOW) == ToolResult(ERROR, {"error": error})
    assert error in KNOWN_TOOL_ERRORS


@pytest.mark.parametrize("status, error", [(500, "unavailable"), (502, "unavailable"),
                                           (503, "unavailable"), (504, "timeout")])
def test_a_server_side_failure_is_transient(status, error):
    assert map_response(response(status, {"error": "x"}), now=NOW) == ToolResult(TRANSIENT_FAILURE, {"error": error})


@pytest.mark.parametrize("raised, error", [
    (TimeoutError("timed out"), "timeout"),
    (ConnectionRefusedError("refused"), "unavailable"),
    (OSError("no route"), "unavailable"),
])
def test_no_answer_at_all_is_transient(raised, error):
    gw, _ = gateway(raises=raised)
    assert gw.call("CheckAppointment", {"patient_id": "P-10041"}, "K-1") == ToolResult(TRANSIENT_FAILURE, {"error": error})


def test_a_urlerror_wrapping_a_timeout_is_a_timeout():
    import urllib.error
    gw, _ = gateway(raises=urllib.error.URLError(TimeoutError("timed out")))
    assert gw.call("CheckAppointment", {"patient_id": "P-10041"}, "K-1").data == {"error": "timeout"}


def test_a_malformed_http_answer_is_an_invalid_response():
    """The server did answer, but http.client could not parse it as HTTP - not the same as
    getting no answer at all (design §2, minor fix 5)."""
    import http.client
    gw, _ = gateway(raises=http.client.IncompleteRead(b""))
    assert gw.call("CheckAppointment", {"patient_id": "P-10041"}, "K-1") == ToolResult(ERROR, {"error": "invalid_response"})


# --- the request (design §2.5) -------------------------------------------------------------

def test_only_the_patient_id_leaves_quoted_in_the_path_with_the_key_and_the_execution_id():
    gw, transport = gateway(response(200, FOUND))
    gw.call("CheckAppointment", {"patient_id": "P 1/../x"}, "CASE-1:1:0:1")
    [(url, headers, timeout)] = transport.requests
    assert url == "http://appointments.test/api/v1/patients/P%201%2F..%2Fx/appointment"
    assert headers == {"X-API-Key": KEY, "X-Execution-ID": "CASE-1:1:0:1", "Accept": "application/json"}
    assert timeout == TIMEOUT_SECONDS == 5


def test_a_missing_patient_id_is_never_sent():
    gw, transport = gateway(response(200, FOUND))
    assert gw.call("CheckAppointment", {}, "K").kind == ERROR
    assert transport.requests == []


def test_the_other_actions_are_the_mocks():
    fallback = MockGateway()
    gw, transport = gateway(response(200, FOUND), fallback=fallback)
    for action in ("CheckDocuments", "LoadInstructions"):
        assert gw.call(action, {"patient_id": "P"}, "K") == MockGateway().call(action, {"patient_id": "P"}, "K")
    gw.call("SendStatusUpdate", {"patient_id": "P", "content_hash": "H"}, "K9")
    assert fallback.delivered == {"K9": {"patient_id": "P", "content_hash": "H"}}
    assert transport.requests == []
    assert all(gw.idempotent(a) == fallback.idempotent(a)
               for a in ("CheckAppointment", "CheckDocuments", "LoadInstructions", "SendStatusUpdate"))


def test_calls_are_recorded_like_the_mocks():
    gw, _ = gateway(response(200, FOUND))
    gw.call("CheckAppointment", {"patient_id": "P-10041"}, "K-1")
    assert gw.calls == [("CheckAppointment", {"patient_id": "P-10041"}, "K-1")]


def test_the_key_never_shows():
    gw, _ = gateway(raises=OSError(f"boom {KEY}"))
    result = gw.call("CheckAppointment", {"patient_id": "P-10041"}, "K")
    assert KEY not in repr(result) and KEY not in repr(gw) and KEY not in str(gw)
    assert "appointments.test" not in repr(gw)


# --- the real urllib path, over a local server -------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    seen: list = []
    status, body, location = 200, json.dumps(FOUND).encode(), None

    def do_GET(self):
        type(self).seen.append((self.path, self.headers.get("X-API-Key")))
        self.send_response(type(self).status)
        if type(self).location:
            self.send_header("Location", type(self).location)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *args):  # keep the test output clean
        pass


@pytest.fixture
def server():
    _Handler.seen, _Handler.status, _Handler.body, _Handler.location = [], 200, json.dumps(FOUND).encode(), None
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_urllib_transport_over_a_real_server(server):
    gw = AppointmentServiceGateway(server, KEY, clock=lambda: NOW)
    result = gw.call("CheckAppointment", {"patient_id": "P-10041"}, "K-1")
    assert result.kind == OK
    assert _Handler.seen == [("/api/v1/patients/P-10041/appointment", KEY)]


def test_urllib_transport_returns_an_error_status_instead_of_raising(server):
    _Handler.status, _Handler.body = 404, json.dumps({"error": "patient_not_found"}).encode()
    assert urllib_transport(server + "/x", {}, 5).status == 404


def test_a_redirect_is_never_followed(server):
    """Following it would send the API key to wherever the Location points."""
    _Handler.status, _Handler.location = 302, server + "/elsewhere"
    gw = AppointmentServiceGateway(server, KEY)
    assert gw.call("CheckAppointment", {"patient_id": "P-10041"}, "K").data == {"error": "invalid_response"}
    assert [path for path, _ in _Handler.seen] == ["/api/v1/patients/P-10041/appointment"]


def test_a_refused_connection_is_transient():
    gw = AppointmentServiceGateway("http://127.0.0.1:1", KEY)
    assert gw.call("CheckAppointment", {"patient_id": "P-10041"}, "K") == ToolResult(TRANSIENT_FAILURE, {"error": "unavailable"})


def test_no_proxy_is_used_even_when_one_is_configured(server, monkeypatch):
    """A proxy from HTTP_PROXY would see X-API-Key and patient_id (design §2, minor fix 3)."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    gw = AppointmentServiceGateway(server, KEY, clock=lambda: NOW)
    result = gw.call("CheckAppointment", {"patient_id": "P-10041"}, "K-1")
    assert result.kind == OK


def test_a_body_over_64kib_is_invalid_response_through_map_response():
    big = json.dumps({"found": True, "appointment": FOUND["appointment"], "pad": "x" * (70 * 1024)}).encode()
    assert len(big) > 64 * 1024
    assert map_response(HttpResponse(200, big), now=NOW) == ToolResult(ERROR, {"error": "invalid_response"})


def test_a_body_over_64kib_is_invalid_response_through_the_local_server(server):
    _Handler.status, _Handler.body = 200, b"{" + b"x" * (70 * 1024)
    gw = AppointmentServiceGateway(server, KEY, clock=lambda: NOW)
    result = gw.call("CheckAppointment", {"patient_id": "P-10041"}, "K-1")
    assert result == ToolResult(ERROR, {"error": "invalid_response"})


# --- choosing the gateway (design §2.2, §2.3) --------------------------------------------

from hospital_agent.execution.appointment_service import build_gateway  # noqa: E402


@pytest.mark.parametrize("env", [{}, {"APPOINTMENT_SERVICE_URL": ""}, {"APPOINTMENT_SERVICE_URL": "  "},
                                 {"APPOINTMENT_API_KEY": KEY}])
def test_without_a_url_it_is_the_mock_exactly_as_before(env):
    gw, source = build_gateway(env)
    assert type(gw) is MockGateway and source == "mock"


@pytest.mark.parametrize("key", ["", "   "])
def test_a_url_without_a_key_refuses_to_start(key):
    gw, status = build_gateway({"APPOINTMENT_SERVICE_URL": "http://h:8080", "APPOINTMENT_API_KEY": key})
    assert gw is None and status == "disabled: APPOINTMENT_API_KEY is not set"


@pytest.mark.parametrize("url", ["host.docker.internal:8080", "ftp://h/x", "http://", "file:///etc/passwd"])
def test_a_url_that_is_not_http_refuses_to_start(url):
    gw, status = build_gateway({"APPOINTMENT_SERVICE_URL": url, "APPOINTMENT_API_KEY": KEY})
    assert gw is None and status == "disabled: APPOINTMENT_SERVICE_URL is not an http(s) URL"
    assert url not in status


def test_a_url_and_a_key_give_the_appointment_service():
    gw, source = build_gateway({"APPOINTMENT_SERVICE_URL": " http://host.docker.internal:8080 ",
                                "APPOINTMENT_API_KEY": f" {KEY} "})
    assert isinstance(gw, AppointmentServiceGateway) and source == "appointment-service"
    assert type(gw.fallback) is MockGateway


def test_build_gateway_reads_the_environment_by_default(monkeypatch):
    monkeypatch.delenv("APPOINTMENT_SERVICE_URL", raising=False)
    assert build_gateway()[1] == "mock"
    monkeypatch.setenv("APPOINTMENT_SERVICE_URL", "http://h:1")
    monkeypatch.setenv("APPOINTMENT_API_KEY", KEY)
    assert build_gateway()[1] == "appointment-service"
