"""LLM telemetry (staff-fixes design Task 1, decisions 1 and 3).

Every attempt of every LLM call is logged here, one line, in the parent process (the
Response Evaluator's worker process has no log configuration, so its caller logs on its
behalf). No request text, prompt or answer ever appears - only the call name, the model,
the time taken and the outcome code (§12.3).

The last outcome is also kept in memory - the last success time, and the last error code and
time - for `/health` and the staff-only `GET /api/staff/system-status` banner.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import TypedDict

from .schemas import Call

logger = logging.getLogger("hospital_agent.llm")

_lock = threading.Lock()
_last_ok_at: str | None = None
_last_error: str | None = None
_last_error_at: str | None = None


class Status(TypedDict):
    last_ok_at: str | None
    last_error: str | None
    last_error_at: str | None


def record(call: Call, model: str, ms: int, outcome: str) -> None:
    """`llm call=<call> model=<model> ms=<ms> outcome=<ok|code>` - INFO for `ok`, WARNING
    otherwise - and updates the in-memory status `status()` reports."""
    level = logging.INFO if outcome == "ok" else logging.WARNING
    logger.log(level, "llm call=%s model=%s ms=%s outcome=%s", call, model, ms, outcome)
    now = datetime.now(UTC).isoformat()
    global _last_ok_at, _last_error, _last_error_at
    with _lock:
        if outcome == "ok":
            _last_ok_at = now
        else:
            _last_error, _last_error_at = outcome, now


def status() -> Status:
    with _lock:
        return {"last_ok_at": _last_ok_at, "last_error": _last_error, "last_error_at": _last_error_at}


def reset() -> None:
    """Tests only: clear the in-memory status between runs."""
    global _last_ok_at, _last_error, _last_error_at
    with _lock:
        _last_ok_at = _last_error = _last_error_at = None
