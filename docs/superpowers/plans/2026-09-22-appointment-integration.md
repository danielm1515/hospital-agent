# Sub-project 10: CheckAppointment against the appointment-service - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the owner's live stack, the agent's `CheckAppointment` asks the real appointment-service; everywhere else (tests, golden traces, the §0 scenarios) it stays on `MockGateway`.

**Architecture:** A composite `ToolGateway` (`AppointmentServiceGateway`) sends `CheckAppointment` over HTTP and hands the other three actions to `MockGateway`. `build_gateway()` picks it only when `APPOINTMENT_SERVICE_URL` is set, refuses to start the Orchestrator when the key is missing, and `api/app.py` uses it. The HTTP answers map onto the existing `ToolResult` kinds, so the Tool Executor and the Retry Manager are unchanged.

**Tech Stack:** Python 3.13, standard library `urllib.request` (no new runtime dependency), pytest; FastAPI/pydantic in the appointment-service.

**Spec:** `docs/superpowers/specs/2026-09-22-appointment-integration-design.md` (read it first; its §2 decisions are binding).

## Global Constraints

- No new runtime dependency in `backend/pyproject.toml` (httpx is dev-only; do not use it in `hospital_agent/`).
- `APPOINTMENT_API_KEY` and `APPOINTMENT_SERVICE_URL` are never logged, printed, put in a `ToolResult`, an exception message, a `repr`, an Audit row or `/health`.
- Only `patient_id` leaves the agent (spec §11 `minimized_fields`; `ACTION_TARGETS` is unchanged).
- Without `APPOINTMENT_SERVICE_URL`, behaviour is byte-for-byte today's: `MockGateway()`.
- The escalation, event and action lists are closed (§2.2, §5): no new names.
- Code, identifiers, comments, commit messages and repo docs in English.
- Never read, print or commit any `.env`. Never push. Never touch the owner's running stacks (ports 54322 / 8200 / 5273 / 8080).
- Run backend tests only in this worktree's isolated compose project, from the worktree root:
  `docker compose -p hospital-sp10 -f docker-compose.yml -f .superpowers/sdd/2026-09-22-appointment-integration/no-ports.yml run --rm backend pytest <args>`
  (the worktree has no `.env`, so it uses its own local `db` container).
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

### Task 1: The appointment-service gateway and its answer mapping

**Files:**
- Create: `backend/hospital_agent/execution/appointment_service.py`
- Modify: `backend/hospital_agent/execution/gateway.py:46-48` (`KNOWN_TOOL_ERRORS`)
- Test: `backend/tests/test_appointment_service.py`

**Interfaces:**
- Produces: `HttpResponse(status: int, body: bytes)`; `Transport = Callable[[str, Mapping[str, str], float], HttpResponse]`; `urllib_transport`; `map_response(response: HttpResponse) -> ToolResult`; `AppointmentServiceGateway(base_url: str, api_key: str, *, fallback: ToolGateway | None = None, transport: Transport = urllib_transport, timeout: float = TIMEOUT_SECONDS)` with `.call(action, parameters, idempotency_key) -> ToolResult`, `.idempotent(action) -> bool`, `.fallback`, `.calls: list[tuple[str, dict, str]]`; `TIMEOUT_SECONDS = 5`.

- [ ] **Step 1: Write the failing tests** - `backend/tests/test_appointment_service.py`:

```python
"""Sub-project 10: CheckAppointment against the owner's appointment-service (design §2)."""
import json
import threading
from datetime import UTC, datetime, timedelta, timezone
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
                                         "location": "Building B, Floor 2", "status": "Scheduled"}}


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
    return AppointmentServiceGateway("http://appointments.test/", KEY, transport=transport, **kwargs), transport


# --- the answer mapping, row by row (design §2.6) ------------------------------------------

def test_a_found_appointment_is_ok_with_an_aware_time():
    result = map_response(response(200, FOUND))
    assert result == ToolResult(OK, {"appointment_at": datetime(2026, 10, 3, 10, 30,
                                                                tzinfo=timezone(timedelta(hours=3)))})
    assert result.data["appointment_at"].utcoffset() == timedelta(hours=3)


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
])
def test_every_other_answer_is_an_error_with_a_known_code(answer, error):
    assert map_response(answer) == ToolResult(ERROR, {"error": error})
    assert error in KNOWN_TOOL_ERRORS


@pytest.mark.parametrize("status, error", [(500, "unavailable"), (502, "unavailable"),
                                           (503, "unavailable"), (504, "timeout")])
def test_a_server_side_failure_is_transient(status, error):
    assert map_response(response(status, {"error": "x"})) == ToolResult(TRANSIENT_FAILURE, {"error": error})


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
    gw = AppointmentServiceGateway(server, KEY)
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
```

