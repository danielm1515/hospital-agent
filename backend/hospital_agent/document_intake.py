"""The Session Service's client for the document-service's upload (sub-project 13, design §5.3;
sub-project 17 task 2 decision 4 for PDF/JPEG/PNG and the `reason` field).

The patient's own action, not a step of the plan: it is not proposed, allowed by policy or
retried, so it is not a ToolGateway - the one recorded exception to "only the Tool Executor
calls an external system". It forwards one document (PDF, JPEG or PNG), as multipart `file`
with its real `Content-Type` (sniffed by magic bytes, never trusted from the caller), and reads
back the document-service's 201 answer. Anything that is not that answer - no answer at all, a
5xx, a refused key, any other status, or a body that is not the contract - is IntakeUnavailable,
and the caller records nothing. A `503 classifier_unavailable` body is reported as that finer
code rather than the generic `status_503`, so the application log names the real cause (Task 2
decision 1).

Sub-project 19 (design D5): a 201 answer also reports the document-service's own LLM call as
`llm_usage` - read strictly here, and bookkeeping only: an invalid one is dropped (`None`, a
code-only WARNING `llm_usage_invalid`) and the upload goes on exactly as it would have.

Standard library only, over the shared transport in `execution.http` (no redirect, no proxy,
a bounded body). The URL and the key never reach a repr, an exception or a log line.
"""
from __future__ import annotations

import http.client as http_client
import json
import logging
import os
import re
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from . import document_status
from .execution import http
from .execution.http import HttpResponse
from .llm.usage import DOCUMENT_CALL_CODES, LLMUsage

logger = logging.getLogger(__name__)

# The document-service's documented worst case is 30 s of PDF parsing + 40 s of classification
# = 70 s (its README); the client must wait longer than that, or an accepted upload would look
# like a failure and the patient's retry would come back as a duplicate.
TIMEOUT_SECONDS = 75.0
# The staff banner's live check: short, because it runs on each poll and only asks "is it up".
HEALTH_TIMEOUT_SECONDS = 3.0

# (method, url, headers, body, timeout) -> the response; raises OSError / HTTPException when
# there is no usable answer.
Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], HttpResponse]

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # a document id or a catalog type, as the agent will store it
_MAX_FILENAME = 100

# Magic-byte sniffing (Task 2 decision 4): the real Content-Type is never taken from the
# caller or the file name - it is read from the bytes themselves, the same way the
# document-service itself will re-check it. Anything that is none of the three is still sent
# as `application/pdf` - the client does not decide what is acceptable, the document-service's
# own signature check does (and answers `not_supported_format`).
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_KIND_CONTENT_TYPE = {"pdf": "application/pdf", "jpeg": "image/jpeg", "png": "image/png"}
_KIND_EXTENSION = {"pdf": "pdf", "jpeg": "jpg", "png": "png"}

# The document-service's closed set of refusal reason codes (design §4.2, Task 2 decision 2).
# Anything else - including a code this version does not know - is ignored (kept `None`),
# never guessed at or passed through unchecked.
_REASONS = frozenset({
    "not_supported_format", "too_large", "parse_error", "no_text_layer", "too_many_pages",
    "too_much_text", "classifier_unparsable", "unknown_type", "future_date", "no_date", "too_old",
})


def sniff_kind(data: bytes) -> str:
    """`"jpeg"` or `"png"` by magic bytes, else `"pdf"` (the previous, and still the most
    common, case - and the safe default for anything unrecognised, since the document-service
    re-checks the actual bytes regardless of what this header claims)."""
    if data.startswith(_JPEG_MAGIC):
        return "jpeg"
    if data.startswith(_PNG_MAGIC):
        return "png"
    return "pdf"


# design D5: the model name as the document-service reports it - a code, never free text.
_MODEL = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_USAGE_COUNTS = ("input_tokens", "cached_input_tokens", "output_tokens")


@dataclass(frozen=True)
class DocumentUsage:
    """The document-service's one LLM call for an upload (design D5): its call code
    (`DocumentClassify` / `DocumentVision`), its model, and its billed tokens - `None` when the
    provider gave no usable usage (the call was made; what it cost is unknown)."""

    call: str
    model: str
    usage: LLMUsage | None


class IntakeUnavailable(Exception):
    """The document-service could not be asked, or did not answer with its contract. The
    message is a short code (no URL, no key, no patient data)."""


@dataclass(frozen=True)
class IntakeAnswer:
    result: str
    document_id: str | None
    document_type: str | None
    duplicate_of: str | None
    # The document-service's fixed refusal code (Task 2 decision 2) - `None` for `ACCEPTED`,
    # `DUPLICATE_DOCUMENT`, `NON_MEDICAL_DOCUMENT`, `PATIENT_MISMATCH`, an answer with no
    # `reason` at all (an older document-service), or one this version does not recognise.
    reason: str | None = None
    # Sub-project 19 (design D5): the document-service's LLM call, or None - no call made
    # (a duplicate, an early rejection), an older document-service, or an invalid report.
    llm_usage: DocumentUsage | None = None


