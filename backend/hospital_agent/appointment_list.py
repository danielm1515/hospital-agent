"""Sub-project 16 (design docs/superpowers/specs/2026-09-26-appointment-list-design.md, D4-D7): the
patient's appointments, read straight from the appointment-service for the patient's and the
staff's screens.

Not a plan step: nothing is proposed, allowed by policy, retried, turned into an event or stored -
the second recorded exception to "only the Tool Executor calls an external system"
(docs/spec_corrections.md row 89, beside row 79). Anything that is not the contract - no answer, any
status but 200 (or the registry's 404), a body that is not exactly the documented shape - is
AppointmentsUnavailable, never a partial list. Standard library only, over the shared transport in
execution.http (no redirect, no proxy, a bounded body). The URL and the key never reach a repr, an
exception or a log line.
"""
from __future__ import annotations

import http.client as http_client
import json
import os
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from .execution import http
from .execution.http import MAX_BODY_BYTES, HttpResponse

TIMEOUT_SECONDS = 5.0  # the same bound as CheckAppointment (sub-project 10 design §2.4)
STATUSES = frozenset({"Scheduled", "Cancelled"})

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


def _appointment(item: object) -> Appointment:
    if not isinstance(item, dict):
        raise AppointmentsUnavailable("invalid_response")
    at_text, documents = item.get("appointment_at"), item.get("required_documents")
    if not (_text(item.get("appointment_id")) and _text(item.get("department")) and _text(at_text)
            and _optional_text(item.get("doctor_name")) and _optional_text(item.get("location"))
            and item.get("status") in STATUSES
            and isinstance(documents, list) and all(_text(d) for d in documents)):
        raise AppointmentsUnavailable("invalid_response")
    try:
        at = datetime.fromisoformat(at_text)
    except ValueError:
        raise AppointmentsUnavailable("invalid_response") from None
    if at.tzinfo is None:
        raise AppointmentsUnavailable("invalid_response")
    return Appointment(item["appointment_id"], at, item["department"], item.get("doctor_name"),
                       item.get("location"), item["status"], tuple(sorted(set(documents))))


def map_answer(response: HttpResponse) -> AppointmentList:
    if len(response.body) >= MAX_BODY_BYTES:
        raise AppointmentsUnavailable("invalid_response")
    try:
        body = json.loads(response.body)
    except ValueError:
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
    return AppointmentList(tuple(_appointment(item) for item in body["appointments"]), body["truncated"])


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
        query = urllib.parse.urlencode({"from": start.isoformat(), "to": end.isoformat()})
        url = f"{self._base_url}/api/v1/patients/{urllib.parse.quote(patient_id, safe='')}/appointments?{query}"
        headers = {"X-API-Key": self._api_key, "Accept": "application/json"}
        try:
            response = self._transport("GET", url, headers, None, self.timeout)
        except (OSError, http_client.HTTPException):
            # `from None`: the transport's own exception can name the host.
            raise AppointmentsUnavailable("no_answer") from None
        return map_answer(response)


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