- [ ] **Step 2: Run the tests to see them fail**

Run (worktree root): `docker compose -p hospital-sp10 -f docker-compose.yml -f .superpowers/sdd/2026-09-22-appointment-integration/no-ports.yml run --rm backend pytest tests/test_appointment_service.py -q`
Expected: collection error, `ModuleNotFoundError: hospital_agent.execution.appointment_service`.

- [ ] **Step 3: Extend the known error codes** - in `backend/hospital_agent/execution/gateway.py` replace

```python
KNOWN_TOOL_ERRORS = frozenset({"timeout", "rejected"})
```
with
```python
# timeout / rejected: the mocks. The rest: the appointment-service (sub-project 10, design §2.6).
KNOWN_TOOL_ERRORS = frozenset({"timeout", "rejected", "unavailable", "not_found", "patient_not_found",
                               "unauthorized", "invalid_response"})
```

- [ ] **Step 4: Write the gateway** - `backend/hospital_agent/execution/appointment_service.py`:

```python
"""CheckAppointment against the owner's appointment-service (sub-project 10, design §2).

Only CheckAppointment leaves over HTTP; the other three actions are MockGateway's, because no
real system exists for them. Every HTTP outcome becomes a ToolResult the Tool Executor and
the Retry Manager already understand, so nothing downstream changes: server-side failures
and no answer at all are transient (bounded retry, then RETRY_EXHAUSTED), and everything
else that is not a found appointment with a timezone-aware time is an error (escalation).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..naming import Action
from .gateway import ERROR, OK, TRANSIENT_FAILURE, MockGateway, ToolGateway, ToolResult

TIMEOUT_SECONDS = 5
_TRANSIENT = {500: "unavailable", 502: "unavailable", 503: "unavailable", 504: "timeout"}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


# (url, headers, timeout) -> the response; raises OSError when there is no answer at all.
Transport = Callable[[str, Mapping[str, str], float], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry X-API-Key to wherever Location points: never follow one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_transport(url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with _OPENER.open(request, timeout=timeout) as answer:
            return HttpResponse(answer.status, answer.read())
    except urllib.error.HTTPError as exc:  # a status the server did send is an answer, not a failure
        with exc:
            return HttpResponse(exc.code, exc.read())


def _error(code: str) -> ToolResult:
    return ToolResult(ERROR, {"error": code})


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None


def map_response(response: HttpResponse) -> ToolResult:
    """design §2.6, row by row."""
    if response.status in _TRANSIENT:
        return ToolResult(TRANSIENT_FAILURE, {"error": _TRANSIENT[response.status]})
    if response.status in (401, 403):
        return _error("unauthorized")
    body = _json(response.body)
    if response.status == 404:
        known = isinstance(body, dict) and body.get("error") == "patient_not_found"
        return _error("patient_not_found" if known else "invalid_response")
    if response.status != 200 or not isinstance(body, dict):
        return _error("invalid_response")
    found, appointment = body.get("found"), body.get("appointment")
    if found is False and appointment is None:
        return _error("not_found")
    if found is not True or not isinstance(appointment, dict) or not isinstance(appointment.get("appointment_at"), str):
        return _error("invalid_response")
    try:
        at = datetime.fromisoformat(appointment["appointment_at"])
    except ValueError:
        return _error("invalid_response")
    if at.tzinfo is None or at.utcoffset() is None:  # the F3 guard would refuse it anyway
        return _error("invalid_response")
    return ToolResult(OK, {"appointment_at": at})


class AppointmentServiceGateway:
    """ToolGateway: CheckAppointment over HTTP, every other action the fallback's."""

    def __init__(self, base_url: str, api_key: str, *, fallback: ToolGateway | None = None,
                 transport: Transport = urllib_transport, timeout: float = TIMEOUT_SECONDS) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport, self._timeout = transport, timeout
        self.fallback = fallback if fallback is not None else MockGateway()
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def __repr__(self) -> str:  # the URL and the key stay out of every log and traceback
        return "AppointmentServiceGateway()"

    def idempotent(self, action: str) -> bool:
        return self.fallback.idempotent(action)

    def call(self, action: str, parameters: Mapping[str, Any], idempotency_key: str) -> ToolResult:
        if action != Action.CHECK_APPOINTMENT.value:
            return self.fallback.call(action, parameters, idempotency_key)
        self.calls.append((action, dict(parameters), idempotency_key))
        patient_id = parameters.get("patient_id")
        if not isinstance(patient_id, str) or not patient_id:
            return _error("invalid_request")
        url = f"{self._base_url}/api/v1/patients/{urllib.parse.quote(patient_id, safe='')}/appointment"
        headers = {"X-API-Key": self._api_key, "X-Execution-ID": idempotency_key, "Accept": "application/json"}
        try:
            answer = self._transport(url, headers, self._timeout)
        except OSError as exc:
            timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
            return ToolResult(TRANSIENT_FAILURE, {"error": "timeout" if timed_out else "unavailable"})
        return map_response(answer)
```

