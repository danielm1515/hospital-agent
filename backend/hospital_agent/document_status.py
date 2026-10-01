"""Is the document-service usable - for the staff system-status banner (row 98).

Two signals, because either alone misses a case:
- the last upload's outcome, kept in memory like the LLM telemetry (llm/telemetry.py): it sees a
  failure the health check cannot, such as the document classifier's LLM provider being out of
  credit (`classifier_unavailable`), but only once a patient has tried;
- a live `GET <DOCUMENT_SERVICE_URL>/health` each time the banner polls (every minute): it sees
  the service being down before any patient tries.

Codes and times only - never a patient id, a file name, the URL or the key.
"""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import TypedDict

_lock = threading.Lock()
_last_ok_at: str | None = None
_last_error: str | None = None
_last_error_at: str | None = None


class Status(TypedDict):
    configured: bool
    health: str | None  # ok | degraded | unreachable; None when not configured
    last_ok_at: str | None
    last_error: str | None
    last_error_at: str | None


def record_ok() -> None:
    """The document-service answered an upload (whatever its verdict on the file)."""
    global _last_ok_at
    with _lock:
        _last_ok_at = datetime.now(UTC).isoformat()


def record_error(code: str) -> None:
    """An upload got no usable answer: `no_answer`, `status_<n>`, `invalid_response`, `classifier_unavailable`."""
    global _last_error, _last_error_at
    with _lock:
        _last_error, _last_error_at = code, datetime.now(UTC).isoformat()


def reset() -> None:
    """For tests only."""
    global _last_ok_at, _last_error, _last_error_at
    with _lock:
        _last_ok_at = _last_error = _last_error_at = None


def health_of(status: int, body: bytes) -> str:
    """The document-service's /health answer as one code."""
    if status == 200:
        try:
            parsed = json.loads(body)
        except ValueError:
            return "degraded"
        return "ok" if isinstance(parsed, dict) and parsed.get("status") == "ok" else "degraded"
    return "degraded"


def status(health: str | None) -> Status:
    with _lock:
        return {"configured": health is not None, "health": health, "last_ok_at": _last_ok_at,
                "last_error": _last_error, "last_error_at": _last_error_at}
