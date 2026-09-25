"""Direct inserts for the metrics tests (sub-project 14): rows at exact instants.

The metrics are pure reads, so their tests need rows at known times - which the real flow
(tests.driver) cannot give, since it stamps everything with the clock. Each helper writes one
row: the columns a metric reads, plus the NOT NULL ones. test_metrics_compute.py also runs
real flows through the Driver, so the shapes seeded here are checked against what the agent
actually writes.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Connection

from hospital_agent.db import approvals, audit_log, cases, executions

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def add_case(conn: Connection, case_id: str, *, created_at: datetime, state: str = "Received",
             intent: str | None = None, escalation_kind: str | None = None,
             patient_id: str = "P-10041") -> None:
    conn.execute(cases.insert().values(
        case_id=case_id, patient_id=patient_id, state=state, state_version=1, intent=intent,
        identity_verified=True, retry_cycle=0, attempt_count=0, held_documents=[],
        escalation_kind=escalation_kind, created_at=created_at, updated_at=created_at))


def add_row(conn: Connection, case_id: str, event: str, *, at: datetime, before: str | None = None,
            after: str | None = None, record_type: str = "Transition", approval_id: str | None = None,
            reasons: Sequence[str] = (), patient_id: str = "P-10041") -> None:
    conn.execute(audit_log.insert().values(
        case_id=case_id, patient_id=patient_id, record_type=record_type, event=event,
        state_before=before, state_after=after, guards={}, policy_reasons=list(reasons),
        approval_id=approval_id, rule_version="test", recorded_at=at))


def add_execution(conn: Connection, case_id: str, action: str, status: str, *,
                  started_at: datetime | None, finished_at: datetime | None = None,
                  attempt: int = 1, patient_id: str = "P-10041") -> None:
    execution_id = f"EXEC-{uuid.uuid4().hex[:12]}"
    conn.execute(executions.insert().values(
        execution_id=execution_id, case_id=case_id, patient_id=patient_id, action=action, step=1,
        retry_cycle=0, attempt_number=attempt, idempotency_key=f"idem-{execution_id}",
        status=status, started_at=started_at, finished_at=finished_at, medical_content_flag=False))


def add_approval(conn: Connection, approval_id: str, case_id: str, escalation_kind: str, *,
                 granted_at: datetime, decision: str = "resolve", patient_id: str = "P-10041") -> None:
    conn.execute(approvals.insert().values(
        approval_id=approval_id, approval_type="WorkflowDecision", case_id=case_id,
        patient_id=patient_id, reviewer_id="admin_coordinator", reviewer_role="admin_staff",
        decision=decision, reason="test", escalation_kind=escalation_kind, shown_context_ref="ctx",
        granted_at=granted_at, valid_until=granted_at + timedelta(hours=1)))
