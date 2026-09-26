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

PATIENT = "P-10041"


def _map(body_response, *, requested_patient_id: str = PATIENT, requested_appointment_id: str | None = None):
    return map_response(body_response, now=NOW, requested_patient_id=requested_patient_id,
                        requested_appointment_id=requested_appointment_id)


def test_a_found_appointment_is_ok_with_an_aware_time():
    result = _map(response(200, FOUND))
    assert result == ToolResult(OK, {"appointment_at": datetime(2026, 10, 3, 10, 30,
                                                                tzinfo=timezone(timedelta(hours=3))),
                                     "required_documents": ["CBC", "COAGULATION_TESTS", "ECG"],
                                     "answered_appointment_id": "APT-1", "department": "Neurology"})
    assert result.data["appointment_at"].utcoffset() == timedelta(hours=3)


def test_a_found_appointment_carries_its_required_documents_sorted():
    assert _map(response(200, FOUND)).data["required_documents"] == ["CBC", "COAGULATION_TESTS", "ECG"]


def test_an_appointment_with_no_requirements_needs_nothing():
    body = {"found": True, "appointment": {**FOUND["appointment"], "required_documents": []}}
    assert _map(response(200, body)).data["required_documents"] == []


@pytest.mark.parametrize("value", [None, "CBC", [""], [1], ["CBC", None]])
def test_malformed_requirements_are_an_invalid_response(value):
    appointment = {**FOUND["appointment"], "required_documents": value}
    assert _map(response(200, {"found": True, "appointment": appointment})) == \
        ToolResult(ERROR, {"error": "invalid_response"})


def test_an_appointment_service_without_the_field_is_an_invalid_response():
    """An older appointment-service (before sub-project 11) cannot say what is required: fail closed."""
    appointment = {k: v for k, v in FOUND["appointment"].items() if k != "required_documents"}
    assert _map(response(200, {"found": True, "appointment": appointment})).data == {"error": "invalid_response"}


# --- sub-project 18: the patient-chosen appointment's new, optional facts (design §2, D3/D6) ----

FOUND_WITH_EXTRAS = {
    "found": True,
    "appointment": {**FOUND["appointment"], "appointment_id": "APT-1", "department": "Cardiology",
                    "exam_type": {"code": "CARD_STRESS", "label": "מבחן מאמץ"},
                    "instruction": {"source_id": "INSTR-CARD-STRESS", "version": "1", "title": "הכנה למבחן מאמץ"}},
    "upcoming_count": 3,
}


def test_the_new_fields_are_carried_when_present():
    data = _map(response(200, FOUND_WITH_EXTRAS), requested_appointment_id="APT-1").data
    assert data["answered_appointment_id"] == "APT-1"
    assert data["department"] == "Cardiology"
    assert data["exam_type_label"] == "מבחן מאמץ"
    assert data["instruction_source_id"] == "INSTR-CARD-STRESS"
    assert data["instruction_version"] == "1"
    assert data["upcoming_count"] == 3


def test_the_new_fields_are_absent_without_a_substitute_when_the_answer_omits_them():
    """design §2: every new field is optional - absent means None (no key at all), never a
    fallback value - so an older appointment-service still works exactly as before."""
    appointment = {k: v for k, v in FOUND["appointment"].items() if k not in ("appointment_id", "department")}
    data = _map(response(200, {"found": True, "appointment": appointment})).data
    for key in ("answered_appointment_id", "department", "exam_type_label", "instruction_source_id",
               "instruction_version", "upcoming_count"):
        assert key not in data


