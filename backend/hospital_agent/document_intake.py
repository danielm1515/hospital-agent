"""The Session Service's client for the document-service's upload (sub-project 13, design §5.3).

The patient's own action, not a step of the plan: it is not proposed, allowed by policy or
retried, so it is not a ToolGateway - the one recorded exception to "only the Tool Executor
calls an external system". It forwards one PDF, as multipart `file`, and reads back the
document-service's 201 answer. Anything that is not that answer - no answer at all, a 5xx, a
refused key, any other status, or a body that is not the contract - is IntakeUnavailable, and
the caller records nothing.

Standard library only, over the shared transport in `execution.http` (no redirect, no proxy,
a bounded body). The URL and the key never reach a repr, an exception or a log line.
"""
from __future__ import annotations

import http.client as http_client
import json
import os
import re
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .execution import http
from .execution.http import HttpResponse

# The document-service's documented worst case is 30 s of PDF parsing + 40 s of classification
# = 70 s (its README); the client must wait longer than that, or an accepted upload would look
# like a failure and the patient's retry would come back as a duplicate.
TIMEOUT_SECONDS = 75.0

# (method, url, headers, body, timeout) -> the response; raises OSError / HTTPException when
# there is no usable answer.
Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], HttpResponse]

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # a document id or a catalog type, as the agent will store it
_FALLBACK_FILENAME = "document.pdf"
_MAX_FILENAME = 100


class IntakeUnavailable(Exception):
    """The document-service could not be asked, or did not answer with its contract. The
    message is a short code (no URL, no key, no patient data)."""


@dataclass(frozen=True)
class IntakeAnswer:
    result: str
    document_id: str | None
    document_type: str | None
    duplicate_of: str | None


def _urllib_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None,
                      timeout: float) -> HttpResponse:
    return http.request(method, url, headers, body, timeout)


def _boundary() -> str:
    return uuid.uuid4().hex


def _safe_filename(filename: str) -> str:
    """A name that cannot break the part header: printable ASCII only, without `"` or `\\`.
    A name with nothing left of its stem (e.g. an all-Hebrew one) is sent as document.pdf -
    the document-service judges the bytes, not the name."""
    kept = "".join(ch for ch in (filename or "") if " " <= ch <= "~" and ch not in '"\\').strip()
    kept = kept[:_MAX_FILENAME]
    if not kept or kept.startswith(".") or not any(ch.isalnum() for ch in kept.rsplit(".", 1)[0]):
        return _FALLBACK_FILENAME
    return kept


def _multipart(filename: str, data: bytes) -> tuple[str, bytes]:
    boundary = _boundary()
    while boundary.encode() in data:  # a boundary must never occur inside the part
        boundary = _boundary()
    head = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{_safe_filename(filename)}"\r\n'
            "Content-Type: application/pdf\r\n\r\n").encode("ascii")
    return boundary, head + data + f"\r\n--{boundary}--\r\n".encode("ascii")


def _optional_id(body: dict, key: str) -> tuple[bool, str | None]:
    value = body.get(key)
    if value is None:
        return True, None
    return (isinstance(value, str) and bool(_ID.match(value))), value


def map_answer(response: HttpResponse) -> IntakeAnswer:
    """The 201 answer of design §4.1, or IntakeUnavailable."""
    if response.status != 201:
        raise IntakeUnavailable(f"status_{response.status}")
    try:
        body = json.loads(response.body)
    except (ValueError, UnicodeDecodeError):
        raise IntakeUnavailable("invalid_response") from None
    if not isinstance(body, dict):
        raise IntakeUnavailable("invalid_response")
    result, document_id = body.get("result"), body.get("document_id")
    type_ok, document_type = _optional_id(body, "document_type")
    duplicate_ok, duplicate_of = _optional_id(body, "duplicate_of")
    if not (isinstance(result, str) and result and isinstance(document_id, str) and _ID.match(document_id)
            and type_ok and duplicate_ok):
        raise IntakeUnavailable("invalid_response")
    return IntakeAnswer(result, document_id, document_type, duplicate_of)


class DocumentIntakeClient:
    def __init__(self, base_url: str, api_key: str, *, transport: Transport | None = None,
                 timeout: float = TIMEOUT_SECONDS) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport or _urllib_transport
        self.timeout = timeout

    def __repr__(self) -> str:  # the URL and the key stay out of every log and traceback
        return "DocumentIntakeClient()"

    def submit(self, patient_id: str, filename: str, data: bytes) -> IntakeAnswer:
        url = f"{self._base_url}/api/v1/patients/{urllib.parse.quote(patient_id, safe='')}/documents"
        boundary, body = _multipart(filename, data)
        headers = {"X-API-Key": self._api_key, "Accept": "application/json",
                   "Content-Type": f"multipart/form-data; boundary={boundary}"}
        try:
            response = self._transport("POST", url, headers, body, self.timeout)
        except (OSError, http_client.HTTPException):
            # `from None`: the transport's own exception can name the host.
            raise IntakeUnavailable("no_answer") from None
        return map_answer(response)


def build_intake_client(env: Mapping[str, str] | None = None) -> DocumentIntakeClient | None:
    """The client the live server uses: from the same two variables as build_document_gateway,
    or None - and then the text upload stays the way in (design §5.3)."""
    env = os.environ if env is None else env
    url = env.get("DOCUMENT_SERVICE_URL", "").strip()
    key = env.get("DOCUMENT_API_KEY", "").strip()
    if not url or not key:
        return None
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return DocumentIntakeClient(url, key)