def _urllib_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None,
                      timeout: float) -> HttpResponse:
    return http.request(method, url, headers, body, timeout)


def _boundary() -> str:
    return uuid.uuid4().hex


def _safe_filename(filename: str, kind: str) -> str:
    """A name that cannot break the part header: printable ASCII only, without `"` or `\\`.
    A name with nothing left of its stem (e.g. an all-Hebrew one) is sent as document.<ext>,
    with the extension matching the sniffed kind - the document-service judges the bytes, not
    the name."""
    kept = "".join(ch for ch in (filename or "") if " " <= ch <= "~" and ch not in '"\\').strip()
    kept = kept[:_MAX_FILENAME]
    if not kept or kept.startswith(".") or not any(ch.isalnum() for ch in kept.rsplit(".", 1)[0]):
        return f"document.{_KIND_EXTENSION[kind]}"
    return kept


def _multipart(filename: str, data: bytes, kind: str) -> tuple[str, bytes]:
    boundary = _boundary()
    while boundary.encode() in data:  # a boundary must never occur inside the part
        boundary = _boundary()
    head = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{_safe_filename(filename, kind)}"\r\n'
            f"Content-Type: {_KIND_CONTENT_TYPE[kind]}\r\n\r\n").encode("ascii")
    return boundary, head + data + f"\r\n--{boundary}--\r\n".encode("ascii")


def _optional_id(body: dict, key: str) -> tuple[bool, str | None]:
    value = body.get(key)
    if value is None:
        return True, None
    return (isinstance(value, str) and bool(_ID.match(value))), value


def _optional_reason(body: dict) -> str | None:
    """The closed set only (Task 2 decision 2) - a code this version does not know is dropped,
    never guessed at or passed through unchecked."""
    value = body.get("reason")
    return value if isinstance(value, str) and value in _REASONS else None


def _document_usage(value: object) -> DocumentUsage:
    """Strict (design D5): ValueError on anything but the documented object. Counts are three
    ints in 0..MAX_TOKENS (never a bool) with cached <= input - LLMUsage's own checks - or all
    three null. Keys beyond the documented five are ignored, like the answer's own."""
    if not isinstance(value, dict):
        raise ValueError("not_an_object")
    call, model = value.get("call"), value.get("model")
    if not isinstance(call, str) or call not in DOCUMENT_CALL_CODES:
        raise ValueError("call")
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        raise ValueError("model")
    if not all(key in value for key in _USAGE_COUNTS):
        raise ValueError("counts_missing")
    counts = [value[key] for key in _USAGE_COUNTS]
    if all(count is None for count in counts):
        return DocumentUsage(DOCUMENT_CALL_CODES[call], model, None)
    return DocumentUsage(DOCUMENT_CALL_CODES[call], model, LLMUsage(*counts))  # ValueError when invalid


def _optional_usage(body: dict) -> DocumentUsage | None:
    """`null` or no field at all is no call; anything invalid is dropped with a code-only
    WARNING - bookkeeping never rejects an upload (design D5)."""
    value = body.get("llm_usage")
    if value is None:
        return None
    try:
        return _document_usage(value)
    except ValueError:
        logger.warning("llm_usage_invalid")
        return None


def map_answer(response: HttpResponse) -> IntakeAnswer:
    """The 201 answer of design §4.1, or IntakeUnavailable.

    A `503` whose body is the document-service's own `{"error": "classifier_unavailable"}` is
    reported as that finer code - a provider failure, never a verdict on the file (Task 2
    decision 1) - so the application log names the real cause instead of the generic
    `status_503`. Any other non-`201` status stays `status_<n>`.
    """
    if response.status != 201:
        if response.status == 503:
            try:
                error_body = json.loads(response.body)
            except (ValueError, UnicodeDecodeError):
                error_body = None
            if isinstance(error_body, dict) and error_body.get("error") == "classifier_unavailable":
                raise IntakeUnavailable("classifier_unavailable")
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
    return IntakeAnswer(result, document_id, document_type, duplicate_of, _optional_reason(body),
                        _optional_usage(body))


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
        kind = sniff_kind(data)
        boundary, body = _multipart(filename, data, kind)
        headers = {"X-API-Key": self._api_key, "Accept": "application/json",
                   "Content-Type": f"multipart/form-data; boundary={boundary}"}
        try:
            response = self._transport("POST", url, headers, body, self.timeout)
        except (OSError, http_client.HTTPException):
            # `from None`: the transport's own exception can name the host.
            raise IntakeUnavailable("no_answer") from None
        return map_answer(response)

    def health(self) -> str:
        """`ok` | `degraded` | `unreachable` from the document-service's own /health, within
        HEALTH_TIMEOUT_SECONDS - for the staff system-status banner (row 98). Never raises."""
        try:
            response = self._transport("GET", f"{self._base_url}/health", {"Accept": "application/json"}, b"",
                                       HEALTH_TIMEOUT_SECONDS)
        except (OSError, http_client.HTTPException):
            return "unreachable"
        return document_status.health_of(response.status, response.body)


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