@pytest.mark.parametrize("appointment_patch, top_level", [
    ({"appointment_id": ""}, {}),
    ({"appointment_id": 1}, {}),
    ({"appointment_id": "APT 1"}, {}),         # M2: not the id pattern (a space)
    ({"appointment_id": ".APT-1"}, {}),        # M2: must start with a letter or digit
    ({"appointment_id": "a" * 65}, {}),        # M2: over 64 characters
    ({"department": ""}, {}),
    ({"department": "   "}, {}),                # m2: whitespace-only, same as empty
    ({"department": 1}, {}),
    ({"department": "ד" * 201}, {}),           # M2: department over 200 characters
    ({"exam_type": "CARD_STRESS"}, {}),          # not a dict
    ({"exam_type": {"code": "CARD_STRESS"}}, {}),  # no label
    ({"exam_type": {"label": ""}}, {}),           # empty label
    ({"exam_type": {"label": "\t\n"}}, {}),       # m2: whitespace-only, same as empty
    ({"exam_type": {"label": "מ" * 201}}, {}),  # M2: exam_type_label over 200 characters
    ({"instruction": "INSTR-CARD-STRESS"}, {}),   # not a dict
    ({"instruction": {"source_id": "INSTR-CARD-STRESS"}}, {}),  # no version
    ({"instruction": {"version": "1"}}, {}),      # no source_id
    ({"instruction": {"source_id": "", "version": "1"}}, {}),
    ({"instruction": {"source_id": "INSTR CARD", "version": "1"}}, {}),  # M2: source_id not the id pattern
    ({"instruction": {"source_id": "INSTR-CARD-STRESS", "version": "v.1.0!"}}, {}),  # M2: version not the id pattern
    ({}, {"upcoming_count": "3"}),
    ({}, {"upcoming_count": True}),
    ({}, {"upcoming_count": 1.5}),
    ({}, {"upcoming_count": -1}),               # M1: never negative
    ({}, {"upcoming_count": 2**31}),            # M1: over the bound
])
def test_a_present_but_malformed_new_field_is_invalid_response(appointment_patch, top_level):
    appointment = {**FOUND_WITH_EXTRAS["appointment"], **appointment_patch}
    body = {**FOUND_WITH_EXTRAS, "appointment": appointment, **top_level}
    assert _map(response(200, body)) == ToolResult(ERROR, {"error": "invalid_response"})


def test_upcoming_count_at_the_bounds_is_accepted():
    """M1: 0 and the maximum are both valid, only outside that range (or a bool) is refused."""
    for value in (0, 2**31 - 1):
        body = {**FOUND_WITH_EXTRAS, "upcoming_count": value}
        assert _map(response(200, body)).data["upcoming_count"] == value


def test_a_chosen_appointment_that_is_not_the_patients_is_not_found_with_no_substitute():
    """design D6: a chosen appointment that is not the patient's (the service answers
    found=false) takes the existing not_found path - never a fallback to some other
    appointment."""
    result = _map(response(200, {"found": False, "appointment": None, "upcoming_count": 2}),
                  requested_appointment_id="APT-8391")
    assert result == ToolResult(ERROR, {"error": "not_found"})


# --- I1 (fix round 1): the answer must be about who and what was actually asked -------------

def test_a_mismatched_answered_appointment_id_is_invalid_response():
    """A chosen appointment_id must come back exactly as answered_appointment_id - a different
    one is never trusted, even if it is otherwise a well-formed, Scheduled, future appointment."""
    result = _map(response(200, FOUND_WITH_EXTRAS), requested_appointment_id="APT-OTHER")
    assert result == ToolResult(ERROR, {"error": "invalid_response"})


def test_a_requested_appointment_id_that_the_answer_omits_is_invalid_response():
    appointment = {k: v for k, v in FOUND["appointment"].items() if k != "appointment_id"}
    result = _map(response(200, {"found": True, "appointment": appointment}), requested_appointment_id="APT-8391")
    assert result == ToolResult(ERROR, {"error": "invalid_response"})


def test_a_matching_answered_appointment_id_is_ok():
    result = _map(response(200, FOUND_WITH_EXTRAS), requested_appointment_id="APT-1")
    assert result.data["answered_appointment_id"] == "APT-1"


def test_no_appointment_id_was_requested_the_answers_is_still_carried():
    """Without a chosen appointment_id (the "nearest appointment" case), whatever the service
    answers is simply carried along - there is nothing to compare it against."""
    result = _map(response(200, FOUND_WITH_EXTRAS))
    assert result.data["answered_appointment_id"] == "APT-1"


def test_a_patient_id_mismatch_is_invalid_response():
    """Closes a pre-existing gap: the answer's own patient_id, when present, must name the
    patient CheckAppointment actually asked about - never silently trusted otherwise."""
    result = _map(response(200, FOUND), requested_patient_id="P-99999")
    assert result == ToolResult(ERROR, {"error": "invalid_response"})