Note: `"invalid_request"` is deliberately not in `KNOWN_TOOL_ERRORS` - the Audit records it as `other`; it cannot happen through the Executor, which always passes `patient_id`.

- [ ] **Step 5: Run the tests to see them pass**

Run: `... run --rm backend pytest tests/test_appointment_service.py tests/test_gateway.py tests/test_execution.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/hospital_agent/execution/appointment_service.py backend/hospital_agent/execution/gateway.py backend/tests/test_appointment_service.py
git commit -m "Add the appointment-service gateway for CheckAppointment

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Choosing the gateway, wiring it in, and the path through the real Executor

**Files:**
- Modify: `backend/hospital_agent/execution/appointment_service.py` (add `build_gateway`)
- Modify: `backend/hospital_agent/api/app.py:30,63-76,100-109`
- Modify: `docker-compose.yml` (backend service)
- Modify: `docs/api.md:92-100` (`GET /health`)
- Test: `backend/tests/test_appointment_service.py` (append), `backend/tests/test_appointment_service_e2e.py` (create)

**Interfaces:**
- Consumes: Task 1's `AppointmentServiceGateway`, `HttpResponse`, `MockGateway`.
- Produces: `build_gateway(env: Mapping[str, str] | None = None) -> tuple[ToolGateway | None, str]` - `(gateway, source)` where `source` is `"mock"` or `"appointment-service"`, or `(None, "disabled: ...")`.

- [ ] **Step 1: Write the failing tests** - append to `backend/tests/test_appointment_service.py`:

```python
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
```

Create `backend/tests/test_appointment_service_e2e.py`:

```python
"""Sub-project 10 through the real Tool Executor and State Manager (design §4)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from hospital_agent.execution.appointment_service import AppointmentServiceGateway, HttpResponse
from hospital_agent.naming import EscalationKind, State
from tests.driver import Driver

AT = "2026-10-03T10:30:00+03:00"


class ScriptedTransport:
    def __init__(self, *answers: HttpResponse):
        self.answers, self.urls = list(answers), []

    def __call__(self, url, headers, timeout):
        self.urls.append(url)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def answer(status, body):
    return HttpResponse(status, json.dumps(body).encode())


def driver(sm, app_engine, *answers):
    transport = ScriptedTransport(*answers)
    gw = AppointmentServiceGateway("http://appointments.test", "k", transport=transport)
    d = Driver(sm, app_engine, gateway=gw)
    d.to_classified()
    d.plan()
    return d, transport


def reasons(d):
    return [reason for row in d.trace() for reason in (row.policy_reasons or [])]


def test_a_found_appointment_is_the_one_the_case_keeps(sm, app_engine):
    d, transport = driver(sm, app_engine, answer(200, {"found": True, "appointment": {"appointment_at": AT}}))
    d.run_step()
    assert d.case.appointment_at == datetime(2026, 10, 3, 10, 30, tzinfo=timezone(timedelta(hours=3)))
    assert transport.urls == [f"http://appointments.test/api/v1/patients/{d.patient_id}/appointment"]
    assert d.state is State.PLANNING


@pytest.mark.parametrize("reply, reason", [
    (answer(200, {"found": False, "appointment": None}), "tool:error:not_found"),
    (answer(404, {"error": "patient_not_found"}), "tool:error:patient_not_found"),
    (answer(401, {"error": "unauthorized"}), "tool:error:unauthorized"),
    (answer(200, {"found": True, "appointment": {"appointment_at": "2026-10-03T10:30:00"}}),
     "tool:error:invalid_response"),
])
def test_an_answer_without_a_usable_appointment_goes_to_a_human(sm, app_engine, reply, reason):
    d, transport = driver(sm, app_engine, reply)
    d.run_step()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.NON_IDEMPOTENT_FAILURE)
    assert reason in reasons(d)
    assert len(transport.urls) == 1  # never retried
    assert d.case.appointment_at is None


def test_an_unavailable_service_is_retried_three_times_then_a_human_decides(sm, app_engine):
    d, transport = driver(sm, app_engine, answer(503, {"error": "patient_registry_unavailable"}))
    for _ in range(3):
        d.run_step()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.RETRY_EXHAUSTED)
    assert len(transport.urls) == 3
    assert reasons(d).count("tool:transient_failure:unavailable") >= 1


def test_a_service_that_recovers_within_the_budget_is_used(sm, app_engine):
    d, transport = driver(sm, app_engine, answer(504, {"error": "timeout"}),
                          answer(200, {"found": True, "appointment": {"appointment_at": AT}}))
    d.run_step()
    d.run_step()
    assert d.case.appointment_at is not None and d.state is State.PLANNING
    assert len(transport.urls) == 2
```

- [ ] **Step 2: Run to see them fail**

Run: `... run --rm backend pytest tests/test_appointment_service.py tests/test_appointment_service_e2e.py -q`
Expected: `ImportError: cannot import name 'build_gateway'`; the e2e file may already pass (it only uses Task 1) - if any e2e test fails, read the trace: the expected state after a successful `run_step()` on step 1 is `Planning` (see `tests/test_execution.py::test_a_call_writes_the_started_pair_then_its_outcome`). Adjust only an assertion that contradicts that test, never the production mapping.

- [ ] **Step 3: Add `build_gateway`** - append to `backend/hospital_agent/execution/appointment_service.py`:

```python
def build_gateway(env: Mapping[str, str] | None = None) -> tuple[ToolGateway | None, str]:
    """The gateway the live server uses (design §2.2-2.3): (gateway, "mock" | "appointment-service"),
    or (None, why the Agent Orchestrator must not start). Neither value ever holds the URL or the key."""
    import os

    env = os.environ if env is None else env
    url = env.get("APPOINTMENT_SERVICE_URL", "").strip()
    if not url:
        return MockGateway(), "mock"
    key = env.get("APPOINTMENT_API_KEY", "").strip()
    if not key:
        return None, "disabled: APPOINTMENT_API_KEY is not set"
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None, "disabled: APPOINTMENT_SERVICE_URL is not an http(s) URL"
    return AppointmentServiceGateway(url, key), "appointment-service"
```

(Move `import os` to the module's imports if the linter in the repo prefers; keep behaviour identical.)

- [ ] **Step 4: Wire it into the app** - in `backend/hospital_agent/api/app.py`:

Replace the import `from ..execution.gateway import MockGateway` with `from ..execution.appointment_service import build_gateway`.

Replace the block

```python
        app.state.orchestrator_status = None  # reported by /health only for a real server
```
with
```python
        app.state.orchestrator_status = None  # reported by /health only for a real server
        app.state.appointments_source = None  # likewise: "mock" or "appointment-service"
```

Replace

```python
            else:  # the demo's external systems are mocks (spec §18)
                started = app.state.orchestrator = Orchestrator(sm, provider, MockGateway())
                stops.append(started.run_in_background(orchestrator_interval_seconds()))
                app.state.orchestrator_status = "running"
```
with
```python
            else:
                # The demo's external systems are mocks (spec §18); on the owner's stack
                # CheckAppointment may ask the real appointment-service (sub-project 10).
                gateway, source = build_gateway()
                if gateway is None:
                    app.state.orchestrator_status = source
                else:
                    app.state.appointments_source = source
                    started = app.state.orchestrator = Orchestrator(sm, provider, gateway)
                    stops.append(started.run_in_background(orchestrator_interval_seconds()))
                    app.state.orchestrator_status = "running"
```

In `health()`, replace

```python
        extra = {} if orchestrator_status is None else {"orchestrator": orchestrator_status}
```
with
```python
        extra = {} if orchestrator_status is None else {"orchestrator": orchestrator_status}
        if request.app.state.appointments_source is not None:
            extra["appointments"] = request.app.state.appointments_source
```

- [ ] **Step 5: Compose** - in `docker-compose.yml`, in the `backend` service: add to `environment:` (next to `OPENAI_MODEL`)

```yaml
      # Sub-project 10: CheckAppointment asks the owner's appointment-service when both are set
      # (in .env); unset, it stays on the mock. Neither is ever logged.
      APPOINTMENT_SERVICE_URL: ${APPOINTMENT_SERVICE_URL:-}
      APPOINTMENT_API_KEY: ${APPOINTMENT_API_KEY:-}
```
and to the service (same indentation as `environment:`)

```yaml
    # host.docker.internal reaches the appointment-service published on the host's 8080
    # (Docker Desktop; on Linux this maps it to the bridge gateway).
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

- [ ] **Step 6: docs/api.md** - replace the `/health` paragraph

```
`orchestrator` appears only on a real server: `"running"`, or
`"disabled: OPENAI_API_KEY is not set"`. `503` with
```
with
```
`orchestrator` appears only on a real server: `"running"`, `"disabled: OPENAI_API_KEY is not set"`,
`"disabled: APPOINTMENT_API_KEY is not set"` or `"disabled: APPOINTMENT_SERVICE_URL is not an http(s) URL"`.
While it runs, `appointments` says where `CheckAppointment` goes: `"mock"`, or `"appointment-service"`
when `APPOINTMENT_SERVICE_URL` is set (sub-project 10) - the word only, never the URL. `503` with
```
and the example line to `{"status": "ok", "database": "ok", "orchestrator": "running", "appointments": "mock"}`.

- [ ] **Step 7: Run the whole backend suite**

Run: `... run --rm backend pytest -q`
Expected: all pass (the pre-existing skips/xfails only).

- [ ] **Step 8: Commit**

```bash
git add backend/hospital_agent/execution/appointment_service.py backend/hospital_agent/api/app.py docker-compose.yml docs/api.md backend/tests/test_appointment_service.py backend/tests/test_appointment_service_e2e.py
git commit -m "Choose the appointment-service gateway by configuration, and report it on /health

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The status message in Israel time

**Files:**
- Modify: `backend/hospital_agent/llm/message.py`
- Modify: `backend/tests/test_llm_components.py:174-179`

**Interfaces:** `status_message(case, source) -> str` unchanged in signature.

- [ ] **Step 1: Change the test first** - in `backend/tests/test_llm_components.py` replace the body of `test_status_message_uses_only_state_facts` expectation line `"התור שלך נקבע ל־23/09/2026 בשעה 08:30 (UTC). "` with `"התור שלך נקבע ל־23/09/2026 בשעה 11:30 (שעון ישראל). "` (the `_case()` time is 08:30 UTC on 23 September, which is 11:30 in Israel's summer time), and append:

```python
def test_status_message_shows_israel_time_in_winter_and_across_midnight():
    source = InstructionSource("INSTR-PREP-COLONOSCOPY", "3")
    winter = status_message(_case(appointment_at=datetime(2026, 12, 1, 8, 30, tzinfo=UTC)), source)
    assert "ל־01/12/2026 בשעה 10:30 (שעון ישראל)" in winter
    late = status_message(_case(appointment_at=datetime(2026, 12, 1, 23, 15, tzinfo=UTC)), source)
    assert "ל־02/12/2026 בשעה 01:15 (שעון ישראל)" in late
```

- [ ] **Step 2: Run to see it fail** - `... run --rm backend pytest tests/test_llm_components.py -q` → the two status-message tests fail.

- [ ] **Step 3: Implement** - in `backend/hospital_agent/llm/message.py`: add `from zoneinfo import ZoneInfo` to the imports, add after the imports

```python
# An appointment is kept in UTC (timestamptz) and is local to the hospital: the patient reads it
# in Israel time (sub-project 10, design §2.8).
CLINIC_TZ = ZoneInfo("Asia/Jerusalem")
```

change the template's first line to `"התור שלך נקבע ל־{date} בשעה {time} (שעון ישראל). "`, and in `status_message` compute `local = case.appointment_at.astimezone(CLINIC_TZ)` and format `date=local.strftime("%d/%m/%Y")`, `time=local.strftime("%H:%M")`. Update the module docstring's first sentence to say the time is shown in Israel time.

- [ ] **Step 4: Run** - `... run --rm backend pytest -q` → all pass; then `... run --rm backend python -m obs.golden` → still prints `35`, `4`, `54`. If `ZoneInfo("Asia/Jerusalem")` raises `ZoneInfoNotFoundError` in the container, stop and report BLOCKED (adding `tzdata` is a dependency change the controller decides).

- [ ] **Step 5: Commit**

```bash
git add backend/hospital_agent/llm/message.py backend/tests/test_llm_components.py
git commit -m "Show the appointment in Israel time in the status message

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The decisions, recorded

**Files:**
- Modify: `docs/spec_corrections.md` (append rows 72-75 after row 71, same table format)
- Modify: `CLAUDE.md` (Project status; Commands; Working in the backend; What is left; Verification targets' demo-stubs line)

- [ ] **Step 1: spec_corrections rows** - append, each a single table row `| N | question | decision |`:
  - **72** - §18 makes appointments a mock; the owner wants cases to see appointments booked in their appointment-service. Decision: `CheckAppointment` goes to it when `APPOINTMENT_SERVICE_URL` is set (live stack only); the other three actions and every test, golden trace and §0 scenario stay on `MockGateway`; URL without `APPOINTMENT_API_KEY` or a non-http(s) URL -> the Orchestrator does not start (fail closed). Design: `docs/superpowers/specs/2026-09-22-appointment-integration-design.md`.
  - **73** - How the service's answers map (design §2.6 table, summarised), and why `found=false` is an error: an `ok` without a time would reach readiness and escalate as `Z3Counterexample`, which `HUMAN_APPROVED` can resume into the same missing appointment; the escalation list is closed, so it is `NonIdempotentFailure` (resolve/reject only), reason `tool:error:not_found`.
  - **74** - The status template said `(UTC)`; a real appointment is local, so it shows `Asia/Jerusalem` time and says `(שעון ישראל)`; golden traces count rows, unchanged; content hashes change.
  - **75** - New codes in `KNOWN_TOOL_ERRORS` (`unavailable`, `not_found`, `patient_not_found`, `unauthorized`, `invalid_response`) so the Audit's `policy_reasons` keep a code, never the service's message (§12.3); a redirect is never followed (it would carry the key elsewhere).

- [ ] **Step 2: CLAUDE.md** - make these edits (keep the file's voice; English):
  - *Project status*: after the sub-project 9 sentence, add one sentence: sub-project 10 connects `CheckAppointment` to the appointment-service on the owner's stack only (`APPOINTMENT_SERVICE_URL` + `APPOINTMENT_API_KEY` in `.env`), everything else stays on the mocks (rows 72-75).
  - *Commands*: after the `READER_DB_PASSWORD` paragraph, add a paragraph: the two variables, that without the URL the mock is used, that the URL without the key (or a non-http URL) keeps the Orchestrator from starting and `/health` says why, that `/health` reports `appointments: mock | appointment-service`, and that from the container the service is `http://host.docker.internal:8080`.
  - *Working in the backend*: in the bullet that says the Tool Executor is the only code that calls an external system (`execution/gateway.py`), add `execution/appointment_service.py` (sub-project 10's `CheckAppointment` over HTTP; `build_gateway()` chooses it).
  - *Verification targets*, "Demo stubs" bullet: add that on the owner's stack `CheckAppointment` may be the real appointment-service (sub-project 10), the rest remain mocks.
  - *What is left*: add sub-project 10 next to sub-project 9's parenthesis as separate from the demo.

- [ ] **Step 3: Check nothing else broke** - `... run --rm backend pytest tests/test_fsm.py -q` (it reads `docs/spec/`, untouched) → pass.

- [ ] **Step 4: Commit**

```bash
git add docs/spec_corrections.md CLAUDE.md
git commit -m "Record sub-project 10's decisions

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The appointment-service returns a timezone (outside this repo)

**Files (the owner's project, no git - a backup is taken first):**
`C:/Users/danie/Documents/ChatGPT/לימודים פרויקט גמר/appointment-service/`
- Modify: `app/schemas.py` (`AppointmentOut`)
- Test: `tests/test_api.py` (append)
- Modify: `README.md` (the CheckAppointment section)

**Never** read or touch that project's `.env`; never restart its running container (the controller does that).

- [ ] **Step 0: Backup** - `cp -r app tests README.md ../appointment-service.backup-before-sp10/` (create the directory first).

- [ ] **Step 1: Failing test** - append to `tests/test_api.py`:

```python
def test_appointment_at_carries_israel_time_zone(tmp_path):
    """The Hospital Agent's guard refuses a naive time (HospitalAgent sub-project 10, design §2.7)."""
    _app, client = make_client(tmp_path)
    with client:
        body = client.get("/api/v1/patients/P-10041/appointment").json()
    assert body["appointment"]["appointment_at"] == "2026-10-03T10:30:00+03:00"


