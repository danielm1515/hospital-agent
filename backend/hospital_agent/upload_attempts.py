"""Every patient upload attempt and how it ended (migration 0010, docs/spec_corrections.md row 98).

An upload that the document-service refuses, or that never gets an answer, adds no event to the
case - so neither the Audit nor the Data Log ever saw it, and the staff could not tell that a
patient tried three times while the service was down. This table keeps one row per attempt, for
the staff audit journal: the case, which path (`upload` for a requested document, `reply` for a
document a staff request asked for), the outcome code the patient was shown, and the detail code
behind it (the document-service's own reason, or why it could not be reached). Codes only: never
the file, its name, a document id or any content (§12.3).

Bookkeeping outside Audit, exactly like llm_usage (row 94): its own short transaction, never
inside StateManager.apply, read by no State, guard, policy decision or Temporal rule, and a
failed write is logged and never blocks the upload.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection, Engine

from .db import upload_attempts

logger = logging.getLogger(__name__)

UPLOAD, REPLY = "upload", "reply"


class UploadAttemptRecorder:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.engine, self.clock = engine, clock

    def record(self, case_id: str, kind: str, outcome: str, reason: str | None) -> None:
        """Never raises: a bookkeeping failure must not turn an upload's answer into an error."""
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(upload_attempts).values(case_id=case_id, kind=kind, outcome=outcome,
                                                            reason=reason, created_at=self.clock()))
        except Exception as exc:  # noqa: BLE001 - bookkeeping, never fail the upload (module docstring)
            logger.warning("upload_attempt_write_failed error=%s", type(exc).__name__)


def case_attempts(conn: Connection, case_id: str) -> list[dict[str, Any]]:
    """One case's upload attempts in order - codes and times only."""
    query = (select(upload_attempts.c.kind, upload_attempts.c.outcome, upload_attempts.c.reason,
                    upload_attempts.c.created_at)
             .where(upload_attempts.c.case_id == case_id)
             .order_by(upload_attempts.c.created_at, upload_attempts.c.attempt_id))
    return [{"kind": r.kind, "outcome": r.outcome, "reason": r.reason, "created_at": r.created_at}
            for r in conn.execute(query)]