def test_a_matching_patient_id_is_ok():
    assert _map(response(200, FOUND), requested_patient_id="P-10041").kind == OK


def test_an_answer_without_a_patient_id_is_still_ok():
    appointment = {k: v for k, v in FOUND["appointment"].items() if k != "patient_id"}
    result = _map(response(200, {"found": True, "appointment": appointment}), requested_patient_id="P-99999")
    assert result.kind == OK


def test_appointment_id_is_sent_as_a_query_parameter_when_present():
    gw, transport = gateway(response(200, FOUND))
    gw.call("CheckAppointment", {"patient_id": "P-10041", "appointment_id": "APT-1"}, "K-1")
    [(url, _, _)] = transport.requests
    assert url == "http://appointments.test/api/v1/patients/P-10041/appointment?appointment_id=APT-1"


def test_no_query_parameter_when_appointment_id_is_absent():
    gw, transport = gateway(response(200, FOUND))
    gw.call("CheckAppointment", {"patient_id": "P-10041"}, "K-1")
    [(url, _, _)] = transport.requests
    assert url == "http://appointments.test/api/v1/patients/P-10041/appointment"


def test_appointment_id_is_url_encoded_when_it_has_reserved_characters():
    """M5: a reserved character (here '/' and '&') must be percent-encoded, not left to be
    read as a path separator or a second query parameter."""
    gw, transport = gateway(response(200, FOUND))
    gw.call("CheckAppointment", {"patient_id": "P-10041", "appointment_id": "APT/8391&x"}, "K-1")
    [(url, _, _)] = transport.requests
    assert url == "http://appointments.test/api/v1/patients/P-10041/appointment?appointment_id=APT%2F8391%26x"


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
    assert _map(answer) == ToolResult(ERROR, {"error": error})
    assert error in KNOWN_TOOL_ERRORS


@pytest.mark.parametrize("status, error", [(500, "unavailable"), (502, "unavailable"),
                                           (503, "unavailable"), (504, "timeout")])
def test_a_server_side_failure_is_transient(status, error):
    assert _map(response(status, {"error": "x"})) == ToolResult(TRANSIENT_FAILURE, {"error": error})


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


def test_check_documents_and_send_status_update_are_the_mocks():
    """Task 4: LoadInstructions no longer falls back to the mock when the appointment-service
    is configured (design D8) - only CheckDocuments and SendStatusUpdate still do."""
    fallback = MockGateway()
    gw, transport = gateway(response(200, FOUND), fallback=fallback)
    assert gw.call("CheckDocuments", {"patient_id": "P"}, "K") == \
        MockGateway().call("CheckDocuments", {"patient_id": "P"}, "K")
    gw.call("SendStatusUpdate", {"patient_id": "P", "content_hash": "H"}, "K9")
    assert fallback.delivered == {"K9": {"patient_id": "P", "content_hash": "H"}}
    assert transport.requests == []
    assert all(gw.idempotent(a) == fallback.idempotent(a)
               for a in ("CheckAppointment", "CheckDocuments", "LoadInstructions", "SendStatusUpdate"))


# --- Task 4: LoadInstructions against the instruction system (design D8) --------------------

INSTRUCTION = {"source_id": "INSTR-CARD-STRESS", "version": "1", "title": "הכנה למבחן מאמץ",
              "text": "יש לצום כ-3 שעות לפני הבדיקה. " + "x" * 10}


def _load(answer=None, raises=None, **params):
    gw, transport = gateway(answer, raises)
    parameters = {"source_id": "INSTR-CARD-STRESS", "version": "1", **params}
    return gw.call("LoadInstructions", parameters, "K-1"), transport


def test_a_matching_instruction_answer_is_ok():
    result, transport = _load(response(200, INSTRUCTION))
    assert result == ToolResult(OK, {"instruction_ids": ["INSTR-CARD-STRESS:1"],
                                     "instruction_text": f"{INSTRUCTION['title']}\n{INSTRUCTION['text']}"})
    [(url, headers, timeout)] = transport.requests
    assert url == "http://appointments.test/api/v1/instructions/INSTR-CARD-STRESS?version=1"
    assert headers == {"X-API-Key": KEY, "X-Execution-ID": "K-1", "Accept": "application/json"}
    assert timeout == TIMEOUT_SECONDS