def test_a_winter_appointment_gets_the_winter_offset(tmp_path):
    from datetime import datetime
    from app.schemas import AppointmentOut
    out = AppointmentOut(appointment_id="A", patient_id="P", department="D", doctor_name=None,
                         appointment_at=datetime(2026, 12, 1, 9, 0), location=None, status="Scheduled")
    assert out.model_dump(mode="json")["appointment_at"] == "2026-12-01T09:00:00+02:00"
```

Run: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src" -w /src -e PIP_ROOT_USER_ACTION=ignore -e PIP_DISABLE_PIP_VERSION_CHECK=1 python:3.12-slim sh -c "pip install -q -r requirements-dev.txt && pytest -q -p no:cacheprovider"` from that project's root (Git Bash). Expected: the two new tests fail.

- [ ] **Step 2: Implement** - in `app/schemas.py`, add `from zoneinfo import ZoneInfo` and `from pydantic import field_validator`, and inside `AppointmentOut`:

```python
    @field_validator("appointment_at")
    @classmethod
    def _israel_time(cls, value: datetime) -> datetime:
        """SQLite keeps the Israel local time without an offset; say which zone it is."""
        return value.replace(tzinfo=ZoneInfo("Asia/Jerusalem")) if value.tzinfo is None else value
```

- [ ] **Step 3: Run** - all of that project's tests pass (they were 54 before).

- [ ] **Step 4: README** - in the "בדיקת תור קיים" section, add one sentence (Hebrew, like the file): `appointment_at` מוחזר תמיד עם אזור זמן (שעון ישראל, `+03:00` בקיץ ו-`+02:00` בחורף), כי Hospital Agent דוחה זמן בלי אזור זמן.

No commit (no git there); report the files changed and the test count.
