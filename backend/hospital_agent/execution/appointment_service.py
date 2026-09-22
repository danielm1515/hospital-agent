"""CheckAppointment against the owner's appointment-service (sub-project 10, design §2).

Only CheckAppointment leaves over HTTP; the other three actions are MockGateway's, because no
real system exists for them. Every HTTP outcome becomes a ToolResult the Tool Executor and
the Retry Manager already understand, so nothing downstream changes: server-side failures
and no answer at all are transient (bounded retry, then RETRY_EXHAUSTED), and everything
else that is not a found appointment with a timezone-aware time is an error (escalation).
"""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..naming import Action
from .gateway import ERROR, OK, TRANSIENT_FAILURE, MockGateway, ToolGateway, ToolResult

TIMEOUT_SECONDS = 5
_TRANSIENT = {500: "unavailable", 502: "unavailable", 503: "unavailable", 504: "timeout"}

# design §2.4 / minor fix 4: the transport never reads more than this many bytes of a body -
# an oversized answer is not the contract either way, so there is no reason to buffer it fully.
# One byte over the 64 KiB limit is enough to tell "too long" apart from "exactly 64 KiB".
MAX_BODY_BYTES = 64 * 1024 + 1


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


# design §2.4 / minor fix 3: no proxy, even when HTTP_PROXY/http_proxy is set in the environment -
# a proxy would see X-API-Key and patient_id. ProxyHandler({}) is an explicit "use no proxy",
# overriding ProxyHandler's own default of reading those variables.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def urllib_transport(url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with _OPENER.open(request, timeout=timeout) as answer:
            return HttpResponse(answer.status, answer.read(MAX_BODY_BYTES))
    except urllib.error.HTTPError as exc:  # a status the server did send is an answer, not a failure
        with exc:
            return HttpResponse(exc.code, exc.read(MAX_BODY_BYTES))


def _error(code: str) -> ToolResult:
    return ToolResult(ERROR, {"error": code})


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None


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
    return ToolResult(OK, {"appointment_at": at})


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
        headers = {"X-API-Key": self._api_key, "X-Execution-ID": idempotency_key, "Accept": "application/json"}
        try:
            answer = self._transport(url, headers, self._timeout)
        except http.client.HTTPException:
            # the server did answer, just not in HTTP the gateway can trust (IncompleteRead,
            # BadStatusLine, ...) - not the same as no answer at all, so not a transient_failure.
            return _error("invalid_response")
        except OSError as exc:
            timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
            return ToolResult(TRANSIENT_FAILURE, {"error": "timeout" if timed_out else "unavailable"})
        return map_response(answer, now=self._clock())


def build_gateway(env: Mapping[str, str] | None = None) -> tuple[ToolGateway | None, str]:
    """The gateway the live server uses (design §2.2-2.3): (gateway, "mock" | "appointment-service"),
    or (None, why the Agent Orchestrator must not start). Neither value ever holds the URL or the key."""
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