def test_a_mismatched_source_id_is_invalid_response():
    body = {**INSTRUCTION, "source_id": "INSTR-CARD-ECHO"}
    assert _load(response(200, body))[0] == ToolResult(ERROR, {"error": "invalid_response"})


def test_a_source_id_the_answer_omits_is_invalid_response():
    body = {k: v for k, v in INSTRUCTION.items() if k != "source_id"}
    assert _load(response(200, body))[0] == ToolResult(ERROR, {"error": "invalid_response"})


def test_a_mismatched_version_is_invalid_response():
    body = {**INSTRUCTION, "version": "2"}
    assert _load(response(200, body))[0] == ToolResult(ERROR, {"error": "invalid_response"})


@pytest.mark.parametrize("title", ["", "   ", 1, None, "x" * 201])
def test_a_malformed_title_is_invalid_response(title):
    body = {**INSTRUCTION, "title": title}
    assert _load(response(200, body))[0] == ToolResult(ERROR, {"error": "invalid_response"})


@pytest.mark.parametrize("text", ["", "   ", 1, None, "x" * 4001])
def test_a_malformed_text_is_invalid_response(text):
    body = {**INSTRUCTION, "text": text}
    assert _load(response(200, body))[0] == ToolResult(ERROR, {"error": "invalid_response"})


def test_titles_and_text_at_the_bound_are_accepted():
    body = {**INSTRUCTION, "title": "x" * 200, "text": "y" * 4000}
    assert _load(response(200, body))[0].kind == OK


def test_an_unknown_source_is_not_found():
    assert _load(response(404, {"error": "instruction_not_found"}))[0] == ToolResult(ERROR, {"error": "not_found"})


def test_an_unexpected_404_shape_is_invalid_response():
    assert _load(response(404, {"detail": "not found"}))[0] == ToolResult(ERROR, {"error": "invalid_response"})


@pytest.mark.parametrize("status, error", [(500, "unavailable"), (502, "unavailable"),
                                           (503, "unavailable"), (504, "timeout")])
def test_a_server_side_failure_is_transient_for_load_instructions(status, error):
    assert _load(response(status, {"error": "x"}))[0] == ToolResult(TRANSIENT_FAILURE, {"error": error})


@pytest.mark.parametrize("raised, error", [
    (TimeoutError("timed out"), "timeout"),
    (OSError("no route"), "unavailable"),
])
def test_no_answer_at_all_is_transient_for_load_instructions(raised, error):
    result, _ = _load(raises=raised)
    assert result == ToolResult(TRANSIENT_FAILURE, {"error": error})


def test_unauthorized_for_load_instructions():
    assert _load(response(401, {"error": "unauthorized"}))[0] == ToolResult(ERROR, {"error": "unauthorized"})


@pytest.mark.parametrize("params", [{"version": "1", "source_id": None}, {"source_id": "INSTR-CARD-STRESS",
                                                                          "version": None}])
def test_a_missing_source_id_or_version_is_never_sent(params):
    gw, transport = gateway(response(200, INSTRUCTION))
    result = gw.call("LoadInstructions", {k: v for k, v in params.items() if v is not None}, "K")
    assert result.data == {"error": "invalid_request"}
    assert transport.requests == []


def test_load_instructions_calls_are_recorded():
    gw, _ = gateway(response(200, INSTRUCTION))
    gw.call("LoadInstructions", {"source_id": "INSTR-CARD-STRESS", "version": "1"}, "K-1")
    assert gw.calls == [("LoadInstructions", {"source_id": "INSTR-CARD-STRESS", "version": "1"}, "K-1")]


def test_load_instructions_is_idempotent():
    gw, _ = gateway(response(200, INSTRUCTION))
    assert gw.idempotent("LoadInstructions")


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
    assert _map(HttpResponse(200, big)) == ToolResult(ERROR, {"error": "invalid_response"})


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


def test_build_gateway_falls_back_to_the_given_gateway():
    fallback = MockGateway()
    gw, source = build_gateway({}, fallback=fallback)
    assert gw is fallback and source == "mock"
    gw, _ = build_gateway({"APPOINTMENT_SERVICE_URL": "http://h:1", "APPOINTMENT_API_KEY": KEY}, fallback=fallback)
    assert gw.fallback is fallback
