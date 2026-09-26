"""Sub-project 16 (design docs/superpowers/specs/2026-09-26-appointment-list-design.md, D4-D7): the
patient's appointments, read straight from the appointment-service for the patient's and the
staff's screens.

Not a plan step: nothing is proposed, allowed by policy, retried, turned into an event or stored -
the second recorded exception to "only the Tool Executor calls an external system"
(docs/spec_corrections.md row 89, beside row 79). Anything that is not the contract - no answer, any
status but 200 (or the registry's 404), a body that is not exactly the documented shape - is
AppointmentsUnavailable, never a partial list. Standard library only, over the shared transport in
execution.http (no redirect, no proxy, a bounded body). The URL and the key never reach a repr, an
exception or a log line. The 64 KiB transport bound holds about 100 catalog-validated rows (roughly
30 KB); an oversized answer fails closed as `invalid_response` rather than being read partially.
"""
from __future__ import annotations

import http.client as http_client
import json
import os
import re
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from .execution import http
from .execution.http import MAX_BODY_BYTES, HttpResponse

TIMEOUT_SECONDS = 5.0  # the same bound as CheckAppointment (sub-project 10 design §2.4)
STATUSES = frozenset({"Scheduled", "Cancelled"})

# The appointment-service's own patient_id pattern (alnum first, then alnum/./_/- up to 64 chars
# total, which also rules out a leading "."). A malformed patient_id is a programming error - the
# routes always pass one already resolved from the token or the case - so it never reaches HTTP.
_PATIENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], HttpResponse]


class AppointmentsUnavailable(Exception):
    """The list could not be read. `code` is short and carries no URL, key or patient data."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PatientNotFound(Exception):
    """The appointment-service's registry does not know the patient (its 404 patient_not_found)."""


@dataclass(frozen=True)
class Appointment:
    appointment_id: str
    appointment_at: datetime
    department: str
    doctor_name: str | None
    location: str | None
    status: str
    required_documents: tuple[str, ...]


@dataclass(frozen=True)
class AppointmentList:
    appointments: tuple[Appointment, ...]
    truncated: bool


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _optional_text(value: object) -> bool:
    return value is None or isinstance(value, str)


def _appointment(item: object, patient_id: str) -> Appointment:
    if not isinstance(item, dict):
        raise AppointmentsUnavailable("invalid_response")
    at_text, documents, status = item.get("appointment_at"), item.get("required_documents"), item.get("status")
    if not (item.get("patient_id") == patient_id
            and _text(item.get("appointment_id")) and _text(item.get("department")) and _text(at_text)
            and _optional_text(item.get("doctor_name")) and _optional_text(item.get("location"))
            and isinstance(status, str) and status in STATUSES
            and isinstance(documents, list) and all(_text(d) for d in documents)):
        raise AppointmentsUnavailable("invalid_response")
    try:
        at = datetime.fromisoformat(at_text)
    except ValueError:
        raise AppointmentsUnavailable("invalid_response") from None
    if at.tzinfo is None:
        raise AppointmentsUnavailable("invalid_response")
    return Appointment(item["appointment_id"], at, item["department"], item.get("doctor_name"),
                       item.get("location"), status, tuple(sorted(set(documents))))


def map_answer(response: HttpResponse, patient_id: str) -> AppointmentList:
    """The answer for `patient_id`: every row not carrying that same patient_id is rejected, not
    silently dropped (D7) - a mixed-patient answer is not the contract either."""
    if len(response.body) >= MAX_BODY_BYTES:
        raise AppointmentsUnavailable("invalid_response")
    try:
        body = json.loads(response.body)
    except (ValueError, RecursionError):
        # RecursionError: pathologically deep nesting is not a bigger answer, just a hostile one -
        # treat it the same as any other body that doesn't parse.
        body = None
    if response.status == 404:
        if isinstance(body, dict) and body.get("error") == "patient_not_found":
            raise PatientNotFound
        raise AppointmentsUnavailable("invalid_response")
    if response.status != 200:
        raise AppointmentsUnavailable(f"status_{response.status}")
    if not (isinstance(body, dict) and isinstance(body.get("appointments"), list)
            and isinstance(body.get("truncated"), bool)):
        raise AppointmentsUnavailable("invalid_response")
    return AppointmentList(tuple(_appointment(item, patient_id) for item in body["appointments"]),
                            body["truncated"])


def _urllib_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None,
                      timeout: float) -> HttpResponse:
    return http.request(method, url, headers, body, timeout)


class AppointmentListClient:
    def __init__(self, base_url: str, api_key: str, *, transport: Transport | None = None,
                 timeout: float = TIMEOUT_SECONDS) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport or _urllib_transport
        self.timeout = timeout

    def __repr__(self) -> str:  # the URL and the key stay out of every log and traceback
        return "AppointmentListClient()"

    def list(self, patient_id: str, start: datetime, end: datetime) -> AppointmentList:
        # Both checks are programming errors, not user input: the routes always resolve patient_id
        # from the token or the case, and always build start/end as aware datetimes.
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware")
        if not _PATIENT_ID_RE.match(patient_id):
            raise ValueError("invalid patient_id")
        query = urllib.parse.urlencode({"from": start.isoformat(), "to": end.isoformat()})
        url = f"{self._base_url}/api/v1/patients/{urllib.parse.quote(patient_id, safe='')}/appointments?{query}"
        headers = {"X-API-Key": self._api_key, "Accept": "application/json"}
        try:
            response = self._transport("GET", url, headers, None, self.timeout)
        except (OSError, http_client.HTTPException):
            # `from None`: the transport's own exception can name the host.
            raise AppointmentsUnavailable("no_answer") from None
        return map_answer(response, patient_id)


def build_list_client(env: Mapping[str, str] | None = None) -> AppointmentListClient | None:
    """From the same two variables as sub-project 10's gateway, or None (design D8: no mock list)."""
    env = os.environ if env is None else env
    url = env.get("APPOINTMENT_SERVICE_URL", "").strip()
    key = env.get("APPOINTMENT_API_KEY", "").strip()
    if not url or not key:
        return None
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return AppointmentListClient(url, key)
