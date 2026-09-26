"""CheckAppointment against the owner's appointment-service (sub-project 10, design §2; sub-project
13 design §5.1 moves `required_documents` here too - the appointment system owns them).

Only CheckAppointment leaves over HTTP; the other three actions are MockGateway's, because no
real system exists for them. Every HTTP outcome becomes a ToolResult the Tool Executor and
the Retry Manager already understand, so nothing downstream changes: server-side failures
and no answer at all are transient (bounded retry, then RETRY_EXHAUSTED), and everything
else that is not a found appointment with a timezone-aware time is an error (escalation).
The HTTP transport itself lives in `.http` (sub-project 13 task 1), shared with the
document-service gateway.
"""
from __future__ import annotations

import http.client as http_client
import json
import os
import urllib.parse
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from ..naming import Action
from . import http
from .gateway import ERROR, OK, TRANSIENT_FAILURE, MockGateway, ToolGateway, ToolResult
from .http import HttpResponse, MAX_BODY_BYTES, no_answer_result

TIMEOUT_SECONDS = 5
_TRANSIENT = {500: "unavailable", 502: "unavailable", 503: "unavailable", 504: "timeout"}

# (url, headers, timeout) -> the response; raises OSError when there is no answer at all.
Transport = Callable[[str, Mapping[str, str], float], HttpResponse]


def urllib_transport(url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
    return http.request("GET", url, headers, None, timeout)


def _error(code: str) -> ToolResult:
    return ToolResult(ERROR, {"error": code})


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None


def _optional_str(value: Any) -> tuple[bool, str | None]:
    """(ok, value): ok is False only for a present field with the wrong shape (sub-project 18
    design §2 - every new field is optional; absent means None, a non-empty string is kept, and
    anything else - "", a number, a list - is invalid_response, fail closed)."""
    if value is None:
        return True, None
    if isinstance(value, str) and value:
        return True, value
    return False, None


def _optional_int(value: Any) -> tuple[bool, int | None]:
    if value is None:
        return True, None
    if isinstance(value, int) and not isinstance(value, bool):
        return True, value
    return False, None


def map_response(response: HttpResponse, *, now: datetime) -> ToolResult:
    """design §2.6, row by row."""
    if response.status in _TRANSIENT:
        return ToolResult(TRANSIENT_FAILURE, {"error": _TRANSIENT[response.status]})
    if response.status in (401, 403):
        return _error("unauthorized")
    if len(response.body) >= MAX_BODY_BYTES:  # minor fix 4: too long to be the contract either way
        return _error("invalid_response")
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
    # spec_corrections row 76: usable only if still Scheduled and still in the future - a
    # cancelled or past appointment must never be confirmed to the patient. A missing status
    # is not "Scheduled" (strict): fail closed rather than assume the field was just omitted.
    if appointment.get("status") != "Scheduled" or at <= now:
        return _error("not_found")
    required = _required_documents(appointment.get("required_documents"))
    if required is None:
        return _error("invalid_response")
    # Sub-project 18 (design §2, D3/D6): every field below is optional (an older service simply
    # omits it - absent means None, never a substitute value) - but a *present* field with an
    # invalid shape is invalid_response, fail closed.
    ok, appointment_id = _optional_str(appointment.get("appointment_id"))
    if not ok:
        return _error("invalid_response")
    ok, department = _optional_str(appointment.get("department"))
    if not ok:
        return _error("invalid_response")
    exam_type_label = None
    exam_type = appointment.get("exam_type")
    if exam_type is not None:
        if not isinstance(exam_type, dict):
            return _error("invalid_response")
        ok, exam_type_label = _optional_str(exam_type.get("label"))
        if not ok or exam_type_label is None:
            return _error("invalid_response")
    instruction_source_id = instruction_version = None
    instruction = appointment.get("instruction")
    if instruction is not None:
        if not isinstance(instruction, dict):
            return _error("invalid_response")
        ok, instruction_source_id = _optional_str(instruction.get("source_id"))
        if not ok or instruction_source_id is None:
            return _error("invalid_response")
        ok, instruction_version = _optional_str(instruction.get("version"))
        if not ok or instruction_version is None:
            return _error("invalid_response")
    ok, upcoming_count = _optional_int(body.get("upcoming_count"))
    if not ok:
        return _error("invalid_response")
    data: dict[str, Any] = {"appointment_at": at, "required_documents": required}
    if appointment_id is not None:
        data["appointment_id"] = appointment_id
    if department is not None:
        data["department"] = department
    if exam_type_label is not None:
        data["exam_type_label"] = exam_type_label
    if instruction_source_id is not None:
        data["instruction_source_id"] = instruction_source_id
    if instruction_version is not None:
        data["instruction_version"] = instruction_version
    if upcoming_count is not None:
        data["upcoming_count"] = upcoming_count
    return ToolResult(OK, data)


def _required_documents(value: Any) -> list[str] | None:
    """design §5.1: a list of non-empty strings, deduplicated and sorted - or None when the value
    is not that shape, including when an older appointment-service omits the field entirely
    (it cannot say what is required, so fail closed rather than assume nothing is needed)."""
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        return None
    return sorted(set(value))


class AppointmentServiceGateway:
    """ToolGateway: CheckAppointment over HTTP, every other action the fallback's."""

    def __init__(self, base_url: str, api_key: str, *, fallback: ToolGateway | None = None,
                 transport: Transport = urllib_transport, timeout: float = TIMEOUT_SECONDS,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport, self._timeout = transport, timeout
        self._clock = clock
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
        appointment_id = parameters.get("appointment_id")
        if isinstance(appointment_id, str) and appointment_id:
            url += f"?appointment_id={urllib.parse.quote(appointment_id, safe='')}"
        headers = {"X-API-Key": self._api_key, "X-Execution-ID": idempotency_key, "Accept": "application/json"}
        try:
            answer = self._transport(url, headers, self._timeout)
        except (OSError, http_client.HTTPException) as exc:
            # OSError: no answer at all (transient). http_client.HTTPException: the server did
            # answer, just not in HTTP the gateway can trust (IncompleteRead, BadStatusLine, ...) -
            # not the same as no answer at all, so not a transient_failure.
            return no_answer_result(exc)
        return map_response(answer, now=self._clock())


def build_gateway(env: Mapping[str, str] | None = None,
                   fallback: ToolGateway | None = None) -> tuple[ToolGateway | None, str]:
    """The gateway the live server uses (design §2.2-2.3): (gateway, "mock" | "appointment-service"),
    or (None, why the Agent Orchestrator must not start). Neither value ever holds the URL or the
    key. `fallback` (sub-project 13 design §5.1) is what every action but CheckAppointment uses -
    typically the document-service gateway, so the two compose instead of each falling back to
    its own mock."""
    env = os.environ if env is None else env
    url = env.get("APPOINTMENT_SERVICE_URL", "").strip()
    if not url:
        return (fallback if fallback is not None else MockGateway()), "mock"
    key = env.get("APPOINTMENT_API_KEY", "").strip()
    if not key:
        return None, "disabled: APPOINTMENT_API_KEY is not set"
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None, "disabled: APPOINTMENT_SERVICE_URL is not an http(s) URL"
    return AppointmentServiceGateway(url, key, fallback=fallback), "appointment-service"
