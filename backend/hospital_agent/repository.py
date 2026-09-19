"""Row <-> record mapping for the four tables.

Every function takes an open Connection: the caller owns the transaction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, RowMapping

from .case import ApprovalRecord, CaseRecord, ExecutionRecord
from .db import approvals, audit_log, cases, executions
from .naming import EscalationKind, SafetyLevel, State


@dataclass(frozen=True)
class AuditEntry:
    """One audit_log row (§12.2). Append-only: there is no update function."""

    case_id: str
    patient_id: str
    record_type: str  # Transition | ExecutionStarted | ... | Blocked
    event: str
    state_before: str | None  # None = Initial
    state_after: str | None
    rule_version: str
    recorded_at: datetime
    guards: dict[str, bool] = field(default_factory=dict)
    action: str | None = None
    execution_id: str | None = None
    policy_result: str | None = None
    policy_reasons: list[str] = field(default_factory=list)
    attempt_number: int | None = None
    retry_cycle: int | None = None
    outcome: str | None = None
    approval_id: str | None = None
    content_hash: str | None = None
    audit_id: int | None = None


# --- cases -------------------------------------------------------------------------


def _case_from_row(row: RowMapping) -> CaseRecord:
    return CaseRecord(
        case_id=row["case_id"],
        patient_id=row["patient_id"],
        state=State(row["state"]),
        state_version=row["state_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        intent=row["intent"],
        safety_level=SafetyLevel(row["safety_level"]) if row["safety_level"] else None,
        identity_verified=row["identity_verified"],
        plan_hash=row["plan_hash"],
        ordered_steps=row["ordered_steps"],
        current_step=row["current_step"],
        retry_cycle=row["retry_cycle"],
        attempt_count=row["attempt_count"],
        required_documents=row["required_documents"],
        held_documents=list(row["held_documents"]),
        escalation_kind=EscalationKind(row["escalation_kind"]) if row["escalation_kind"] else None,
        escalated_from_state=State(row["escalated_from_state"]) if row["escalated_from_state"] else None,
        patient_deadline=row["patient_deadline"],
    )


def _case_values(case: CaseRecord) -> dict[str, Any]:
    """Every column except case_id and created_at, as plain values."""
    return {
        "patient_id": case.patient_id,
        "state": case.state.value,
        "state_version": case.state_version,
        "intent": case.intent,
        "safety_level": case.safety_level.value if case.safety_level else None,
        "identity_verified": case.identity_verified,
        "plan_hash": case.plan_hash,
        "ordered_steps": case.ordered_steps,
        "current_step": case.current_step,
        "retry_cycle": case.retry_cycle,
        "attempt_count": case.attempt_count,
        "required_documents": case.required_documents,
        "held_documents": case.held_documents,
        "escalation_kind": case.escalation_kind.value if case.escalation_kind else None,
        "escalated_from_state": case.escalated_from_state.value if case.escalated_from_state else None,
        "patient_deadline": case.patient_deadline,
        "updated_at": case.updated_at,
    }


def load_case(conn: Connection, case_id: str) -> CaseRecord | None:
    row = conn.execute(select(cases).where(cases.c.case_id == case_id)).mappings().first()
    return None if row is None else _case_from_row(row)


def list_cases(conn: Connection, state: State | None = None) -> list[CaseRecord]:
    query = select(cases).order_by(cases.c.updated_at.desc(), cases.c.case_id)
    if state is not None:
        query = query.where(cases.c.state == state.value)
    return [_case_from_row(row) for row in conn.execute(query).mappings()]


def insert_case(conn: Connection, case: CaseRecord) -> None:
    conn.execute(insert(cases).values(case_id=case.case_id, created_at=case.created_at, **_case_values(case)))


def update_case(conn: Connection, case: CaseRecord, expected_version: int) -> int:
    """Optimistic lock (§18.2): returns the number of rows updated - 0 means stale."""
    result = conn.execute(
        update(cases)
        .where(cases.c.case_id == case.case_id, cases.c.state_version == expected_version)
        .values(**_case_values(case))
    )
    return result.rowcount


# --- audit_log ---------------------------------------------------------------------


def insert_audit(conn: Connection, entry: AuditEntry) -> int:
    values = asdict(entry)
    values.pop("audit_id")
    return conn.execute(insert(audit_log).values(**values).returning(audit_log.c.audit_id)).scalar_one()


def load_trace(conn: Connection, case_id: str) -> list[AuditEntry]:
    """The case's audit rows in order - the trace the Temporal Monitor evaluates."""
    rows = conn.execute(
        select(audit_log).where(audit_log.c.case_id == case_id).order_by(audit_log.c.audit_id)
    ).mappings()
    return [AuditEntry(**dict(row)) for row in rows]


# --- approvals ---------------------------------------------------------------------


def load_approval(conn: Connection, approval_id: str) -> ApprovalRecord | None:
    row = conn.execute(select(approvals).where(approvals.c.approval_id == approval_id)).mappings().first()
    return None if row is None else ApprovalRecord(**dict(row))


def insert_approval(conn: Connection, approval: ApprovalRecord) -> None:
    conn.execute(insert(approvals).values(**asdict(approval)))


def approval_used(conn: Connection, approval_id: str) -> bool:
    """True if approval_id already appears on a committed Transition audit row (F1: replay guard)."""
    row = conn.execute(
        select(audit_log.c.audit_id)
        .where(audit_log.c.approval_id == approval_id, audit_log.c.record_type == "Transition")
        .limit(1)
    ).first()
    return row is not None


def consume_approval(conn: Connection, approval_id: str, now: datetime) -> int:
    """Single use (§18.2): returns 0 if the approval was already consumed."""
    result = conn.execute(
        update(approvals)
        .where(approvals.c.approval_id == approval_id, approvals.c.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    return result.rowcount


# --- executions --------------------------------------------------------------------


def load_execution(conn: Connection, execution_id: str) -> ExecutionRecord | None:
    row = conn.execute(select(executions).where(executions.c.execution_id == execution_id)).mappings().first()
    return None if row is None else ExecutionRecord(**dict(row))


def insert_execution(conn: Connection, execution: ExecutionRecord) -> None:
    conn.execute(insert(executions).values(**asdict(execution)))
