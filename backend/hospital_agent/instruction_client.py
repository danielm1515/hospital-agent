"""Sub-project 18 (design D12, D14): the small client behind the patient's and the staff's
instruction-read routes (`api/instructions.py`) - `GET /api/v1/instructions/{source_id}?version=`.

Not the same client Task 4's `LoadInstructions` gateway uses
(`execution/appointment_service.py`'s `AppointmentServiceGateway._load_instructions`, wired
into the FSM's Tool Executor): this one backs a plain read for the UI, outside the FSM, exactly
like `appointment_list.py` beside it (docs/spec_corrections.md row 89, extended by that same row
to cover this read too) - nothing here is proposed, policy-checked, retried, turned into an
event or stored. Same transport rules: standard library only, over the shared transport in
`execution.http` (no redirect, no proxy, a bounded body, a 5 s timeout). The URL and the key
never reach a repr, an exception or a log line.
"""
from __future__ import annotations

import http.client as http_client
import json
import os
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .execution import http
from .execution.http import MAX_BODY_BYTES, HttpResponse

TIMEOUT_SECONDS = 5.0  # the same bound as appointment_list.AppointmentListClient

# The instruction system's own bounds on title/text (Task 4, design D8): a title the same
# length as a free-text label, a text long enough for the catalog's demo drafts.
MAX_TITLE_LENGTH = 200
MAX_TEXT_LENGTH = 4000

Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], HttpResponse]


class InstructionUnavailable(Exception):
    """The instruction could not be read. `code` is short and carries no title, text or id."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class InstructionNotFound(Exception):
    """The instruction system's own 404 `instruction_not_found`: the registry approved this
    source_id + version, but the service has never heard of it - registry and service
    disagree, and the caller must not treat that as approved and unavailable-only text."""


@dataclass(frozen=True)
class Instruction:
    source_id: str
    version: str
    title: str
    text: str


def _bounded_text(value: object, max_length: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= max_length


def map_answer(response: HttpResponse, *, source_id: str, version: str) -> Instruction:
    """The answer for exactly `source_id` + `version` - anything else, including a different
    source or version, is `invalid_response`, never silently substituted."""
    if len(response.body) >= MAX_BODY_BYTES:
        raise InstructionUnavailable("invalid_response")
    try:
        body = json.loads(response.body)
    except (ValueError, RecursionError):
        # RecursionError: pathologically deep nesting is not a bigger answer, just a hostile one.
        body = None
    if response.status == 404:
        if isinstance(body, dict) and body.get("error") == "instruction_not_found":
            raise InstructionNotFound
        raise InstructionUnavailable("invalid_response")
    if response.status != 200:
        raise InstructionUnavailable(f"status_{response.status}")
    if not isinstance(body, dict):
        raise InstructionUnavailable("invalid_response")
    if body.get("source_id") != source_id or body.get("version") != version:
        raise InstructionUnavailable("invalid_response")
    title, text = body.get("title"), body.get("text")
    if not _bounded_text(title, MAX_TITLE_LENGTH) or not _bounded_text(text, MAX_TEXT_LENGTH):
        raise InstructionUnavailable("invalid_response")
    return Instruction(source_id, version, title, text)


def _urllib_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None,
                      timeout: float) -> HttpResponse:
    return http.request(method, url, headers, body, timeout)


class InstructionClient:
    def __init__(self, base_url: str, api_key: str, *, transport: Transport | None = None,
                 timeout: float = TIMEOUT_SECONDS) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport or _urllib_transport
        self.timeout = timeout

    def __repr__(self) -> str:  # the URL and the key stay out of every log and traceback
        return "InstructionClient()"

    def get(self, source_id: str, version: str) -> Instruction:
        query = urllib.parse.urlencode({"version": version})
        url = f"{self._base_url}/api/v1/instructions/{urllib.parse.quote(source_id, safe='')}?{query}"
        headers = {"X-API-Key": self._api_key, "Accept": "application/json"}
        try:
            response = self._transport("GET", url, headers, None, self.timeout)
        except (OSError, http_client.HTTPException):
            # `from None`: the transport's own exception can name the host.
            raise InstructionUnavailable("no_answer") from None
        return map_answer(response, source_id=source_id, version=version)


def build_instruction_client(env: Mapping[str, str] | None = None) -> InstructionClient | None:
    """From the same two variables as sub-project 10's gateway and `appointment_list`'s own
    client, or None (design D12: no mock instruction read for the UI)."""
    env = os.environ if env is None else env
    url = env.get("APPOINTMENT_SERVICE_URL", "").strip()
    key = env.get("APPOINTMENT_API_KEY", "").strip()
    if not url or not key:
        return None
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return InstructionClient(url, key)
