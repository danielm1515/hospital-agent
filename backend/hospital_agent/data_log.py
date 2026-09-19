"""Data Log (spec §12.3; LLM design §7) - the case's own content.

The request text, uploaded documents, instructions shown and outgoing messages are kept
here and nowhere else: audit_log records only their content_hash, which is the reference
back to this table. Deleting an entry at the patient's request leaves a tombstone - the
content is cleared, the hash and the row stay (§18.4).
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from .db import data_log


class DataKind(StrEnum):
    REQUEST_TEXT = "request_text"
    UPLOADED_DOCUMENT = "uploaded_document"
    INSTRUCTIONS = "instructions"
    OUTGOING_MESSAGE = "outgoing_message"


@dataclass(frozen=True)
class DataEntry:
    entry_id: str
    case_id: str
    patient_id: str
    kind: DataKind
    content: str | None  # None once tombstoned
    content_hash: str
    created_at: datetime
    deleted_at: datetime | None = None


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def record(conn: Connection, case_id: str, patient_id: str, kind: DataKind, content: str, now: datetime) -> DataEntry:
    entry = DataEntry(f"DATA-{uuid.uuid4().hex[:12]}", case_id, patient_id, kind, content,
                      content_hash(content), now)
    conn.execute(insert(data_log).values(
        entry_id=entry.entry_id, case_id=case_id, patient_id=patient_id, kind=kind.value,
        content=content, content_hash=entry.content_hash, created_at=now,
    ))
    return entry


def entries(conn: Connection, case_id: str, kind: DataKind) -> list[DataEntry]:
    """The case's entries of one kind, oldest first, tombstones included (content None)."""
    rows = conn.execute(
        select(data_log).where(data_log.c.case_id == case_id, data_log.c.kind == kind.value)
        .order_by(data_log.c.created_at, data_log.c.entry_id)
    ).mappings()
    return [DataEntry(**{**row, "kind": DataKind(row["kind"])}) for row in rows]


def tombstone(conn: Connection, entry_id: str, now: datetime) -> int:
    """Clear an entry's content, keeping its hash and row (§18.4). Returns rows changed."""
    return conn.execute(
        update(data_log).where(data_log.c.entry_id == entry_id, data_log.c.deleted_at.is_(None))
        .values(content=None, deleted_at=now)
    ).rowcount
