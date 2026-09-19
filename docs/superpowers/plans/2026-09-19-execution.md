# Sub-project 3 (Execution) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the execution layer of spec §1 and §12: the Tool Executor with `ExecutorReverified`, the `executions` outbox, the atomic `TOOL_EXECUTION_STARTED` / `AUDIT_RECORDED` pair, the Retry Manager, mock external systems, the SLA Worker and restart recovery. `python -m obs.golden` then prints the §15 golden traces from the running system, with 35 / 4 / 54 audit rows.

**Architecture (approach A, approved):**
- `POLICY_ALLOWED` writes an `executions` row (`status = intent`) bound to the decision's `state_version`, `plan_hash` and step, in the same transaction as the transition.
- `StateManager.start_execution()` re-verifies that row against the case, increments `attempt_count` *before* the call, and writes the STARTED / AUDIT_RECORDED pair, all in one transaction.
- The Tool Executor (`hospital_agent/execution/`) makes the external call outside any transaction.
- The outcome row (`ExecutionSucceeded` / `Failed` / `Unknown`) is written in the same transaction as the event that follows it.
- `hospital_agent/scripted.py` plays the components that don't exist yet around the real engines.

**Tech Stack:** Python 3.13, SQLAlchemy Core, Alembic, psycopg 3, FastAPI, pytest, PostgreSQL 16, Docker Compose. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-19-execution-design.md` (design, approved) and `docs/spec/`, the binding spec: §1, §3.1, §6, §12, §13.2, §14, §15, §16, §18.2. Read both before starting. The Core and Policy layers this builds on are described in `CLAUDE.md` and in `docs/superpowers/specs/2026-09-19-core-design.md` / `…-policy-design.md`.

**Validation:** Every file in this plan was built and run as a prototype before the plan was written. The tasks were then replayed one by one on a copy of `main`, and the full suite passed after each:

| After task | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| tests passed | 295 | 306 | 318 | 328 | 338 | 343 | 346 | 346 |

`python -m obs.golden` printed `audit rows: 35`, `4`, `54`. Copy the code exactly. If something fails, the environment differs from the prototype, so investigate before changing the code.

## Global Constraints

- Everything runs in Docker from the repo root: `docker compose run --rm backend pytest …`. The live stack keeps Postgres on host port 54322 and the API on 8000.
- A schema change is a new migration (`0002`), never an edit to `0001`. `db.py` must mirror the migrations; `tests/test_schema.py` checks this.
- Only the State Manager writes State. `HUMAN_REVIEW_REQUIRED` enters only through `EscalationCoordinator.signal()`. Every event comes from the component that owns it (`naming.EVENT_OWNER`, §13.2).
- Fail closed (§14):
  - a start that fails re-verification makes **no external call**;
  - an execution that started but has no outcome after a restart escalates as `ExecutionUnknown` and is **never replayed**.
- `TOOL_EXECUTION_STARTED` and `AUDIT_RECORDED` are committed together, in one transaction, both checked by the Temporal Monitor. `attempt_count` is incremented in that same transaction, before the call.
- The external call carries only the fields in spec §11 `minimized_fields`.
- Names come from the closed lists in `naming.py` (§2, §5, §17). Never invent a State, Event, Action or escalation kind.
- The six approved design decisions (design §8):
  1. migration 0002;
  2. a re-verification failure escalates as `ExecutionUnknown`;
  3. all four automatic actions are idempotent;
  4. the pair rows are `ExecutionStarted`, and outcome rows are outside the temporal trace;
  5. the demo data;
  6. `obs.golden` uses the test database.
- All files use LF line endings. Commit after every task. End each commit message with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`, exactly that line.

## Refinements of the design found while prototyping

1. **The outcome is recorded before the guards run.** `StateManager.apply(..., execution_outcome=…)` writes the outcome row and moves the `executions` row to its final status *before* it evaluates the transition's guards. That way `DeliveryConfirmed` sees `succeeded` on the `CASE_RESOLVED` that follows a successful delivery. An outcome for an execution that isn't `started` raises `ExecutionStateError`, and nothing is written.
2. **A replayed decision gets only a Blocked row.** If `start_execution` fails re-verification while the case is no longer in `RetrievingData` / `Delivering`, the executor does not escalate. `ExecutionUnknown` is not in those states' allowlists, so the escalation would only be blocked too. The Blocked `TOOL_EXECUTION_STARTED` row is the whole answer. A drift *inside* an executing state still escalates as `ExecutionUnknown` (decision 2).
3. **`SlaWorker.tick()` reads the State Manager's clock.** It does not take a `now` parameter, so the SLA Worker, the guards and the Readiness Check always agree on "now". Tests set `sm.clock`.
4. **Startup lives in `execution/background.py`.** `start_background(sm, interval)` runs recovery synchronously, then starts the SLA thread. The app calls it only when it owns its engine (a real server, not a test). The interval comes from `SLA_INTERVAL_SECONDS`, default 30.
5. **The idempotency key is `"{case_id}:{step}:{retry_cycle}:{attempt_number}"`,** where `attempt_number = attempt_count + 1` when the decision is accepted. The `executions.idempotency_key` UNIQUE constraint therefore rejects a second decision for the same attempt.
6. **Task 4 edits `tests/test_scenarios.py` and `tests/driver.py` temporarily.** Once decisions write intent rows, the Core-era scenario 3 must start each attempt through the State Manager, or two decisions for the same attempt collide on the idempotency key. Task 5 replaces the driver and Task 7 replaces the scenario test.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `backend/alembic/versions/0002_execution_binding.py`, `db.py`, `case.py`, `repository.py` | schema and repository | 1 |
| `execution/__init__.py`, `execution/gateway.py`, `execution/retry.py` | mock external systems, Retry Manager | 2 |
| `execution/verify.py` | `ExecutorReverified` (both checks) | 3 |
| `fsm.py`, `guards.py`, `state_manager.py`, `escalation.py`, `wiring.py`, `policy/service.py`, `policy/temporal.py`, `policy/readiness.py` | the State Manager's execution writes; decisions bound to State | 4 |
| `execution/executor.py`, `scripted.py`, `tests/driver.py` | Tool Executor; scripted demo components | 5 |
| `execution/sla.py`, `execution/recovery.py`, `execution/background.py`, `api/app.py` | SLA Worker, recovery, startup | 6 |
| `backend/obs/__init__.py`, `backend/obs/golden.py`, `tests/test_scenarios.py` | golden traces | 7 |
| `docs/spec_corrections.md`, `CLAUDE.md` | decisions 19–24, hand-off to sub-project 4 | 8 |

(Paths without a prefix are under `backend/hospital_agent/`.)

---

### Task 1: Migration 0002 and the repository

**Files:**
- Create: `backend/alembic/versions/0002_execution_binding.py`
- Modify (full new content below): `backend/hospital_agent/db.py`, `backend/hospital_agent/case.py`, `backend/hospital_agent/repository.py`
- Test: `backend/tests/test_execution_repository.py`

**Interfaces:**
- Produces:
  - `CaseRecord.appointment_at: datetime | None`.
  - `ExecutionRecord` gains `state_version: int | None`, `plan_hash: str | None`, `approval_id: str | None`, `content_hash: str | None` and `medical_content_flag: bool = False`.
  - `repository.set_execution_status(conn, execution_id, from_statuses: tuple[str, ...], to_status, now) -> int` returns the number of rows updated. It stamps `started_at` for `started` and `finished_at` for the final statuses.
  - `repository.executions_with_status(conn, status) -> list[ExecutionRecord]`.
  - `repository.expired_patient_deadlines(conn, now) -> list[CaseRecord]`.

- [ ] **Step 1: Write the failing test** `backend/tests/test_execution_repository.py`:

```python
"""Migration 0002's columns and the repository functions the execution layer uses."""
from datetime import UTC, datetime, timedelta

from hospital_agent import repository
from hospital_agent.case import CaseRecord, ExecutionRecord
from hospital_agent.naming import State

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _case(case_id: str, state: State = State.AWAITING_PATIENT_INPUT, **fields) -> CaseRecord:
    return CaseRecord(case_id=case_id, patient_id="P-10041", state=state, state_version=1,
                      created_at=NOW, updated_at=NOW, **fields)


def _execution(execution_id: str, status: str = "intent") -> ExecutionRecord:
    return ExecutionRecord(execution_id=execution_id, case_id="CASE-1", patient_id="P-10041", action="CheckDocuments",
                           step=2, retry_cycle=0, attempt_number=1, idempotency_key=f"CASE-1:2:0:{execution_id}",
                           status=status, decision_token="tok", state_version=5, plan_hash="h" * 64,
                           approval_id="APPR-1", content_hash="HASH-1", medical_content_flag=True)


def test_new_columns_round_trip(app_engine):
    with app_engine.begin() as conn:
        repository.insert_case(conn, _case("CASE-1", appointment_at=NOW + timedelta(hours=96)))
        repository.insert_execution(conn, _execution("EXEC-1"))
    with app_engine.connect() as conn:
        assert repository.load_case(conn, "CASE-1").appointment_at == NOW + timedelta(hours=96)
        assert repository.load_execution(conn, "EXEC-1") == _execution("EXEC-1")


def test_set_execution_status_moves_only_from_the_expected_status(app_engine):
    with app_engine.begin() as conn:
        repository.insert_case(conn, _case("CASE-1"))
        repository.insert_execution(conn, _execution("EXEC-1"))
        assert repository.set_execution_status(conn, "EXEC-1", ("intent",), "started", NOW) == 1
        assert repository.set_execution_status(conn, "EXEC-1", ("intent",), "started", NOW) == 0
        assert repository.set_execution_status(conn, "EXEC-1", ("started",), "succeeded", NOW) == 1
    with app_engine.connect() as conn:
        row = repository.load_execution(conn, "EXEC-1")
        assert (row.status, row.started_at, row.finished_at) == ("succeeded", NOW, NOW)
        assert repository.executions_with_status(conn, "succeeded") == [row]
        assert repository.executions_with_status(conn, "started") == []


def test_expired_patient_deadlines(app_engine):
    with app_engine.begin() as conn:
        repository.insert_case(conn, _case("CASE-PAST", patient_deadline=NOW - timedelta(minutes=1)))
        repository.insert_case(conn, _case("CASE-FUTURE", patient_deadline=NOW + timedelta(minutes=1)))
        repository.insert_case(conn, _case("CASE-OTHER", State.PLANNING, patient_deadline=NOW - timedelta(hours=1)))
    with app_engine.connect() as conn:
        assert [case.case_id for case in repository.expired_patient_deadlines(conn, NOW)] == ["CASE-PAST"]
```

- [ ] **Step 2: Run it and see it fail**

Run: `docker compose run --rm backend pytest tests/test_execution_repository.py -v`
Expected: FAIL. `CaseRecord` / `ExecutionRecord` reject the new keyword arguments (`TypeError: … unexpected keyword argument 'appointment_at'`).

- [ ] **Step 3: Create the migration** `backend/alembic/versions/0002_execution_binding.py`:

```python
"""Execution binding (Execution design decision 1; an addition to spec §18.2).

executions gains what a Policy decision was bound to, so the Tool Executor can re-verify
it right before the call (ExecutorReverified, §3.1): state_version, plan_hash, the
ContentApproval it relies on (approval_id, content_hash) and medical_content_flag.
cases gains appointment_at, the time CheckAppointment returned; the Readiness Check
derives hours_until from it (§9.1). Existing grants on both tables cover new columns.

Revision ID: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("state_version", sa.Integer))
    op.add_column("executions", sa.Column("plan_hash", sa.Text))
    op.add_column("executions", sa.Column("approval_id", sa.Text))
    op.add_column("executions", sa.Column("content_hash", sa.Text))
    op.add_column("executions", sa.Column("medical_content_flag", sa.Boolean, nullable=False,
                                          server_default=sa.false()))
    op.add_column("cases", sa.Column("appointment_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("cases", "appointment_at")
    for column in ("medical_content_flag", "content_hash", "approval_id", "plan_hash", "state_version"):
        op.drop_column("executions", column)
```

- [ ] **Step 4: Replace** `backend/hospital_agent/db.py` with:

```python
"""Postgres schema (spec §18.2) as SQLAlchemy Core tables, and engine creation.

alembic/versions/ holds the migrations that create these tables; this module
mirrors them for queries. tests/test_schema.py fails if the two drift apart.
"""
from __future__ import annotations

import os

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

metadata = MetaData()

cases = Table(
    "cases",
    metadata,
    Column("case_id", Text, primary_key=True),
    Column("patient_id", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("state_version", Integer, nullable=False),
    Column("intent", Text),
    Column("safety_level", Text),
    Column("identity_verified", Boolean, nullable=False),
    Column("plan_hash", Text),
    Column("ordered_steps", JSONB),
    Column("current_step", Integer),
    Column("retry_cycle", Integer, nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("required_documents", JSONB),
    Column("held_documents", JSONB, nullable=False),
    Column("escalation_kind", Text),
    Column("escalated_from_state", Text),
    Column("patient_deadline", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("appointment_at", DateTime(timezone=True)),  # migration 0002
)

executions = Table(
    "executions",
    metadata,
    Column("execution_id", Text, primary_key=True),
    Column("case_id", Text, ForeignKey("cases.case_id"), nullable=False),
    Column("patient_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("step", Integer, nullable=False),
    Column("retry_cycle", Integer, nullable=False),
    Column("attempt_number", Integer, nullable=False),
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column("decision_token", Text),
    Column("status", Text, nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    # migration 0002: what the Policy decision was bound to (ExecutorReverified)
    Column("state_version", Integer),
    Column("plan_hash", Text),
    Column("approval_id", Text),
    Column("content_hash", Text),
    Column("medical_content_flag", Boolean, nullable=False),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("audit_id", BigInteger, Identity(), primary_key=True),
    Column("case_id", Text, ForeignKey("cases.case_id"), nullable=False),
    Column("patient_id", Text, nullable=False),
    Column("execution_id", Text),
    Column("record_type", Text, nullable=False),
    Column("event", Text, nullable=False),
    Column("action", Text),
    Column("state_before", Text),
    Column("state_after", Text),
    Column("guards", JSONB, nullable=False),
    Column("policy_result", Text),
    Column("policy_reasons", JSONB, nullable=False),
    Column("attempt_number", Integer),
    Column("retry_cycle", Integer),
    Column("outcome", Text),
    Column("approval_id", Text),
    Column("content_hash", Text),
    Column("rule_version", Text, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)

approvals = Table(
    "approvals",
    metadata,
    Column("approval_id", Text, primary_key=True),
    Column("approval_type", Text, nullable=False),
    Column("case_id", Text, ForeignKey("cases.case_id"), nullable=False),
    Column("patient_id", Text, nullable=False),
    Column("execution_id", Text),
    Column("action", Text),
    Column("content_hash", Text),
    Column("reviewer_id", Text, nullable=False),
    Column("reviewer_role", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("escalation_kind", Text),
    Column("plan_hash", Text),
    Column("current_step", Integer),
    Column("verified_identity_ref", Text),
    Column("patient_deadline", DateTime(timezone=True)),
    Column("shown_context_ref", Text, nullable=False),
    Column("granted_at", DateTime(timezone=True), nullable=False),
    Column("valid_until", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True)),
)


def make_engine(url: str | None = None) -> Engine:
    """Engine for the application role (hospital_app). Defaults to $DATABASE_URL."""
    return create_engine(url or os.environ["DATABASE_URL"], pool_pre_ping=True)
```

- [ ] **Step 5: Replace** `backend/hospital_agent/case.py` with:

```python
"""Records that mirror the Postgres rows the Core reads (spec §18.2), and plan hashing.

The cases row is the ONLY place a case's current State lives (design §3): nothing
is kept in memory between requests, so a restart loses nothing.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .naming import Action, EscalationKind, SafetyLevel, State

# §0 scenario 3, §12.1: automatic attempts per step, per retry_cycle.
MAX_ATTEMPTS = 3


def compute_plan_hash(ordered_steps: list[dict[str, Any]]) -> str:
    """sha256 of canonical JSON (sorted keys, no spaces).

    Identical to OPA's crypto.sha256(json.marshal(ordered_steps)); reproduces the
    plan_hash of the spec §8 example input.
    """
    canonical = json.dumps(ordered_steps, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CaseRecord:
    """One row of the cases table."""

    case_id: str
    patient_id: str
    state: State
    state_version: int
    created_at: datetime
    updated_at: datetime
    intent: str | None = None
    safety_level: SafetyLevel | None = None
    identity_verified: bool = False
    plan_hash: str | None = None
    ordered_steps: list[dict[str, Any]] | None = None
    current_step: int | None = None
    retry_cycle: int = 0
    attempt_count: int = 0
    required_documents: list[str] | None = None
    held_documents: list[str] = field(default_factory=list)
    escalation_kind: EscalationKind | None = None
    escalated_from_state: State | None = None
    patient_deadline: datetime | None = None
    appointment_at: datetime | None = None  # from CheckAppointment's result (Execution design §5)

    def step_action(self, step: int | None) -> Action | None:
        """The action at 1-based `step` of the approved plan, or None outside the plan."""
        if self.ordered_steps is None or step is None or not 1 <= step <= len(self.ordered_steps):
            return None
        return Action(self.ordered_steps[step - 1]["action"])

    @property
    def readiness_complete(self) -> bool:
        """Every required document is held - computed from tool results, never declared (§3.1)."""
        return self.required_documents is not None and set(self.required_documents) <= set(self.held_documents)

    @property
    def current_action(self) -> Action | None:
        return self.step_action(self.current_step)

    @property
    def next_action(self) -> Action | None:
        return None if self.current_step is None else self.step_action(self.current_step + 1)


def new_case(case_id: str, patient_id: str, now: datetime) -> CaseRecord:
    """The row REQUEST_SUBMITTED creates (§3 Initial -> Received)."""
    return CaseRecord(
        case_id=case_id,
        patient_id=patient_id,
        state=State.RECEIVED,
        state_version=1,
        created_at=now,
        updated_at=now,
    )


@dataclass(frozen=True)
class ApprovalRecord:
    """One row of the approvals table (§12.5)."""

    approval_id: str
    approval_type: str
    case_id: str
    patient_id: str
    reviewer_id: str
    reviewer_role: str
    decision: str
    reason: str
    shown_context_ref: str
    granted_at: datetime
    valid_until: datetime
    execution_id: str | None = None
    action: str | None = None
    content_hash: str | None = None
    escalation_kind: str | None = None
    plan_hash: str | None = None
    current_step: int | None = None
    verified_identity_ref: str | None = None
    patient_deadline: datetime | None = None
    consumed_at: datetime | None = None


@dataclass(frozen=True)
class ExecutionRecord:
    """One row of the executions table (§18.2). The Core only reads it (DeliveryConfirmed)."""

    execution_id: str
    case_id: str
    patient_id: str
    action: str
    step: int
    retry_cycle: int
    attempt_number: int
    idempotency_key: str
    status: str
    decision_token: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # what the Policy decision was bound to (Execution design §3.1)
    state_version: int | None = None
    plan_hash: str | None = None
    approval_id: str | None = None
    content_hash: str | None = None
    medical_content_flag: bool = False
```

- [ ] **Step 6: Replace** `backend/hospital_agent/repository.py` with:

```python
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
        appointment_at=row["appointment_at"],
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
        "appointment_at": case.appointment_at,
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


def confirm_version(conn: Connection, case_id: str, expected_version: int) -> int:
    """A no-op conditional UPDATE that also locks the row (F4): 0 means the case moved on.

    Used before writing a Blocked row for an existing case, so a verdict based on a
    stale in-memory snapshot is not committed to audit_log. Deliberately an UPDATE, not
    SELECT ... FOR UPDATE, so it cannot deadlock against a concurrent transaction doing
    the same (see the D15 barrier test).
    """
    result = conn.execute(
        update(cases)
        .where(cases.c.case_id == case_id, cases.c.state_version == expected_version)
        .values(state_version=cases.c.state_version)
    )
    return result.rowcount


def approval_used(conn: Connection, approval_id: str) -> bool:
    """True if approval_id already appears on a committed Transition audit row (F1: replay guard)."""
    row = conn.execute(
        select(audit_log.c.audit_id)
        .where(audit_log.c.approval_id == approval_id, audit_log.c.record_type == "Transition")
        .limit(1)
    ).first()
    return row is not None


def open_policy_review_override(
    conn: Connection, case_id: str, plan_hash: str | None, current_step: int | None
) -> ApprovalRecord | None:
    """The approval the case's latest committed HUMAN_APPROVED used, if it still is a valid
    open PolicyReview override for this plan step (§3.1 PolicyReviewOverrideValid).

    F1: this must NOT be "any unconsumed PolicyReview approval for the step" - a double-submit
    can leave two such approvals open, and only the one that actually resumed the case (the
    approval_id on its latest committed HUMAN_APPROVED audit row) is a valid override. OPA
    judges its validity; this only finds the candidate the Policy Service presents.
    """
    latest = conn.execute(
        select(audit_log.c.approval_id)
        .where(
            audit_log.c.case_id == case_id,
            audit_log.c.record_type == "Transition",
            audit_log.c.event == "HUMAN_APPROVED",
        )
        .order_by(audit_log.c.audit_id.desc())
        .limit(1)
    ).first()
    if latest is None or latest.approval_id is None:
        return None
    approval = load_approval(conn, latest.approval_id)
    if (
        approval is None
        or approval.approval_type != "WorkflowDecision"
        or approval.escalation_kind != "PolicyReview"
        or approval.decision != "approve"
        or approval.consumed_at is not None
        or approval.plan_hash != plan_hash
        or approval.current_step != current_step
    ):
        return None
    return approval


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


def set_execution_status(
    conn: Connection, execution_id: str, from_statuses: tuple[str, ...], to_status: str, now: datetime
) -> int:
    """Move an execution along intent -> started -> succeeded|failed|unknown (§18.2).

    Returns 0 if the row is not in one of `from_statuses` (it moved on, or never existed).
    started_at is stamped on entering 'started'; finished_at on any final status.
    """
    stamp = {"started_at": now} if to_status == "started" else {"finished_at": now}
    result = conn.execute(
        update(executions)
        .where(executions.c.execution_id == execution_id, executions.c.status.in_(from_statuses))
        .values(status=to_status, **stamp)
    )
    return result.rowcount


def executions_with_status(conn: Connection, status: str) -> list[ExecutionRecord]:
    rows = conn.execute(
        select(executions).where(executions.c.status == status).order_by(executions.c.started_at)
    ).mappings()
    return [ExecutionRecord(**dict(row)) for row in rows]


def expired_patient_deadlines(conn: Connection, now: datetime) -> list[CaseRecord]:
    """Cases waiting for the patient whose deadline has passed - the SLA Worker's scan (§18.2 index)."""
    rows = conn.execute(
        select(cases)
        .where(cases.c.state == State.AWAITING_PATIENT_INPUT.value, cases.c.patient_deadline <= now)
        .order_by(cases.c.patient_deadline)
    ).mappings()
    return [_case_from_row(row) for row in rows]
```

- [ ] **Step 7: Run the new test and the schema test**

Run: `docker compose run --rm backend pytest tests/test_execution_repository.py tests/test_schema.py -v`
Expected: all PASS.

- [ ] **Step 8: Run the whole suite**

Run: `docker compose run --rm backend pytest -q`
Expected: `295 passed`.

- [ ] **Step 9: Commit**

```bash
git add backend/alembic/versions/0002_execution_binding.py backend/hospital_agent/db.py backend/hospital_agent/case.py backend/hospital_agent/repository.py backend/tests/test_execution_repository.py
git commit -m "Add migration 0002: bind executions to the Policy decision, store appointment_at

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Mock external systems and the Retry Manager

**Files:**
- Create: `backend/hospital_agent/execution/__init__.py` (empty), `backend/hospital_agent/execution/gateway.py`, `backend/hospital_agent/execution/retry.py`
- Test: `backend/tests/test_gateway.py`

**Interfaces:**
- Consumes: `naming.AUTOMATIC_ACTIONS`, `naming.Action`, `case.MAX_ATTEMPTS`, `policy.build_minimized.OUTPUT` (the path of `minimized_fields.json`).
- Produces:
  - `gateway.IDEMPOTENT_ACTIONS: frozenset[str]`.
  - `gateway.ACTION_TARGETS: dict[str, tuple[str, tuple[str, ...]]]`, mapping action → (target system, patient fields).
  - `gateway.OK`, `gateway.TRANSIENT_FAILURE`, `gateway.ERROR`.
  - `gateway.ToolResult(kind, data)`.
  - The `gateway.ToolGateway` protocol: `call(action, parameters, idempotency_key) -> ToolResult` and `idempotent(action) -> bool`.
  - `gateway.MockGateway(*, clock, hours_until_appointment=96, required_documents, held_documents, failures, errors, non_idempotent)`, with `.calls` and `.delivered`.
  - `retry.RetryVerdict(event, escalation)`.
  - `retry.after_failure(case, *, idempotent, transient) -> RetryVerdict`.

- [ ] **Step 1: Write the failing test** `backend/tests/test_gateway.py`:

```python
"""The mock external systems and the Retry Manager (Execution design §4)."""
import json
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent.case import CaseRecord
from hospital_agent.execution.gateway import (
    ACTION_TARGETS, ERROR, IDEMPOTENT_ACTIONS, OK, TRANSIENT_FAILURE, MockGateway,
)
from hospital_agent.execution.retry import after_failure
from hospital_agent.naming import AUTOMATIC_ACTIONS, EscalationKind, Event, State
from hospital_agent.policy.build_minimized import OUTPUT as MINIMIZED_FIELDS

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


# --- gateway -------------------------------------------------------------------------------

def test_every_automatic_action_has_a_target_and_is_idempotent():
    assert set(ACTION_TARGETS) == {a.value for a in AUTOMATIC_ACTIONS} == IDEMPOTENT_ACTIONS


def test_action_parameters_are_within_the_minimized_fields():
    allowed = json.loads(MINIMIZED_FIELDS.read_text())["hospital_agent"]["minimized_fields"]
    for target, fields in ACTION_TARGETS.values():
        assert set(fields) <= set(allowed[target]), target


def test_mock_returns_the_demo_data():
    gw = MockGateway(clock=lambda: NOW)
    assert gw.call("CheckAppointment", {"patient_id": "P"}, "k1") == \
        type(gw.call("CheckAppointment", {}, "k0"))(OK, {"appointment_at": NOW + timedelta(hours=96)})
    docs = gw.call("CheckDocuments", {"patient_id": "P"}, "k2")
    assert docs.data == {"required_documents": ["referral", "blood_test"], "held_documents": ["referral"]}
    assert gw.call("LoadInstructions", {}, "k3").data == {"instruction_ids": ["INSTR-PREP-COLONOSCOPY:3"]}


def test_mock_fails_as_scripted_then_recovers():
    gw = MockGateway(failures={"CheckDocuments": 2}, errors=frozenset({"LoadInstructions"}))
    kinds = [gw.call("CheckDocuments", {}, f"k{i}").kind for i in range(3)]
    assert kinds == [TRANSIENT_FAILURE, TRANSIENT_FAILURE, OK]
    assert gw.call("LoadInstructions", {}, "k").kind == ERROR
    assert [call[0] for call in gw.calls] == ["CheckDocuments"] * 3 + ["LoadInstructions"]


def test_patient_channel_ignores_a_repeated_idempotency_key():
    gw = MockGateway()
    gw.call("SendStatusUpdate", {"patient_id": "P", "content_hash": "H1"}, "same")
    gw.call("SendStatusUpdate", {"patient_id": "P", "content_hash": "H2"}, "same")
    assert gw.delivered == {"same": {"patient_id": "P", "content_hash": "H1"}}


def test_non_idempotent_can_be_scripted():
    gw = MockGateway(non_idempotent=frozenset({"CheckDocuments"}))
    assert not gw.idempotent("CheckDocuments") and gw.idempotent("CheckAppointment")


# --- Retry Manager -------------------------------------------------------------------------

def _case(attempt_count: int) -> CaseRecord:
    return CaseRecord(case_id="CASE-1", patient_id="P-10041", state=State.RETRIEVING_DATA, state_version=7,
                      created_at=NOW, updated_at=NOW, current_step=2, attempt_count=attempt_count)


@pytest.mark.parametrize("attempts, idempotent, transient, expected", [
    (1, True, True, Event.TOOL_TRANSIENT_FAILURE),
    (2, True, True, Event.TOOL_TRANSIENT_FAILURE),
    (3, True, True, Event.RETRY_EXHAUSTED),
    (1, False, True, EscalationKind.NON_IDEMPOTENT_FAILURE),   # D27
    (1, True, False, EscalationKind.NON_IDEMPOTENT_FAILURE),   # an error is not retried
])
def test_retry_verdict(attempts, idempotent, transient, expected):
    verdict = after_failure(_case(attempts), idempotent=idempotent, transient=transient)
    assert (verdict.event or verdict.escalation) is expected
```

- [ ] **Step 2: Run it and see it fail**

Run: `docker compose run --rm backend pytest tests/test_gateway.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'hospital_agent.execution'`.

- [ ] **Step 3: Create** an empty `backend/hospital_agent/execution/__init__.py`, then `backend/hospital_agent/execution/gateway.py`:

```python
"""The external systems the Tool Executor calls, and their demo mocks (Execution design §4).

Only the fields a target may receive are sent (spec §11 minimized_fields): the
parameters of each action are fixed here, and a test checks them against the
Datalog export. The mocks return the demo data of Execution design decision 5 and
can be scripted to time out or fail, so the three scenarios and the D-tests only
change the mock's script (spec §0: "רק קלט המטופל ותגובות ה־Mock משתנים").
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..naming import AUTOMATIC_ACTIONS, Action

# Execution design decision 3: every automatic action is idempotent; the patient channel
# ignores a repeated idempotency_key.
IDEMPOTENT_ACTIONS = frozenset(a.value for a in AUTOMATIC_ACTIONS)

# action -> (target_system, the patient fields it receives)
ACTION_TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    Action.CHECK_APPOINTMENT.value: ("appointment_system", ("patient_id",)),
    Action.CHECK_DOCUMENTS.value: ("document_system", ("patient_id",)),
    Action.LOAD_INSTRUCTIONS.value: ("instruction_system", ()),
    Action.SEND_STATUS_UPDATE.value: ("patient_channel", ("patient_id",)),
}

OK, TRANSIENT_FAILURE, ERROR = "ok", "transient_failure", "error"


@dataclass(frozen=True)
class ToolResult:
    kind: str  # ok | transient_failure | error
    data: dict[str, Any] = field(default_factory=dict)


class ToolGateway(Protocol):
    def call(self, action: str, parameters: Mapping[str, Any], idempotency_key: str) -> ToolResult: ...

    def idempotent(self, action: str) -> bool: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MockGateway:
    """Deterministic demo systems: a colonoscopy appointment, a referral already held, a
    blood test still missing, the approved preparation instructions, and a patient channel."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _utcnow,
        hours_until_appointment: float = 96,
        required_documents: tuple[str, ...] = ("referral", "blood_test"),
        held_documents: tuple[str, ...] = ("referral",),
        failures: Mapping[str, int] | None = None,
        errors: frozenset[str] = frozenset(),
        non_idempotent: frozenset[str] = frozenset(),
    ) -> None:
        self.clock = clock
        self.hours_until_appointment = hours_until_appointment
        self.required_documents, self.held_documents = required_documents, held_documents
        self.failures = dict(failures or {})
        self.errors, self.non_idempotent = errors, non_idempotent
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self.delivered: dict[str, dict[str, Any]] = {}  # idempotency_key -> message (deduplicated)

    def idempotent(self, action: str) -> bool:
        return action in IDEMPOTENT_ACTIONS and action not in self.non_idempotent

    def call(self, action: str, parameters: Mapping[str, Any], idempotency_key: str) -> ToolResult:
        self.calls.append((action, dict(parameters), idempotency_key))
        if self.failures.get(action, 0) > 0:
            self.failures[action] -= 1
            return ToolResult(TRANSIENT_FAILURE, {"error": "timeout"})
        if action in self.errors:
            return ToolResult(ERROR, {"error": "rejected"})
        match action:
            case Action.CHECK_APPOINTMENT.value:
                at = self.clock() + timedelta(hours=self.hours_until_appointment)
                return ToolResult(OK, {"appointment_at": at})
            case Action.CHECK_DOCUMENTS.value:
                return ToolResult(OK, {"required_documents": list(self.required_documents),
                                       "held_documents": list(self.held_documents)})
            case Action.LOAD_INSTRUCTIONS.value:
                return ToolResult(OK, {"instruction_ids": ["INSTR-PREP-COLONOSCOPY:3"]})
            case Action.SEND_STATUS_UPDATE.value:
                self.delivered.setdefault(idempotency_key, dict(parameters))
                return ToolResult(OK, {"delivered": True})
        return ToolResult(ERROR, {"error": f"unknown action {action}"})
```

- [ ] **Step 4: Create** `backend/hospital_agent/execution/retry.py`:

```python
"""Retry Manager - what follows a failed call (spec §3, §12.1, §14; Execution design §4).

attempt_count has already been incremented when the call started, so after the 3rd
failed attempt of a step and cycle it equals MAX_ATTEMPTS:

    error, or a non-idempotent action     -> escalate NonIdempotentFailure (no automatic retry)
    attempt_count >= MAX_ATTEMPTS         -> RETRY_EXHAUSTED (a human decides)
    otherwise                             -> TOOL_TRANSIENT_FAILURE (back to Planning, re-proposed)
"""
from __future__ import annotations

from dataclasses import dataclass

from ..case import MAX_ATTEMPTS, CaseRecord
from ..naming import EscalationKind, Event


@dataclass(frozen=True)
class RetryVerdict:
    event: Event | None = None
    escalation: EscalationKind | None = None


def after_failure(case: CaseRecord, *, idempotent: bool, transient: bool) -> RetryVerdict:
    if not transient or not idempotent:
        return RetryVerdict(escalation=EscalationKind.NON_IDEMPOTENT_FAILURE)
    if case.attempt_count >= MAX_ATTEMPTS:
        return RetryVerdict(event=Event.RETRY_EXHAUSTED)
    return RetryVerdict(event=Event.TOOL_TRANSIENT_FAILURE)
```

- [ ] **Step 5: Run the test**

Run: `docker compose run --rm backend pytest tests/test_gateway.py -v`
Expected: all PASS. The whole suite (`pytest -q`) gives `306 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/hospital_agent/execution/__init__.py backend/hospital_agent/execution/gateway.py backend/hospital_agent/execution/retry.py backend/tests/test_gateway.py
git commit -m "Add the mock external systems and the Retry Manager

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: ExecutorReverified

**Files:**
- Create: `backend/hospital_agent/execution/verify.py`
- Test: `backend/tests/test_verify.py`

**Interfaces:**
- Consumes: `case.compute_plan_hash`, `guards.GuardContext`, `policy.approvals.content_approval_valid` (keyword arguments `case_id, patient_id, execution_id, action, content_hash, now`).
- Produces:
  - `verify.REVERIFICATION_FAILED = "executor_reverification_failed"` and `verify.EXECUTING_STATES`.
  - `verify.verify_decision(ctx: GuardContext) -> bool`: the `GuardPorts.executor_reverified` port, evaluated on `POLICY_ALLOWED`.
  - `verify.verify_start(case, execution, approval, now) -> str | None`: `None` means the call may go out.

- [ ] **Step 1: Write the failing test** `backend/tests/test_verify.py`:

```python
"""verify_start - the Tool Executor's re-verification right before a call (spec §3.1 ExecutorReverified)."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from hospital_agent.case import CaseRecord, ExecutionRecord, compute_plan_hash
from hospital_agent.execution.verify import REVERIFICATION_FAILED, verify_start
from hospital_agent.naming import State

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
PLAN = [
    {"step": 1, "action": "CheckAppointment"},
    {"step": 2, "action": "CheckDocuments"},
    {"step": 3, "action": "LoadInstructions"},
    {"step": 4, "action": "SendStatusUpdate"},
]


def _case(**changes) -> CaseRecord:
    base = CaseRecord(case_id="CASE-1", patient_id="P-10041", state=State.RETRIEVING_DATA, state_version=7,
                      identity_verified=True, ordered_steps=PLAN, plan_hash=compute_plan_hash(PLAN),
                      current_step=2, attempt_count=1, created_at=NOW, updated_at=NOW)
    return replace(base, **changes)


def _execution(**changes) -> ExecutionRecord:
    base = ExecutionRecord(execution_id="EXEC-1", case_id="CASE-1", patient_id="P-10041", action="CheckDocuments",
                           step=2, retry_cycle=0, attempt_number=2, idempotency_key="CASE-1:2:0:2", status="intent",
                           decision_token="tok", state_version=7, plan_hash=compute_plan_hash(PLAN))
    return replace(base, **changes)


def test_verify_start_accepts_an_untouched_decision():
    assert verify_start(_case(), _execution(), None, NOW) is None


@pytest.mark.parametrize("case_change, execution_change", [
    ({}, {"status": "started"}),                       # already started - never replayed
    ({"state_version": 8}, {}),                        # the case moved on after the decision
    ({"current_step": 3}, {}),
    ({"state": State.PLANNING}, {}),
    ({"identity_verified": False}, {}),
    ({}, {"plan_hash": "0" * 64}),
    ({}, {"action": "LoadInstructions"}),
    ({}, {"patient_id": "P-OTHER"}),
    ({}, {"decision_token": ""}),
    ({}, {"idempotency_key": " "}),
])
def test_verify_start_rejects_any_drift(case_change, execution_change):
    assert verify_start(_case(**case_change), _execution(**execution_change), None, NOW) == REVERIFICATION_FAILED


def test_verify_start_rejects_a_missing_row_and_an_unapproved_medical_message():
    assert verify_start(_case(), None, None, NOW) == REVERIFICATION_FAILED
    medical = _execution(medical_content_flag=True, approval_id="APPR-X", content_hash="H")
    assert verify_start(_case(), medical, None, NOW) is not None
```

- [ ] **Step 2: Run it and see it fail**

Run: `docker compose run --rm backend pytest tests/test_verify.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'hospital_agent.execution.verify'`.

- [ ] **Step 3: Create** `backend/hospital_agent/execution/verify.py`:

```python
"""ExecutorReverified (spec §3.1) - the Tool Executor's two checks of a Policy decision.

1. verify_decision(ctx): the GuardPorts.executor_reverified port, evaluated on the
   POLICY_ALLOWED transition. The decision must have been computed on exactly the
   State the case is in now (decided_state_version, plan_hash, current_step, action),
   and its execution_id must be new.
2. verify_start(case, execution, approval, now): right before the call, inside the
   ExecutionStarted transaction. The executions row written with POLICY_ALLOWED must
   still match the case - nothing may have changed since the decision was accepted -
   and a medical output needs its ContentApproval to be valid still.

Both are pure: they read only what they are given (Execution design §3).
"""
from __future__ import annotations

from datetime import datetime

from ..case import ApprovalRecord, CaseRecord, ExecutionRecord, compute_plan_hash
from ..guards import GuardContext
from ..naming import State
from ..policy.approvals import content_approval_valid

REVERIFICATION_FAILED = "executor_reverification_failed"
EXECUTING_STATES = frozenset({State.RETRIEVING_DATA, State.DELIVERING})


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _plan_matches(case: CaseRecord, plan_hash: object) -> bool:
    return (case.ordered_steps is not None and plan_hash == case.plan_hash
            and case.plan_hash == compute_plan_hash(case.ordered_steps))


def verify_decision(ctx: GuardContext) -> bool:
    case, p = ctx.case, ctx.payload
    return (
        case is not None
        and _nonempty(p.get("decision_token"))
        and _nonempty(p.get("execution_id"))
        and ctx.execution is None  # the execution_id has never been used
        and p.get("decided_state_version") == case.state_version
        and _plan_matches(case, p.get("plan_hash"))
        and p.get("current_step") == case.current_step
        and case.current_action is not None
        and p.get("action") == case.current_action.value
    )


def verify_start(
    case: CaseRecord, execution: ExecutionRecord | None, approval: ApprovalRecord | None, now: datetime
) -> str | None:
    """None when the call may go out, otherwise the anomaly reason (no external call, §14)."""
    valid = (
        execution is not None
        and execution.status == "intent"
        and case.state in EXECUTING_STATES
        and execution.case_id == case.case_id
        and execution.patient_id == case.patient_id
        and case.identity_verified
        and _nonempty(case.case_id) and _nonempty(case.patient_id) and _nonempty(execution.execution_id)
        and _nonempty(execution.decision_token)
        and _nonempty(execution.idempotency_key)
        and execution.state_version == case.state_version
        and _plan_matches(case, execution.plan_hash)
        and execution.step == case.current_step
        and case.current_action is not None
        and execution.action == case.current_action.value
    )
    if not valid:
        return REVERIFICATION_FAILED
    if execution.medical_content_flag and not content_approval_valid(
        approval,
        case_id=case.case_id,
        patient_id=case.patient_id,
        execution_id=execution.execution_id,
        action=execution.action,
        content_hash=execution.content_hash,
        now=now,
    ):
        return REVERIFICATION_FAILED
    return None
```

- [ ] **Step 4: Run the test**

Run: `docker compose run --rm backend pytest tests/test_verify.py -v`
Expected: all PASS. The whole suite gives `318 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/hospital_agent/execution/verify.py backend/tests/test_verify.py
git commit -m "Add ExecutorReverified: decision and start re-verification

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The State Manager's execution writes

This task switches the production State Manager (and the tests' `sm` fixture) to the real `ExecutorReverified`. From here on, every Policy decision must carry `decided_state_version`, `plan_hash` and `current_step`.

**Files:**
- Modify (full new content below):
  - `backend/hospital_agent/fsm.py`, `guards.py`, `state_manager.py`, `escalation.py`, `wiring.py`;
  - `backend/hospital_agent/policy/service.py`, `policy/temporal.py`, `policy/readiness.py`;
  - `backend/tests/conftest.py`, `tests/test_policy_core.py`, `tests/test_policy_d_tests.py`.
- Modify (edits below): `backend/tests/driver.py`, `backend/tests/test_scenarios.py`.
- Test: `backend/tests/test_execution_core.py`.

**Interfaces:**
- Consumes: Task 1 repository functions and fields; Task 3 `verify_decision`, `verify_start`, `REVERIFICATION_FAILED`.
- Produces:
  - `fsm.Effect.RECORD_EXECUTION_INTENT`, set on both `POLICY_ALLOWED` rows.
  - `state_manager.ExecutionOutcome(execution_id, status, reason=None)`, where `status` is one of `succeeded | failed | unknown`.
  - `state_manager.StartResult(started, reason=None, temporal_violation=None, execution=None)`.
  - `state_manager.ExecutionStateError`, `OUTCOME_RECORD_TYPES`, `OUTCOME_VALUES`.
  - `StateManager.apply(case_id, event, payload, source, *, execution_outcome=None)`.
  - `StateManager.start_execution(case_id, execution_id) -> StartResult`.
  - `EscalationCoordinator.signal(case_id, kind, from_state, source, reasons=(), execution_outcome=None)`.
  - `PolicyDecision.decided_state_version`, `.plan_hash`, `.current_step`, `.content_approval_id`.
  - `ReadinessCheck.run(case_id)`, which no longer takes an `hours_until` argument.
  - `wiring.build_state_manager(engine)`.
  - `policy.temporal.EXCLUDED_RECORD_TYPES`.
  - `Driver.last_execution_id`.

- [ ] **Step 1: Write the failing test** `backend/tests/test_execution_core.py`:

```python
"""The State Manager's execution writes (Execution design §3): the intent row, the start, the outcome."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from hospital_agent import repository
from hospital_agent.db import cases
from hospital_agent.execution.verify import REVERIFICATION_FAILED
from hospital_agent.naming import Component, EscalationKind, Event, State
from hospital_agent.policy.service import decision_event
from hospital_agent.policy.temporal import trace_rows
from hospital_agent.state_manager import ExecutionOutcome, ExecutionStateError
from tests.driver import Driver
from tests.test_policy_d_tests import MEDICAL, at_step, content_approval, insert_approval


def execution(d: Driver, execution_id: str):
    with d.engine.connect() as conn:
        return repository.load_execution(conn, execution_id)


def to_allowed(d: Driver) -> None:
    """Step 1 (CheckAppointment) accepted by the real Policy Service."""
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    assert d.state is State.RETRIEVING_DATA


def test_policy_allowed_records_an_intent_bound_to_the_decision(sm, app_engine):
    d = Driver(sm, app_engine)
    to_allowed(d)
    case, row = d.case, execution(d, d.last_execution_id)
    assert (row.status, row.action, row.step, row.state_version) == ("intent", "CheckAppointment", 1, case.state_version)
    assert (row.plan_hash, row.idempotency_key) == (case.plan_hash, f"{case.case_id}:1:0:1")
    assert row.decision_token and not row.medical_content_flag
    assert case.attempt_count == 0  # incremented only when the call starts


def test_a_decision_for_another_state_version_is_blocked(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    decision = d.policy.decide(d.case, d.request())
    stale = replace(decision, decided_state_version=decision.decided_state_version - 1)
    event, payload = decision_event(stale)
    result = sm.apply(d.case_id, event, payload, Component.POLICY_SERVICE)
    assert (result.committed, d.state) == (False, State.PLANNING)


def test_start_writes_the_audit_pair_and_counts_the_attempt(sm, app_engine):
    d = Driver(sm, app_engine)
    to_allowed(d)
    start = sm.start_execution(d.case_id, d.last_execution_id)
    assert start.started and start.execution.execution_id == d.last_execution_id
    started, recorded = d.trace()[-2:]
    assert [(r.record_type, r.event, r.state_after) for r in (started, recorded)] == [
        ("ExecutionStarted", "TOOL_EXECUTION_STARTED", "RetrievingData"),
        ("ExecutionStarted", "AUDIT_RECORDED", "RetrievingData"),
    ]
    assert started.guards == {"InPlan": True, "IdentityVerified": True, "PatientContextPresent": True,
                              "AttemptsAvailable": True, "medical_content_flag": False}
    assert started.execution_id == recorded.execution_id == d.last_execution_id
    assert (d.case.attempt_count, started.attempt_number) == (1, 1)
    assert execution(d, d.last_execution_id).status == "started"


def test_a_decision_starts_only_once(sm, app_engine):
    d = Driver(sm, app_engine)
    to_allowed(d)
    sm.start_execution(d.case_id, d.last_execution_id)
    again = sm.start_execution(d.case_id, d.last_execution_id)
    assert (again.started, again.reason) == (False, REVERIFICATION_FAILED)
    assert d.trace()[-1].record_type == "Blocked" and d.case.attempt_count == 1


def test_a_start_after_the_last_attempt_is_refused(sm, app_engine):
    d = Driver(sm, app_engine)
    to_allowed(d)
    with app_engine.begin() as conn:
        conn.execute(update(cases).where(cases.c.case_id == d.case_id).values(attempt_count=3))
    refused = sm.start_execution(d.case_id, d.last_execution_id)
    assert (refused.started, refused.reason) == (False, "attempts_exhausted")
    assert execution(d, d.last_execution_id).status == "failed"


def test_the_outcome_is_recorded_with_the_event_that_follows(sm, app_engine):
    d = Driver(sm, app_engine)
    to_allowed(d)
    sm.start_execution(d.case_id, d.last_execution_id)
    appointment_at = datetime.now(UTC) + timedelta(hours=96)
    sm.apply(d.case_id, Event.DATA_RETRIEVED, {"execution_id": d.last_execution_id, "appointment_at": appointment_at},
             Component.TOOL_EXECUTOR, execution_outcome=ExecutionOutcome(d.last_execution_id, "succeeded"))
    outcome, transition = d.trace()[-2:]
    assert (outcome.record_type, outcome.state_after, outcome.outcome) == ("ExecutionSucceeded", "RetrievingData", "success")
    assert (transition.event, transition.state_after) == ("DATA_RETRIEVED", "Planning")
    assert outcome not in trace_rows(d.trace())  # not a transition of the temporal trace
    assert execution(d, d.last_execution_id).status == "succeeded"
    assert d.case.appointment_at == appointment_at


def test_an_outcome_needs_a_running_execution(sm, app_engine):
    d = Driver(sm, app_engine)
    to_allowed(d)
    before = len(d.trace())
    with pytest.raises(ExecutionStateError):
        sm.apply(d.case_id, Event.DATA_RETRIEVED, {"execution_id": d.last_execution_id}, Component.TOOL_EXECUTOR,
                 execution_outcome=ExecutionOutcome(d.last_execution_id, "succeeded"))
    assert len(d.trace()) == before and d.state is State.RETRIEVING_DATA


def test_d20_case_resolved_is_blocked_while_the_delivery_has_no_outcome(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    d.allow()
    sm.start_execution(d.case_id, d.last_execution_id)
    payload = {"execution_id": d.last_execution_id}
    early = sm.apply(d.case_id, Event.CASE_RESOLVED, payload, Component.RESPONSE_DELIVERY)
    assert (early.committed, early.reason, d.state) == (False, "guard_failed", State.DELIVERING)
    sm.apply(d.case_id, Event.CASE_RESOLVED, payload, Component.RESPONSE_DELIVERY,
             execution_outcome=ExecutionOutcome(d.last_execution_id, "succeeded"))
    assert d.state is State.COMPLETED


def test_a_medical_message_consumes_its_content_approval_when_it_starts(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    request = d.request(outgoing_message=MEDICAL)
    approval_id = insert_approval(app_engine, content_approval(d, request.execution_id))
    d.allow(execution_id=request.execution_id, outgoing_message=MEDICAL, approval_id=approval_id)
    assert execution(d, request.execution_id).medical_content_flag
    assert sm.start_execution(d.case_id, request.execution_id).started
    assert d.trace()[-2].guards["ContentApprovalValid"] is True
    with app_engine.connect() as conn:
        assert repository.load_approval(conn, approval_id).consumed_at is not None


def test_readiness_without_an_appointment_time_escalates(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    for result in ({}, {"required_documents": ["referral"], "held_documents": []}):
        d.retrieve_step(**result)
        d.advance()
    d.retrieve_step()
    d.assess()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.Z3_COUNTEREXAMPLE)
```

- [ ] **Step 2: Run it and see it fail**

Run: `docker compose run --rm backend pytest tests/test_execution_core.py -v`
Expected: collection error `ImportError: cannot import name 'ExecutionOutcome' from 'hospital_agent.state_manager'`.

- [ ] **Step 3: Replace** `backend/hospital_agent/policy/service.py` with the version below. The decision now records what it was computed on, and passes the content approval's id on only when the approval held:

```python
"""Policy Service - one decision per proposed action (spec §1, §7-§10, §14; Policy design §6).

decide() evaluates OPA (the spec's Rego, run by the real binary) and Prolog (rules.pl,
in a fresh isolated engine) on the same trusted state, and folds them:

    OPA Deny, or Prolog blocks          -> Deny   (reasons from both; §14: disagreement denies)
    OPA RequireHumanReview              -> RequireHumanReview
    OPA Allow and Prolog allows         -> Allow
    an engine is unavailable            -> Deny policy_engine_unavailable

decision_event() turns the decision into POLICY_ALLOWED / POLICY_DENIED /
POLICY_HUMAN_REVIEW_REQUIRED, emitted with source=PolicyService.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any

from sqlalchemy.engine import Engine

from .. import repository
from ..case import MAX_ATTEMPTS, ApprovalRecord, CaseRecord
from ..naming import Action, Component, Event, to_prolog
from ..state_manager import StateManager, TransitionResult
from . import opa_runner
from .approvals import content_approval_valid
from .opa_runner import OpaDecision
from .prolog import RULES_FILE, Prolog, parse_program

AUTOMATION_ACTOR = "patient_agent"
DECISION_EVENT = {
    "Allow": Event.POLICY_ALLOWED,
    "Deny": Event.POLICY_DENIED,
    "RequireHumanReview": Event.POLICY_HUMAN_REVIEW_REQUIRED,
}


@dataclass(frozen=True)
class ProposedAction:
    action: str
    from_step: int | None
    target_system: str
    patient_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutgoingMessage:
    """Set by the Response Evaluator only (§6.5) - never by the Planner or the LLM."""

    evaluated: bool
    medical_content_flag: bool
    content_hash: str


@dataclass(frozen=True)
class InstructionSource:
    source_id: str
    version: str


@dataclass(frozen=True)
class PolicyRequest:
    """What trusted components supply for one attempt (§8: loaded from trusted services)."""

    execution_id: str
    proposed_action: ProposedAction
    outgoing_message: OutgoingMessage | None = None
    approval_id: str | None = None
    instruction_source: InstructionSource | None = None
    patient_verification_status: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    result: str  # Allow | Deny | RequireHumanReview
    reasons: tuple[str, ...]
    action: str
    execution_id: str
    decision_token: str
    content_hash: str | None = None
    content_approval_valid: bool = False
    medical_content_flag: bool = False
    policy_review_override_id: str | None = None
    # What the decision was computed on - the Tool Executor re-verifies it (Execution design §3.1).
    decided_state_version: int | None = None
    plan_hash: str | None = None
    current_step: int | None = None
    content_approval_id: str | None = None  # set only when the ContentApproval was valid


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _rfc3339(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _approval_json(approval: ApprovalRecord | None) -> dict[str, Any] | None:
    if approval is None:
        return None
    data = {k: _rfc3339(v) if isinstance(v, datetime) else v for k, v in approval.__dict__.items()}
    # The approvals table has no escalated_from_state column (§18.2); a PolicyReview
    # escalation always comes from Planning (§3 row POLICY_HUMAN_REVIEW_REQUIRED).
    if approval.escalation_kind == "PolicyReview":
        data["escalated_from_state"] = "Planning"
    return data


def build_opa_input(
    case: CaseRecord,
    request: PolicyRequest,
    override: ApprovalRecord | None,
    approval: ApprovalRecord | None = None,
) -> dict[str, Any]:
    proposal, message, source = request.proposed_action, request.outgoing_message, request.instruction_source
    return {
        "case_id": case.case_id,
        "patient_id": case.patient_id,
        "execution_id": request.execution_id,
        "identity_verified": case.identity_verified,
        "safety_level": case.safety_level.value if case.safety_level else None,
        "intent": case.intent,
        "execution": {"attempt_count": case.attempt_count, "max_attempts": MAX_ATTEMPTS},
        "plan": {"current_step": case.current_step, "plan_hash": case.plan_hash, "ordered_steps": case.ordered_steps},
        "proposed_action": {
            "action": proposal.action,
            "from_step": proposal.from_step,
            "target_system": proposal.target_system,
            "parameters": {"patient_fields": list(proposal.patient_fields)},
        },
        "outgoing_message": None if message is None else {
            "evaluated": message.evaluated,
            "medical_content_flag": message.medical_content_flag,
            "content_hash": message.content_hash,
        },
        "approval": _approval_json(approval),
        "policy_review_override": _approval_json(override),
        "instruction_source": None if source is None else {"source_id": source.source_id, "version": source.version},
        "patient": {"verification_status": request.patient_verification_status},
    }


@cache
def _rule_clauses() -> tuple:
    return tuple(parse_program(RULES_FILE.read_text(encoding="utf-8")))


def _atom(value: str) -> str:
    # M3: backslash must be escaped first, or a value ending "...\\'" produces an unterminated
    # (or wrongly-terminated) quoted atom once the quote's own escape is added after it.
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def prolog_verdict(case: CaseRecord, request: PolicyRequest, approval_ok: bool) -> tuple[bool, str]:
    """explain/4 of rules.pl for the automation actor, over this request's facts only."""
    action = request.proposed_action.action
    if action not in {a.value for a in Action}:
        return False, "action_not_supported"
    engine = Prolog()
    engine.clauses = list(_rule_clauses())
    c, p, e = _atom(case.case_id), _atom(case.patient_id), _atom(request.execution_id)
    if case.identity_verified:
        engine.assertz(f"case_identity_verified({c})")
    engine.assertz(f"case_patient({c}, {p})")
    if case.current_action is not None:
        engine.assertz(f"case_step({c}, {to_prolog(case.current_action)})")
    engine.assertz(f"case_execution({c}, {e})")
    engine.assertz(f"current_execution({e})")
    engine.assertz(f"current_patient({p})")
    message = request.outgoing_message
    if message is not None:
        engine.assertz(f"current_content_hash({_atom(message.content_hash)})")
        if message.evaluated:
            engine.assertz("outgoing_message_evaluated")
        engine.assertz(f"outgoing_medical_content({'true' if message.medical_content_flag else 'false'})")
        if approval_ok:
            engine.assertz(f"content_approval_valid({e}, {p}, {to_prolog(action)}, {_atom(message.content_hash)})")
    [answer] = engine.solve(f"explain({AUTOMATION_ACTOR}, {to_prolog(action)}, {c}, R)", limit=1)
    return answer["R"] == "allowed", answer["R"]


class PolicyService:
    def __init__(
        self,
        engine: Engine,
        *,
        opa: Callable[[Mapping[str, Any]], OpaDecision] = opa_runner.evaluate,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.engine, self.opa, self.clock = engine, opa, clock

    def decide(self, case: CaseRecord, request: PolicyRequest) -> PolicyDecision:
        now = self.clock()
        message = request.outgoing_message
        try:
            with self.engine.connect() as conn:
                override = repository.open_policy_review_override(
                    conn, case.case_id, case.plan_hash, case.current_step
                )
                # F2: the caller supplies only an id - the Policy Service loads the trusted
                # record itself. An id that is not on the approvals table behaves exactly like
                # no approval.
                approval = repository.load_approval(conn, request.approval_id) if request.approval_id else None
            approval_ok = message is not None and content_approval_valid(
                approval,
                case_id=case.case_id,
                patient_id=case.patient_id,
                execution_id=request.execution_id,
                action=request.proposed_action.action,
                content_hash=message.content_hash,
                now=now,
            )
            opa = self.opa(build_opa_input(case, request, override, approval=approval))
        except Exception:  # noqa: BLE001 - M2: any failure loading input for OPA fails closed (§14)
            override, approval_ok, opa = None, False, opa_runner.UNAVAILABLE
        try:
            prolog_allowed, explanation = prolog_verdict(case, request, approval_ok)
        except Exception:  # noqa: BLE001 - any engine failure fails closed (§14)
            prolog_allowed, explanation = False, "policy_engine_unavailable"

        reasons = list(opa.reasons) if opa.result == "Deny" else []
        if not prolog_allowed:
            reasons.append(f"prolog:{explanation}")
        if opa.result == "Deny" or not prolog_allowed:
            result = "Deny"
        elif opa.result == "RequireHumanReview":
            result, reasons = "RequireHumanReview", list(opa.reasons)
        else:
            result = "Allow"
        return PolicyDecision(
            result=result,
            reasons=tuple(reasons),
            action=request.proposed_action.action,
            execution_id=request.execution_id,
            decision_token=uuid.uuid4().hex,
            content_hash=None if message is None else message.content_hash,
            content_approval_valid=approval_ok,
            medical_content_flag=bool(message and message.medical_content_flag),
            policy_review_override_id=None if override is None else override.approval_id,
            decided_state_version=case.state_version,
            plan_hash=case.plan_hash,
            current_step=case.current_step,
            content_approval_id=request.approval_id if approval_ok else None,
        )

    def apply(self, state_manager: StateManager, case_id: str, request: PolicyRequest) -> TransitionResult:
        decision = self.decide(state_manager.load(case_id), request)
        event, payload = decision_event(decision)
        return state_manager.apply(case_id, event, payload, Component.POLICY_SERVICE)


def decision_event(decision: PolicyDecision) -> tuple[Event, dict[str, Any]]:
    payload: dict[str, Any] = {
        "action": decision.action,
        "policy_result": decision.result,
        "policy_reasons": list(decision.reasons),
        "decision_token": decision.decision_token,
        "execution_id": decision.execution_id,
        "decided_state_version": decision.decided_state_version,
        "plan_hash": decision.plan_hash,
        "current_step": decision.current_step,
        "evidence": {
            "ContentApprovalValid": decision.content_approval_valid,
            "medical_content_flag": decision.medical_content_flag,
        },
    }
    if decision.content_hash is not None:
        payload["content_hash"] = decision.content_hash
    if decision.policy_review_override_id is not None:
        payload["policy_review_override_id"] = decision.policy_review_override_id
    if decision.content_approval_id is not None:
        payload["content_approval_id"] = decision.content_approval_id
    return DECISION_EVENT[decision.result], payload
```

- [ ] **Step 4: Replace** `backend/hospital_agent/fsm.py` with the version below. It adds `Effect.RECORD_EXECUTION_INTENT` on both `POLICY_ALLOWED` rows, and `RECORD_RETRIEVAL` now also stores `appointment_at`:

```python
"""Transition table - spec §3, the single source of truth for State changes.

41 rows. tests/test_fsm.py compares (source, event, spec_guard, target) of every
row with docs/spec/03-transitions-guards.md, so the code cannot drift from the spec.

A guard name starting with "!" is negated ("לא ReadinessInProgress").
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from .case import CaseRecord, compute_plan_hash
from .guards import GUARD_FAILED, GUARDS, GuardContext
from .naming import EscalationKind as K
from .naming import Event as E
from .naming import SafetyLevel
from .naming import State as S


class Effect(StrEnum):
    """Changes a transition makes to the cases row (design §6.3)."""

    MARK_IDENTITY_VERIFIED = "MarkIdentityVerified"
    RECORD_CLASSIFICATION = "RecordClassification"
    RECORD_PLAN = "RecordPlan"
    ADVANCE_STEP = "AdvanceStep"
    RECORD_RETRIEVAL = "RecordRetrieval"
    RECORD_PATIENT_DEADLINE = "RecordPatientDeadline"
    HOLD_DOCUMENT = "HoldDocument"
    OPEN_RETRY_CYCLE = "OpenRetryCycle"
    APPROVED_PATIENT_DEADLINE = "ApprovedPatientDeadline"
    CLEAR_ESCALATION = "ClearEscalation"
    CONSUME_APPROVAL = "ConsumeApproval"
    RECORD_EXECUTION_INTENT = "RecordExecutionIntent"  # Execution design §3.1 - written by the State Manager


@dataclass(frozen=True)
class EscalationSpec:
    """The escalation a row records: which kinds are allowed and the state it comes from.

    from_payload=True (the HUMAN_REVIEW_REQUIRED rows): the kind is taken from the
    event payload and must be in `kinds`. Otherwise the row has exactly one kind.
    """

    kinds: frozenset[K]
    from_state: S
    from_payload: bool = False


@dataclass(frozen=True)
class Transition:
    source: S | None  # None = Initial
    event: E
    target: S
    spec_guard: str  # the Guard cell of the §3 table, verbatim
    guards: tuple[str, ...] = ()
    requires_escalation: frozenset[K] | None = None  # HUMAN_APPROVED rows: required case.escalation_kind
    escalation: EscalationSpec | None = None
    effects: tuple[Effect, ...] = field(default=())


def _fixed(kind: K, source: S) -> EscalationSpec:
    return EscalationSpec(frozenset({kind}), source)


def _signal(source: S, *kinds: K) -> EscalationSpec:
    return EscalationSpec(frozenset(kinds), source, from_payload=True)


_SYS = ("SystemEscalationRequired",)
_RESUME = (Effect.CLEAR_ESCALATION, Effect.CONSUME_APPROVAL)

TRANSITIONS: tuple[Transition, ...] = (
    Transition(None, E.REQUEST_SUBMITTED, S.RECEIVED, "PatientIdentified", ("PatientIdentified",)),
    Transition(S.RECEIVED, E.REQUEST_VALIDATED, S.CLASSIFYING, "RequestValid, IdentityVerified",
               ("RequestValid", "IdentityVerified"), effects=(Effect.MARK_IDENTITY_VERIFIED,)),
    Transition(S.RECEIVED, E.PATIENT_VERIFICATION_FAILED, S.AWAITING_HUMAN_REVIEW,
               "escalation_kind = PatientVerificationFailed, escalated_from_state = Received",
               escalation=_fixed(K.PATIENT_VERIFICATION_FAILED, S.RECEIVED)),
    Transition(S.CLASSIFYING, E.INTENT_CLASSIFIED, S.CLASSIFIED, "לא ReadinessInProgress",
               ("valid_classification", "!ReadinessInProgress"), effects=(Effect.RECORD_CLASSIFICATION,)),
    Transition(S.CLASSIFYING, E.INTENT_CLASSIFIED, S.ASSESSING_READINESS, "ReadinessInProgress",
               ("valid_classification", "ReadinessInProgress"), effects=(Effect.RECORD_CLASSIFICATION,)),
    Transition(S.CLASSIFYING, E.MEDICAL_QUESTION_DETECTED, S.AWAITING_HUMAN_REVIEW,
               "escalation_kind = MedicalQuestion, escalated_from_state = Classifying",
               escalation=_fixed(K.MEDICAL_QUESTION, S.CLASSIFYING)),
    Transition(S.CLASSIFYING, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {SafetyEscalation, ClassificationFailed, "
               "TemporalViolation}, escalated_from_state = Classifying",
               _SYS, escalation=_signal(S.CLASSIFYING, K.SAFETY_ESCALATION, K.CLASSIFICATION_FAILED,
                                        K.TEMPORAL_VIOLATION)),
    Transition(S.CLASSIFIED, E.PLAN_CREATED, S.PLANNING, "PlanComplete", ("PlanComplete",),
               effects=(Effect.RECORD_PLAN,)),
    Transition(S.CLASSIFIED, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {PlanningFailed, TemporalViolation}, "
               "escalated_from_state = Classified",
               _SYS, escalation=_signal(S.CLASSIFIED, K.PLANNING_FAILED, K.TEMPORAL_VIOLATION)),
    Transition(S.PLANNING, E.ACTION_PROPOSED, S.PLANNING, "InPlan, PlanIntact", ("InPlan", "PlanIntact")),
    Transition(S.PLANNING, E.STEP_ADVANCED, S.PLANNING, "PlanIntact, CanAdvance", ("PlanIntact", "CanAdvance"),
               effects=(Effect.ADVANCE_STEP,)),
    Transition(S.PLANNING, E.POLICY_ALLOWED, S.RETRIEVING_DATA,
               "ExecutorReverified, proposed_action ∈ retrieval_actions", ("ExecutorReverified", "retrieval_action"),
               effects=(Effect.RECORD_EXECUTION_INTENT,)),
    Transition(S.PLANNING, E.POLICY_ALLOWED, S.DELIVERING,
               "ExecutorReverified, proposed_action = SendStatusUpdate", ("ExecutorReverified", "delivery_action"),
               effects=(Effect.RECORD_EXECUTION_INTENT,)),
    Transition(S.PLANNING, E.POLICY_DENIED, S.AWAITING_HUMAN_REVIEW,
               "escalation_kind = PolicyDenied, escalated_from_state = Planning",
               escalation=_fixed(K.POLICY_DENIED, S.PLANNING)),
    Transition(S.PLANNING, E.POLICY_HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "escalation_kind = PolicyReview, escalated_from_state = Planning",
               escalation=_fixed(K.POLICY_REVIEW, S.PLANNING)),
    Transition(S.PLANNING, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {PlanningFailed, TemporalViolation}, "
               "escalated_from_state = Planning",
               _SYS, escalation=_signal(S.PLANNING, K.PLANNING_FAILED, K.TEMPORAL_VIOLATION)),
    Transition(S.RETRIEVING_DATA, E.DATA_RETRIEVED, S.PLANNING, "RetrievalStepsRemain",
               ("valid_tool_result", "RetrievalStepsRemain"), effects=(Effect.RECORD_RETRIEVAL,)),
    Transition(S.RETRIEVING_DATA, E.DATA_RETRIEVED, S.ASSESSING_READINESS, "PreReadinessPhaseComplete",
               ("valid_tool_result", "PreReadinessPhaseComplete"), effects=(Effect.RECORD_RETRIEVAL,)),
    Transition(S.RETRIEVING_DATA, E.TOOL_TRANSIENT_FAILURE, S.PLANNING, "AttemptsAvailable, IsIdempotent",
               ("AttemptsAvailable", "IsIdempotent")),
    Transition(S.RETRIEVING_DATA, E.RETRY_EXHAUSTED, S.AWAITING_HUMAN_REVIEW,
               "escalation_kind = RetryExhausted, escalated_from_state = RetrievingData",
               escalation=_fixed(K.RETRY_EXHAUSTED, S.RETRIEVING_DATA)),
    Transition(S.RETRIEVING_DATA, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {NonIdempotentFailure, ExecutionUnknown, "
               "TemporalViolation}, escalated_from_state = RetrievingData",
               _SYS, escalation=_signal(S.RETRIEVING_DATA, K.NON_IDEMPOTENT_FAILURE, K.EXECUTION_UNKNOWN,
                                        K.TEMPORAL_VIOLATION)),
    Transition(S.DELIVERING, E.CASE_RESOLVED, S.COMPLETED, "DeliveryConfirmed, ReadinessComplete",
               ("DeliveryConfirmed", "ReadinessComplete")),
    Transition(S.DELIVERING, E.TOOL_TRANSIENT_FAILURE, S.PLANNING, "AttemptsAvailable, IsIdempotent",
               ("AttemptsAvailable", "IsIdempotent")),
    Transition(S.DELIVERING, E.RETRY_EXHAUSTED, S.AWAITING_HUMAN_REVIEW,
               "escalation_kind = RetryExhausted, escalated_from_state = Delivering",
               escalation=_fixed(K.RETRY_EXHAUSTED, S.DELIVERING)),
    Transition(S.DELIVERING, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {NonIdempotentFailure, ExecutionUnknown, "
               "TemporalViolation}, escalated_from_state = Delivering",
               _SYS, escalation=_signal(S.DELIVERING, K.NON_IDEMPOTENT_FAILURE, K.EXECUTION_UNKNOWN,
                                        K.TEMPORAL_VIOLATION)),
    Transition(S.ASSESSING_READINESS, E.READINESS_PASSED, S.READY, "ReadinessComplete", ("ReadinessComplete",)),
    Transition(S.ASSESSING_READINESS, E.MISSING_INFORMATION_DETECTED, S.AWAITING_PATIENT_INPUT,
               "לא ReadinessComplete, AskPatientSafe", ("deadline_registered", "!ReadinessComplete", "AskPatientSafe"),
               effects=(Effect.RECORD_PATIENT_DEADLINE,)),
    Transition(S.ASSESSING_READINESS, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {Z3Counterexample (לא AskPatientSafe), "
               "TemporalViolation}, escalated_from_state = AssessingReadiness",
               _SYS, escalation=_signal(S.ASSESSING_READINESS, K.Z3_COUNTEREXAMPLE, K.TEMPORAL_VIOLATION)),
    Transition(S.AWAITING_PATIENT_INPUT, E.DOCUMENT_UPLOADED, S.CLASSIFYING, "DocumentValid", ("DocumentValid",),
               effects=(Effect.HOLD_DOCUMENT,)),
    Transition(S.AWAITING_PATIENT_INPUT, E.DOCUMENT_UPLOADED, S.AWAITING_PATIENT_INPUT, "DocumentValid אינו מתקיים",
               ("!DocumentValid",)),
    Transition(S.AWAITING_PATIENT_INPUT, E.TIMEOUT_EXPIRED, S.AWAITING_HUMAN_REVIEW,
               "PatientSlaExpired, escalation_kind = PatientSlaExpired, escalated_from_state = AwaitingPatientInput",
               ("PatientSlaExpired",), escalation=_fixed(K.PATIENT_SLA_EXPIRED, S.AWAITING_PATIENT_INPUT)),
    Transition(S.READY, E.DELIVERY_PLANNED, S.PLANNING, "PlanIntact, CanAdvance, DeliveryStepPending",
               ("PlanIntact", "CanAdvance", "DeliveryStepPending"), effects=(Effect.ADVANCE_STEP,)),
    Transition(S.READY, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {DeliveryStepMissing, TemporalViolation}, "
               "escalated_from_state = Ready",
               _SYS, escalation=_signal(S.READY, K.DELIVERY_STEP_MISSING, K.TEMPORAL_VIOLATION)),
    Transition(S.RECEIVED, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {TemporalViolation}, escalated_from_state = Received",
               _SYS, escalation=_signal(S.RECEIVED, K.TEMPORAL_VIOLATION)),
    Transition(S.AWAITING_PATIENT_INPUT, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {TemporalViolation}, "
               "escalated_from_state = AwaitingPatientInput",
               _SYS, escalation=_signal(S.AWAITING_PATIENT_INPUT, K.TEMPORAL_VIOLATION)),
    Transition(S.AWAITING_HUMAN_REVIEW, E.HUMAN_APPROVED, S.RECEIVED,
               "WorkflowDecisionValid, escalation_kind = PatientVerificationFailed, verified_identity_ref קיים; "
               "identity_verified נכתב true באותה טרנזקציה",
               ("WorkflowDecisionValid",), requires_escalation=frozenset({K.PATIENT_VERIFICATION_FAILED}),
               effects=(Effect.MARK_IDENTITY_VERIFIED, *_RESUME)),
    Transition(S.AWAITING_HUMAN_REVIEW, E.HUMAN_APPROVED, S.PLANNING,
               "WorkflowDecisionValid, escalation_kind = RetryExhausted; retry_cycle + 1, attempt_count = 0",
               ("WorkflowDecisionValid",), requires_escalation=frozenset({K.RETRY_EXHAUSTED}),
               effects=(Effect.OPEN_RETRY_CYCLE, *_RESUME)),
    # Design §12.1: the PolicyReview approval is NOT consumed here; it stays open as
    # PolicyReviewOverrideValid and is consumed by the next Policy decision (sub-project 2).
    Transition(S.AWAITING_HUMAN_REVIEW, E.HUMAN_APPROVED, S.PLANNING,
               "WorkflowDecisionValid, escalation_kind = PolicyReview; יוצר PolicyReviewOverrideValid כבול "
               "ל־plan_hash + current_step",
               ("WorkflowDecisionValid",), requires_escalation=frozenset({K.POLICY_REVIEW}),
               effects=(Effect.CLEAR_ESCALATION,)),
    Transition(S.AWAITING_HUMAN_REVIEW, E.HUMAN_APPROVED, S.AWAITING_PATIENT_INPUT,
               "WorkflowDecisionValid, escalation_kind in {Z3Counterexample, PatientSlaExpired}, patient_deadline "
               "קיים; נרשם דד־ליין חדש",
               ("WorkflowDecisionValid",),
               requires_escalation=frozenset({K.Z3_COUNTEREXAMPLE, K.PATIENT_SLA_EXPIRED}),
               effects=(Effect.APPROVED_PATIENT_DEADLINE, *_RESUME)),
    Transition(S.AWAITING_HUMAN_REVIEW, E.HUMAN_RESOLVED_CASE, S.COMPLETED, "WorkflowDecisionValid, קיימת סיבת סגירה",
               ("WorkflowDecisionValid", "closure_reason"), effects=(Effect.CONSUME_APPROVAL,)),
    Transition(S.AWAITING_HUMAN_REVIEW, E.HUMAN_REJECTED, S.FAILED, "WorkflowDecisionValid",
               ("WorkflowDecisionValid",), effects=(Effect.CONSUME_APPROVAL,)),
)


class AmbiguousTransition(RuntimeError):
    """More than one row matched - a bug in the table. Fail closed."""


@dataclass(frozen=True)
class Resolution:
    transition: Transition | None
    reason: str | None  # None when a row matched
    guard_results: dict[str, bool]  # the matched row's guards, for audit_log.guards


def _evaluate(row: Transition, ctx: GuardContext) -> tuple[str | None, dict[str, bool]]:
    """Return (failure reason or None, guard results) for one candidate row."""
    results: dict[str, bool] = {}
    if row.requires_escalation is not None and (ctx.case is None or ctx.case.escalation_kind not in row.requires_escalation):
        return GUARD_FAILED, results
    row_ctx = replace(ctx, escalation_kinds=row.escalation.kinds if row.escalation else frozenset())
    for name in row.guards:
        negated = name.startswith("!")
        reason = GUARDS[name.removeprefix("!")](row_ctx)
        holds = (reason is not None) if negated else (reason is None)
        results[name] = holds
        if not holds:
            return (GUARD_FAILED if negated else reason), results
    return None, results


def resolve(state: S | None, event: E, ctx: GuardContext) -> Resolution:
    """Find the one row for (state, event) whose guards all hold (§3, note 8)."""
    candidates = [row for row in TRANSITIONS if row.source == state and row.event == event]
    matched: list[tuple[Transition, dict[str, bool]]] = []
    reasons: list[str] = []
    for row in candidates:
        reason, results = _evaluate(row, ctx)
        if reason is None:
            matched.append((row, results))
        else:
            reasons.append(reason)
    if len(matched) > 1:
        raise AmbiguousTransition(f"{len(matched)} rows matched ({state}, {event})")
    if matched:
        return Resolution(matched[0][0], None, matched[0][1])
    # Surface a specific reason (identity_not_established, ...) over the generic one.
    specific = next((r for r in reasons if r != GUARD_FAILED), GUARD_FAILED)
    return Resolution(None, specific, {})


def apply_effects(case: CaseRecord, row: Transition, ctx: GuardContext) -> CaseRecord:
    """The cases row after `row` fires. Pure: the State Manager persists the result."""
    p = ctx.payload
    changes: dict[str, object] = {"state": row.target}
    if row.escalation is not None:
        kind = p["escalation_kind"] if row.escalation.from_payload else next(iter(row.escalation.kinds))
        changes["escalation_kind"] = K(kind)
        changes["escalated_from_state"] = row.escalation.from_state
    for effect in row.effects:
        match effect:
            case Effect.MARK_IDENTITY_VERIFIED:
                changes["identity_verified"] = True
            case Effect.RECORD_CLASSIFICATION:
                changes["intent"] = p.get("intent")
                changes["safety_level"] = SafetyLevel(p["safety_level"]) if p.get("safety_level") else None
            case Effect.RECORD_PLAN:
                steps = [dict(step) for step in p["ordered_steps"]]
                changes.update(ordered_steps=steps, plan_hash=compute_plan_hash(steps), current_step=1,
                               retry_cycle=0, attempt_count=0)
            case Effect.ADVANCE_STEP:
                changes.update(current_step=case.current_step + 1, retry_cycle=0, attempt_count=0)
            case Effect.RECORD_RETRIEVAL:
                if "appointment_at" in p:
                    changes["appointment_at"] = p["appointment_at"]
                if "required_documents" in p:
                    changes["required_documents"] = list(p["required_documents"])
                if "held_documents" in p:
                    changes["held_documents"] = list(p["held_documents"])
            case Effect.RECORD_PATIENT_DEADLINE:
                changes["patient_deadline"] = p.get("patient_deadline")
            case Effect.HOLD_DOCUMENT:
                document_id = p["document"]["document_id"]
                if document_id not in case.held_documents:
                    changes["held_documents"] = [*case.held_documents, document_id]
            case Effect.OPEN_RETRY_CYCLE:
                changes.update(retry_cycle=case.retry_cycle + 1, attempt_count=0)
            case Effect.APPROVED_PATIENT_DEADLINE:
                changes["patient_deadline"] = ctx.approval.patient_deadline
            case Effect.CLEAR_ESCALATION:
                changes.update(escalation_kind=None, escalated_from_state=None)
            case Effect.CONSUME_APPROVAL | Effect.RECORD_EXECUTION_INTENT:
                pass  # written by the State Manager (approvals / executions), in the same transaction
    return replace(case, **changes)
```

- [ ] **Step 5: Replace** `backend/hospital_agent/guards.py` with the version below. `ValidToolResult` also requires that an `appointment_at`, when present, is timezone-aware:

```python
"""Guards of spec §3.1, plus the three conditions the §3 table writes in prose.

A guard takes a GuardContext and returns None when it holds, or a reason code
when it does not. Most failures are "guard_failed"; the approval and escalation
guards return the specific codes the spec names.

§3.1's "who evaluates" column is enforced here. A fact that another component
determines ("X קובע · State Manager מאמת") is trusted only from its owning
component: for a system-owned event, the State Manager has already checked
that the event came from its owner (§13.2), so the guard can read the fact
straight from the payload; for an external event (DOCUMENT_UPLOADED has no
owner), the guard itself checks GuardContext.source against the component that
is supposed to have determined the fact (DocumentValid requires
Component.SESSION_SERVICE). A guard marked "State Manager, מה־State השמור"
reads only the cases row.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .case import MAX_ATTEMPTS, ApprovalRecord, CaseRecord, ExecutionRecord, compute_plan_hash
from .naming import AUTOMATIC_ACTIONS, RETRIEVAL_ACTIONS, Action, Component, EscalationKind, Event, SafetyLevel, State

GUARD_FAILED = "guard_failed"
INVALID_ESCALATION_REASON = "invalid_escalation_reason"
WORKFLOW_DECISION_INVALID = "workflow_decision_invalid"

SUPPORTED_DOCUMENT_FORMATS = frozenset({"pdf", "jpg", "png"})
REVIEWER_ROLES = frozenset({"clinical_staff", "admin_staff"})
DECISION_FOR_EVENT = {
    Event.HUMAN_APPROVED: "approve",
    Event.HUMAN_REJECTED: "reject",
    Event.HUMAN_RESOLVED_CASE: "resolve",
}


@dataclass(frozen=True)
class GuardPorts:
    """Guards evaluated by components the Core does not contain yet.

    Every port must be passed explicitly - there is no permissive default. Tests
    use the fakes in tests/fakes.py; sub-project 3 supplies the real Tool Executor.
    """

    executor_reverified: Callable[[GuardContext], bool]


@dataclass(frozen=True)
class GuardContext:
    case: CaseRecord | None  # None only for REQUEST_SUBMITTED (source state Initial)
    event: Event
    payload: Mapping[str, Any]
    now: datetime
    ports: GuardPorts
    source: Component = Component.EXTERNAL  # who emitted the event; populated by the State Manager
    approval: ApprovalRecord | None = None  # loaded from approvals by payload["approval_id"]
    execution: ExecutionRecord | None = None  # loaded from executions by payload["execution_id"]
    escalation_kinds: frozenset[EscalationKind] = frozenset()  # allowlist of the row being evaluated
    approval_already_used: bool = False  # payload["approval_id"] already appears on a committed Transition row


Guard = Callable[[GuardContext], str | None]


def _check(condition: bool) -> str | None:
    return None if condition else GUARD_FAILED


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _valid_plan(steps: object) -> bool:
    """Steps numbered 1..n, only the keys step/action, only automatic actions (§5)."""
    if not isinstance(steps, list) or not steps:
        return False
    automatic = {a.value for a in AUTOMATIC_ACTIONS}
    return all(
        isinstance(s, Mapping) and set(s) == {"step", "action"} and s["step"] == i and s["action"] in automatic
        for i, s in enumerate(steps, start=1)
    )


# --- §3.1 guards, in the order of the spec table ---------------------------------


def patient_identified(ctx: GuardContext) -> str | None:
    return _check(_nonempty(ctx.payload.get("patient_id")))


def request_valid(ctx: GuardContext) -> str | None:
    document = ctx.payload.get("document")
    document_ok = document is None or (
        isinstance(document, Mapping) and document.get("format") in SUPPORTED_DOCUMENT_FORMATS
    )
    return _check(_nonempty(ctx.payload.get("text")) and document_ok)


def plan_complete(ctx: GuardContext) -> str | None:
    return _check(ctx.payload.get("plan_complete") is True and _valid_plan(ctx.payload.get("ordered_steps")))


def in_plan(ctx: GuardContext) -> str | None:
    case, proposal = ctx.case, ctx.payload.get("proposed_action")
    if case is None or case.current_action is None or not isinstance(proposal, Mapping):
        return GUARD_FAILED
    return _check(proposal.get("action") == case.current_action.value and proposal.get("from_step") == case.current_step)


def identity_verified(ctx: GuardContext) -> str | None:
    if ctx.case is not None and ctx.case.identity_verified:
        return None
    # REQUEST_VALIDATED is where the Session Service's verification is written to State.
    return _check(ctx.event is Event.REQUEST_VALIDATED and ctx.payload.get("identity_verified") is True)


def attempts_available(ctx: GuardContext) -> str | None:
    return _check(ctx.case is not None and ctx.case.attempt_count < MAX_ATTEMPTS)


def is_idempotent(ctx: GuardContext) -> str | None:
    return _check(_nonempty(ctx.payload.get("idempotency_key")) and ctx.payload.get("idempotent") is True)


def readiness_complete(ctx: GuardContext) -> str | None:
    # Computed from tool results stored in State - never declared by the caller.
    return _check(ctx.case is not None and ctx.case.readiness_complete)


def delivery_confirmed(ctx: GuardContext) -> str | None:
    case, execution = ctx.case, ctx.execution
    return _check(
        case is not None
        and execution is not None
        and execution.case_id == case.case_id
        and execution.step == case.current_step
        and execution.action == Action.SEND_STATUS_UPDATE.value
        and execution.status == "succeeded"
    )


def readiness_in_progress(ctx: GuardContext) -> str | None:
    case = ctx.case
    return _check(case is not None and case.plan_hash is not None and case.required_documents is not None)


def plan_intact(ctx: GuardContext) -> str | None:
    case = ctx.case
    return _check(
        case is not None and case.ordered_steps is not None and case.plan_hash == compute_plan_hash(case.ordered_steps)
    )


def can_advance(ctx: GuardContext) -> str | None:
    case = ctx.case
    return _check(
        case is not None
        and case.ordered_steps is not None
        and case.current_step is not None
        and case.current_step + 1 <= len(case.ordered_steps)
    )


def retrieval_steps_remain(ctx: GuardContext) -> str | None:
    case = ctx.case
    return _check(
        case is not None
        and case.current_action in {Action.CHECK_APPOINTMENT, Action.CHECK_DOCUMENTS}
        and case.next_action in RETRIEVAL_ACTIONS
    )


def pre_readiness_phase_complete(ctx: GuardContext) -> str | None:
    case = ctx.case
    if case is None or case.current_action is not Action.LOAD_INSTRUCTIONS:
        return GUARD_FAILED
    earlier = [case.step_action(step) for step in range(1, case.current_step)]
    return _check(all(action in RETRIEVAL_ACTIONS for action in earlier))


def delivery_step_pending(ctx: GuardContext) -> str | None:
    case = ctx.case
    return _check(
        readiness_complete(ctx) is None
        and case.current_action is Action.LOAD_INSTRUCTIONS
        and case.next_action is Action.SEND_STATUS_UPDATE
    )


def _not_expired(expires_at: object, now: datetime) -> bool:
    """F3(a): expires_at is an aware datetime, an ISO-8601 string with a timezone, or absent.

    Anything unparsable or naive fails the guard - it never raises.
    """
    if expires_at is None:
        return True
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            return False
    if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
        return False
    return expires_at > now


def document_valid(ctx: GuardContext) -> str | None:
    case, document = ctx.case, ctx.payload.get("document")
    if case is None or not isinstance(document, Mapping):
        return GUARD_FAILED
    return _check(
        ctx.source is Component.SESSION_SERVICE
        and _nonempty(document.get("document_id"))
        and document.get("format") in SUPPORTED_DOCUMENT_FORMATS
        and document.get("patient_id") == case.patient_id
        and _not_expired(document.get("expires_at"), ctx.now)
    )


def workflow_decision_valid(ctx: GuardContext) -> str | None:
    """§3.1 + §12.4/§12.5, checked against the approvals row - never against payload flags."""
    case, approval = ctx.case, ctx.approval
    if case is None or approval is None:
        return WORKFLOW_DECISION_INVALID
    if (
        approval.approval_type != "WorkflowDecision"
        or approval.case_id != case.case_id
        or approval.patient_id != case.patient_id
        or approval.reviewer_role not in REVIEWER_ROLES
        or not all(_nonempty(v) for v in (approval.reviewer_id, approval.reason, approval.shown_context_ref))
        or not approval.granted_at <= ctx.now < approval.valid_until
        or approval.consumed_at is not None
        or approval.escalation_kind != case.escalation_kind
        or ctx.approval_already_used
    ):
        return WORKFLOW_DECISION_INVALID
    if approval.decision != DECISION_FOR_EVENT.get(ctx.event):
        return "approval_decision_mismatch"
    if ctx.event is Event.HUMAN_APPROVED:
        kind = case.escalation_kind
        if kind is EscalationKind.PATIENT_VERIFICATION_FAILED and not _nonempty(approval.verified_identity_ref):
            return "identity_not_established"
        if kind in {EscalationKind.Z3_COUNTEREXAMPLE, EscalationKind.PATIENT_SLA_EXPIRED} and approval.patient_deadline is None:
            return "patient_deadline_missing"
        if kind is EscalationKind.POLICY_REVIEW and (
            approval.plan_hash != case.plan_hash or approval.current_step != case.current_step
        ):
            return WORKFLOW_DECISION_INVALID
    return None


def executor_reverified(ctx: GuardContext) -> str | None:
    return _check(ctx.ports.executor_reverified(ctx))


def patient_sla_expired(ctx: GuardContext) -> str | None:
    case = ctx.case
    return _check(
        case is not None
        and case.state is State.AWAITING_PATIENT_INPUT
        and case.patient_deadline is not None
        and case.patient_deadline <= ctx.now
        and ctx.payload.get("registered_state_version") == case.state_version
    )


def system_escalation_required(ctx: GuardContext) -> str | None:
    case = ctx.case
    valid = (
        case is not None
        and ctx.payload.get("escalation_kind") in ctx.escalation_kinds
        and ctx.payload.get("escalated_from_state") == case.state.value
    )
    return None if valid else INVALID_ESCALATION_REASON


def ask_patient_safe(ctx: GuardContext) -> str | None:
    # Readiness Check with Z3 determines it; only UNSAT means safe (§9.1).
    return _check(ctx.payload.get("z3_result") == "unsat")


# --- conditions the §3 table writes in prose ------------------------------------


def retrieval_action(ctx: GuardContext) -> str | None:
    """"proposed_action ∈ retrieval_actions" - the current plan step, confirmed by the Policy decision."""
    case = ctx.case
    return _check(
        case is not None
        and case.current_action in RETRIEVAL_ACTIONS
        and ctx.payload.get("action") == case.current_action.value
    )


def delivery_action(ctx: GuardContext) -> str | None:
    """"proposed_action = SendStatusUpdate"."""
    case = ctx.case
    return _check(
        case is not None
        and case.current_action is Action.SEND_STATUS_UPDATE
        and ctx.payload.get("action") == Action.SEND_STATUS_UPDATE.value
    )


def closure_reason(ctx: GuardContext) -> str | None:
    """"קיימת סיבת סגירה" - the resolving approval carries a reason."""
    return _check(ctx.approval is not None and _nonempty(ctx.approval.reason))


def valid_classification(ctx: GuardContext) -> str | None:
    """F3(b): payload["safety_level"] must be one of the four SafetyLevel values (§14 invalid_safety_level).

    An unrecognised value is malformed input, not a missing rule - it must never reach
    apply_effects(), which would otherwise raise constructing SafetyLevel(...).
    """
    level = ctx.payload.get("safety_level")
    valid = isinstance(level, SafetyLevel) or (isinstance(level, str) and level in {s.value for s in SafetyLevel})
    return None if valid else "invalid_safety_level"


def _valid_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() != "" for item in value)


def valid_tool_result(ctx: GuardContext) -> str | None:
    """F3(c): required_documents / held_documents, when present, must be lists of non-empty strings.

    Otherwise a string payload silently becomes a list of characters in apply_effects().
    appointment_at (Execution design §5), when present, must be a timezone-aware datetime.
    """
    payload = ctx.payload
    for key in ("required_documents", "held_documents"):
        if key in payload and not _valid_string_list(payload[key]):
            return "invalid_tool_result"
    if "appointment_at" in payload:
        at = payload["appointment_at"]
        if not (isinstance(at, datetime) and at.tzinfo is not None):
            return "invalid_tool_result"
    return None


def deadline_registered(ctx: GuardContext) -> str | None:
    """F3(d): MISSING_INFORMATION_DETECTED must carry a future, timezone-aware patient_deadline.

    Otherwise the case would commit to AwaitingPatientInput with a NULL deadline, and
    PatientSlaExpired could never fire.
    """
    deadline = ctx.payload.get("patient_deadline")
    valid = isinstance(deadline, datetime) and deadline.tzinfo is not None and deadline > ctx.now
    return None if valid else "patient_deadline_missing"


# PascalCase keys are §3.1 guard names (tests/test_guards.py checks them against the
# spec); snake_case keys are the prose conditions above.
GUARDS: dict[str, Guard] = {
    "PatientIdentified": patient_identified,
    "RequestValid": request_valid,
    "PlanComplete": plan_complete,
    "InPlan": in_plan,
    "IdentityVerified": identity_verified,
    "AttemptsAvailable": attempts_available,
    "IsIdempotent": is_idempotent,
    "ReadinessComplete": readiness_complete,
    "DeliveryConfirmed": delivery_confirmed,
    "ReadinessInProgress": readiness_in_progress,
    "PlanIntact": plan_intact,
    "CanAdvance": can_advance,
    "RetrievalStepsRemain": retrieval_steps_remain,
    "PreReadinessPhaseComplete": pre_readiness_phase_complete,
    "DeliveryStepPending": delivery_step_pending,
    "DocumentValid": document_valid,
    "WorkflowDecisionValid": workflow_decision_valid,
    "ExecutorReverified": executor_reverified,
    "PatientSlaExpired": patient_sla_expired,
    "SystemEscalationRequired": system_escalation_required,
    "AskPatientSafe": ask_patient_safe,
    "retrieval_action": retrieval_action,
    "delivery_action": delivery_action,
    "closure_reason": closure_reason,
    "valid_classification": valid_classification,
    "valid_tool_result": valid_tool_result,
    "deadline_registered": deadline_registered,
}
```

- [ ] **Step 6: Replace** `backend/hospital_agent/state_manager.py` with:

```python
"""State Manager - the only writer of State (spec §3, §12, §18.2; design §7).

apply() runs one transaction per event:

    read cases -> ownership check -> resolve the §3 row -> Temporal Monitor
    -> UPDATE cases WHERE state_version + INSERT audit_log (+ consume approval) -> COMMIT

A blocked event writes one Blocked audit row and changes nothing else. A stale
state_version rolls the whole transaction back and the event is re-processed
against the fresh row, at most MAX_REPROCESS times (design §12.2), then fails closed.

For the Tool Executor (Execution design §3) it also:
  - writes the executions 'intent' row with POLICY_ALLOWED (Effect.RECORD_EXECUTION_INTENT);
  - start_execution(): re-verifies the decision, increments attempt_count and writes the
    TOOL_EXECUTION_STARTED / AUDIT_RECORDED pair, in one transaction;
  - apply(..., execution_outcome=...): records a call's outcome row in the same
    transaction as the event that follows it.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.engine import Connection, Engine

from . import repository
from .case import MAX_ATTEMPTS, CaseRecord, ExecutionRecord, new_case
from .escalation import EscalationCoordinator
from .execution.verify import verify_start
from .fsm import Effect, Resolution, apply_effects, resolve
from .guards import GuardContext, GuardPorts
from .naming import (
    EVENT_OWNER,
    NON_TRANSITION_EVENTS,
    POLICY_DECISION_EVENTS,
    Component,
    EscalationKind,
    Event,
    State,
    canonical_event,
)
from .repository import AuditEntry

MAX_REPROCESS = 3
# §12.2 rule_version of the transition table. wiring.build_state_manager() appends the
# version of the policy files (policy.rego, rules.pl, flows.dl).
RULE_VERSION = "transitions-v1"


class TraceMonitor(Protocol):
    """The Temporal Monitor port (§7). The real one arrives in sub-project 2."""

    def check(self, trace: Sequence[AuditEntry], candidate: AuditEntry) -> str | None:
        """Return the violated rule id (e.g. "T1"), or None. Raising means unavailable: no commit."""


class CaseNotFound(LookupError):
    pass


class NonTransitionEvent(ValueError):
    """TOOL_EXECUTION_STARTED / AUDIT_RECORDED are recorded by the Tool Executor (sub-project 3)."""


class ReprocessLimitExceeded(RuntimeError):
    """The case kept changing underneath this event. Nothing was committed."""


class _StaleVersion(Exception):
    pass


class ExecutionStateError(RuntimeError):
    """An execution outcome for a row that is not running - a replayed or duplicate finish."""


# §12.2: the audit record_type of each outcome (Execution design decision 4).
OUTCOME_RECORD_TYPES = {"succeeded": "ExecutionSucceeded", "failed": "ExecutionFailed", "unknown": "ExecutionUnknown"}
OUTCOME_VALUES = {"succeeded": "success", "failed": "failed", "unknown": "unknown"}


@dataclass(frozen=True)
class ExecutionOutcome:
    """The result of one external call, recorded with the event that follows it."""

    execution_id: str
    status: str  # succeeded | failed | unknown
    reason: str | None = None


@dataclass(frozen=True)
class StartResult:
    started: bool
    reason: str | None = None  # executor_reverification_failed | attempts_exhausted | temporal_violation:<rule>
    temporal_violation: str | None = None
    execution: ExecutionRecord | None = None


POLICY_EVIDENCE_KEYS = frozenset({"ContentApprovalValid", "medical_content_flag"})


def _policy_evidence(event: Event, payload: Mapping[str, Any]) -> dict[str, bool]:
    """Evidence the Policy Service attaches to its decision row (§6.1: HumanAuthorized is the
    ContentApprovalValid evidence kept on the POLICY_ALLOWED row). Only Policy decisions may
    carry it - the owner check has already proven they come from the Policy Service. M1: only
    these two named keys are accepted (an unknown key, however boolean, is dropped) - the
    caller must never be able to forge or override an unrelated guard's result."""
    if event not in POLICY_DECISION_EVENTS:
        return {}
    evidence = payload.get("evidence") or {}
    return {str(k): v for k, v in evidence.items() if k in POLICY_EVIDENCE_KEYS and isinstance(v, bool)}


@dataclass(frozen=True)
class TransitionResult:
    case_id: str | None  # None only when REQUEST_SUBMITTED itself was rejected
    committed: bool
    state_before: State | None
    state_after: State | None  # the case's State after this call
    reason: str | None = None  # why it was blocked
    audit_id: int | None = None
    temporal_violation: str | None = None
    escalation: TransitionResult | None = None  # the TemporalViolation escalation that followed


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_case_id() -> str:
    return f"CASE-{uuid.uuid4().hex[:12].upper()}"


class StateManager:
    def __init__(
        self,
        engine: Engine,
        monitor: TraceMonitor,
        ports: GuardPorts,
        *,
        clock: Callable[[], datetime] = _utcnow,
        new_case_id: Callable[[], str] = _new_case_id,
        rule_version: str = RULE_VERSION,
    ) -> None:
        self.engine = engine
        self.monitor = monitor
        self.ports = ports
        self.clock = clock
        self.new_case_id = new_case_id
        self.rule_version = rule_version
        self.escalation = EscalationCoordinator(self)

    # --- public API ----------------------------------------------------------------

    def apply(
        self,
        case_id: str | None,
        event: Event | str,
        payload: Mapping[str, Any] | None = None,
        source: Component = Component.EXTERNAL,
        *,
        execution_outcome: ExecutionOutcome | None = None,
    ) -> TransitionResult:
        """Apply one event. case_id is None only for REQUEST_SUBMITTED.

        execution_outcome: the call this event reports on; its outcome row is written in
        the same transaction, whether the event commits or is blocked.
        """
        event = canonical_event(event)
        if event in NON_TRANSITION_EVENTS:
            raise NonTransitionEvent(f"{event} is recorded by the Tool Executor, not applied as a transition")
        payload = dict(payload or {})
        for _ in range(MAX_REPROCESS + 1):
            try:
                result = self._apply_once(case_id, event, payload, source, execution_outcome)
            except _StaleVersion:
                continue
            if result.temporal_violation and result.case_id and event is not Event.HUMAN_REVIEW_REQUIRED:
                follow_up = self.escalation.signal(
                    result.case_id, EscalationKind.TEMPORAL_VIOLATION, result.state_after, Component.TEMPORAL_MONITOR
                )
                result = replace(result, escalation=follow_up)
            return result
        raise ReprocessLimitExceeded(f"{event} on {case_id}: state_version kept changing")

    def record_blocked(self, case_id: str, event: Event, reason: str) -> TransitionResult:
        """Write a Blocked audit row without attempting a transition.

        Used by the Escalation Coordinator for an invalid signal, whose verdict does not
        depend on State: it loads `case` itself, in this same transaction, so there is no
        earlier possibly-stale read to reconcile - check_version is skipped (F4).
        """
        with self.engine.begin() as conn:
            case = repository.load_case(conn, case_id)
            if case is None:
                raise CaseNotFound(case_id)
            return self._block(conn, case, event, reason, self.clock(), check_version=False)

    def load(self, case_id: str) -> CaseRecord:
        with self.engine.connect() as conn:
            case = repository.load_case(conn, case_id)
        if case is None:
            raise CaseNotFound(case_id)
        return case

    # --- one attempt, one transaction ------------------------------------------------

    def _apply_once(
        self,
        case_id: str | None,
        event: Event,
        payload: dict[str, Any],
        source: Component,
        outcome: ExecutionOutcome | None = None,
    ) -> TransitionResult:
        now = self.clock()
        with self.engine.begin() as conn:
            case = None
            if case_id is not None:
                case = repository.load_case(conn, case_id)
                if case is None:
                    raise CaseNotFound(case_id)
            state = case.state if case else None

            owner = EVENT_OWNER.get(event)
            if owner is not None and source is not owner:
                return self._block(conn, case, event, "system_owned_event", now, payload=payload)
            if outcome is not None:
                # Before the guards run: DeliveryConfirmed reads the finished execution row.
                self._record_outcome(conn, case, event, outcome, now)

            ctx = GuardContext(
                case=case,
                event=event,
                payload=payload,
                now=now,
                ports=self.ports,
                source=source,
                approval=repository.load_approval(conn, payload["approval_id"]) if payload.get("approval_id") else None,
                execution=repository.load_execution(conn, payload["execution_id"]) if payload.get("execution_id") else None,
                approval_already_used=repository.approval_used(conn, payload["approval_id"])
                if payload.get("approval_id")
                else False,
            )
            resolution = resolve(state, event, ctx)
            if resolution.transition is None:
                return self._block(conn, case, event, resolution.reason, now, payload=payload)
            row = resolution.transition

            if case is None:
                after = new_case(self.new_case_id(), payload["patient_id"], now)
            else:
                after = replace(apply_effects(case, row, ctx), state_version=case.state_version + 1, updated_at=now)

            candidate = self._audit_entry(case, after, event, payload, resolution, now)
            trace = repository.load_trace(conn, after.case_id) if case else []
            violation = self.monitor.check(trace, candidate)
            if violation:
                blocked = self._block(conn, case, event, f"temporal_violation:{violation}", now, payload=payload)
                return replace(blocked, temporal_violation=violation)

            if case is None:
                repository.insert_case(conn, after)
            elif repository.update_case(conn, after, expected_version=case.state_version) == 0:
                raise _StaleVersion(case.case_id)
            audit_id = repository.insert_audit(conn, candidate)
            if Effect.RECORD_EXECUTION_INTENT in row.effects:
                repository.insert_execution(conn, self._execution_intent(after, payload))
            if Effect.CONSUME_APPROVAL in row.effects:
                if repository.consume_approval(conn, ctx.approval.approval_id, now) == 0:
                    raise _StaleVersion(case.case_id)
            override_id = payload.get("policy_review_override_id")
            if event in POLICY_DECISION_EVENTS and override_id:
                # Policy design decision 4: a PolicyReview override is consumed by the next
                # Policy decision, whatever it is, in the same transaction.
                if repository.consume_approval(conn, override_id, now) == 0:
                    raise _StaleVersion(case.case_id)
            return TransitionResult(after.case_id, True, state, row.target, audit_id=audit_id)

    # --- the Tool Executor's writes (Execution design §3) --------------------------------

    def start_execution(self, case_id: str, execution_id: str) -> StartResult:
        """Everything that must be true, and recorded, before an external call - one transaction.

        Re-verifies the decision against the executions 'intent' row (ExecutorReverified),
        checks AttemptsAvailable on the count BEFORE the increment, increments attempt_count,
        consumes a ContentApproval for medical output, and writes the TOOL_EXECUTION_STARTED /
        AUDIT_RECORDED pair - both checked by the Temporal Monitor before the commit.
        """
        for _ in range(MAX_REPROCESS + 1):
            try:
                return self._start_once(case_id, execution_id)
            except _StaleVersion:
                continue
        raise ReprocessLimitExceeded(f"start of {execution_id} on {case_id}: state_version kept changing")

    def _start_once(self, case_id: str, execution_id: str) -> StartResult:
        now = self.clock()
        with self.engine.begin() as conn:
            case = repository.load_case(conn, case_id)
            if case is None:
                raise CaseNotFound(case_id)
            execution = repository.load_execution(conn, execution_id)
            approval = (repository.load_approval(conn, execution.approval_id)
                        if execution is not None and execution.approval_id else None)
            started_payload = {"execution_id": execution_id}

            reason = verify_start(case, execution, approval, now)
            if reason is None and case.attempt_count >= MAX_ATTEMPTS:
                reason = "attempts_exhausted"
            if reason is not None:
                if execution is not None:
                    repository.set_execution_status(conn, execution_id, ("intent",), "failed", now)
                self._block(conn, case, Event.TOOL_EXECUTION_STARTED, reason, now, payload=started_payload)
                return StartResult(False, reason, execution=execution)

            after = replace(case, attempt_count=case.attempt_count + 1,
                            state_version=case.state_version + 1, updated_at=now)
            evidence = {"InPlan": True, "IdentityVerified": True, "PatientContextPresent": True,
                        "AttemptsAvailable": True, "medical_content_flag": execution.medical_content_flag}
            if execution.medical_content_flag:
                evidence["ContentApprovalValid"] = True
            common = dict(case_id=case.case_id, patient_id=case.patient_id, record_type="ExecutionStarted",
                          state_before=case.state.value, state_after=case.state.value,
                          rule_version=self.rule_version, recorded_at=now, action=execution.action,
                          execution_id=execution_id, attempt_number=after.attempt_count,
                          retry_cycle=after.retry_cycle, content_hash=execution.content_hash,
                          approval_id=execution.approval_id)
            started = AuditEntry(event=Event.TOOL_EXECUTION_STARTED.value, guards=evidence, **common)
            recorded = AuditEntry(event=Event.AUDIT_RECORDED.value, **common)

            trace = repository.load_trace(conn, case_id)
            violation = self.monitor.check(trace, started) or self.monitor.check([*trace, started], recorded)
            if violation:
                repository.set_execution_status(conn, execution_id, ("intent",), "failed", now)
                reason = f"temporal_violation:{violation}"
                self._block(conn, case, Event.TOOL_EXECUTION_STARTED, reason, now, payload=started_payload)
                return StartResult(False, reason, temporal_violation=violation, execution=execution)

            if repository.update_case(conn, after, expected_version=case.state_version) == 0:
                raise _StaleVersion(case_id)
            if repository.set_execution_status(conn, execution_id, ("intent",), "started", now) == 0:
                raise _StaleVersion(case_id)
            if execution.medical_content_flag and repository.consume_approval(conn, execution.approval_id, now) == 0:
                raise _StaleVersion(case_id)
            repository.insert_audit(conn, started)
            repository.insert_audit(conn, recorded)
            return StartResult(True, execution=execution)

    def _record_outcome(self, conn: Connection, case: CaseRecord, event: Event,
                        outcome: ExecutionOutcome, now: datetime) -> None:
        """Finish the executions row and write its outcome audit row (§12.2)."""
        execution = repository.load_execution(conn, outcome.execution_id)
        if execution is None or execution.case_id != case.case_id:
            raise ExecutionStateError(f"no execution {outcome.execution_id} on {case.case_id}")
        if repository.set_execution_status(conn, outcome.execution_id, ("started",), outcome.status, now) == 0:
            raise ExecutionStateError(f"execution {outcome.execution_id} is not running")
        repository.insert_audit(conn, AuditEntry(
            case_id=case.case_id,
            patient_id=case.patient_id,
            record_type=OUTCOME_RECORD_TYPES[outcome.status],
            event=event.value,
            state_before=case.state.value,
            state_after=case.state.value,
            rule_version=self.rule_version,
            recorded_at=now,
            action=execution.action,
            execution_id=execution.execution_id,
            policy_reasons=[outcome.reason] if outcome.reason else [],
            attempt_number=execution.attempt_number,
            retry_cycle=execution.retry_cycle,
            outcome=OUTCOME_VALUES[outcome.status],
            content_hash=execution.content_hash,
        ))

    @staticmethod
    def _execution_intent(after: CaseRecord, payload: Mapping[str, Any]) -> ExecutionRecord:
        """The outbox row POLICY_ALLOWED writes: the decision and what it is bound to (§3.1)."""
        attempt_number = after.attempt_count + 1
        evidence = payload.get("evidence") or {}
        return ExecutionRecord(
            execution_id=payload["execution_id"],
            case_id=after.case_id,
            patient_id=after.patient_id,
            action=after.current_action.value,
            step=after.current_step,
            retry_cycle=after.retry_cycle,
            attempt_number=attempt_number,
            idempotency_key=f"{after.case_id}:{after.current_step}:{after.retry_cycle}:{attempt_number}",
            status="intent",
            decision_token=payload["decision_token"],
            state_version=after.state_version,
            plan_hash=after.plan_hash,
            approval_id=payload.get("content_approval_id"),
            content_hash=payload.get("content_hash"),
            medical_content_flag=evidence.get("medical_content_flag") is True,
        )

    def _block(
        self,
        conn: Connection,
        case: CaseRecord | None,
        event: Event,
        reason: str,
        now: datetime,
        *,
        check_version: bool = True,
        payload: Mapping[str, Any] | None = None,
    ) -> TransitionResult:
        if case is None:  # a rejected REQUEST_SUBMITTED: there is no case row to attach an audit row to
            return TransitionResult(None, False, None, None, reason=reason)
        if check_version and repository.confirm_version(conn, case.case_id, case.state_version) == 0:
            # F4: the row moved on since `case` was read - reprocess against the fresh State
            # instead of writing a Blocked verdict that may no longer be correct.
            raise _StaleVersion(case.case_id)
        entry = AuditEntry(
            case_id=case.case_id,
            patient_id=case.patient_id,
            record_type="Blocked",
            event=event.value,
            state_before=case.state.value,
            state_after=case.state.value,
            rule_version=self.rule_version,
            recorded_at=now,
            policy_reasons=[reason],
            attempt_number=case.attempt_count,
            retry_cycle=case.retry_cycle,
            # M1: keep approval_id/execution_id from the payload; record_blocked() has no
            # payload (the Escalation Coordinator's invalid-signal path), so both stay None.
            approval_id=payload.get("approval_id") if payload else None,
            execution_id=payload.get("execution_id") if payload else None,
        )
        audit_id = repository.insert_audit(conn, entry)
        return TransitionResult(case.case_id, False, case.state, case.state, reason=reason, audit_id=audit_id)

    def _audit_entry(
        self,
        before: CaseRecord | None,
        after: CaseRecord,
        event: Event,
        payload: Mapping[str, Any],
        resolution: Resolution,
        now: datetime,
    ) -> AuditEntry:
        proposal = payload.get("proposed_action")
        action = payload.get("action") or (proposal.get("action") if isinstance(proposal, Mapping) else None)
        if action is None and before is not None and before.current_action is not None:
            action = before.current_action.value
        return AuditEntry(
            case_id=after.case_id,
            patient_id=after.patient_id,
            record_type="Transition",
            event=event.value,
            state_before=before.state.value if before else None,
            state_after=after.state.value,
            rule_version=self.rule_version,
            recorded_at=now,
            # M1: real guard results win on a name clash - evidence can only add, never override.
            guards={**_policy_evidence(event, payload), **resolution.guard_results},
            action=action,
            execution_id=payload.get("execution_id"),
            policy_result=payload.get("policy_result"),
            policy_reasons=list(payload.get("policy_reasons", [])),
            attempt_number=after.attempt_count,
            retry_cycle=after.retry_cycle,
            approval_id=payload.get("approval_id"),
            content_hash=payload.get("content_hash"),
        )
```

- [ ] **Step 7: Replace** `backend/hospital_agent/escalation.py` with:

```python
"""Escalation Coordinator - the only emitter of HUMAN_REVIEW_REQUIRED (spec §3.1, §13.2).

Internal components never emit HUMAN_REVIEW_REQUIRED themselves: they send a signal
here. The signal becomes the canonical event only if it comes from an authorized
internal component, names a known escalation kind, and names the current State as
its origin; the §3 row then checks the kind against that State's allowlist.
Anything else is Blocked: invalid_escalation_reason.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .guards import INVALID_ESCALATION_REASON
from .naming import ESCALATION_SIGNAL_SOURCES, Component, EscalationKind, Event, State

if TYPE_CHECKING:
    from .state_manager import ExecutionOutcome, StateManager, TransitionResult


class EscalationCoordinator:
    def __init__(self, state_manager: StateManager) -> None:
        self._state_manager = state_manager

    def signal(
        self,
        case_id: str,
        kind: EscalationKind | str,
        from_state: State | str,
        source: Component,
        reasons: Sequence[str] = (),
        execution_outcome: ExecutionOutcome | None = None,
    ) -> TransitionResult:
        """`reasons` (e.g. a Z3 counterexample) are kept in the escalation row's policy_reasons.

        `execution_outcome`: the Tool Executor's call this escalation reports on (a
        non-idempotent failure, or an unknown outcome after a restart) - recorded in the
        same transaction as the escalation.
        """
        valid_kind = kind in {k.value for k in EscalationKind}
        valid_state = from_state in {s.value for s in State}
        if source not in ESCALATION_SIGNAL_SOURCES or not valid_kind or not valid_state:
            return self._state_manager.record_blocked(case_id, Event.HUMAN_REVIEW_REQUIRED, INVALID_ESCALATION_REASON)
        payload = {"escalation_kind": str(kind), "escalated_from_state": str(from_state),
                   "policy_reasons": list(reasons)}
        return self._state_manager.apply(case_id, Event.HUMAN_REVIEW_REQUIRED, payload, Component.ESCALATION_COORDINATOR,
                                         execution_outcome=execution_outcome)
```

- [ ] **Step 8: Replace** `backend/hospital_agent/policy/temporal.py` with the version below. Outcome rows leave the trace:

```python
"""Temporal Monitor - the twelve past-time rules of spec §6.2 over a case's audit trace.

It implements the Core's TraceMonitor port: check(trace, candidate) runs before every
commit (§7) and returns the id of a rule the candidate would newly violate, or None.

Trace (§6.1, Policy design decision 1): the case's audit rows in order, without
Blocked rows (a blocked event is not a transition) and without the execution outcome
rows (Execution design decision 4). Each row is read as
(state_after, event, guards, execution_id, content_hash).

Operators (§6.1): Y = previous row (false on the first row), O = now or earlier,
X = next row. At the end of a trace X of a terminal state holds (the terminal state
repeats); X of any other state is still pending and is judged when the next row
arrives - which is exactly when check() sees it.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..repository import AuditEntry

# Policy design decision 1 + Execution design decision 4: a blocked event and a call's
# outcome record are not transitions of the trace.
EXCLUDED_RECORD_TYPES = frozenset({"Blocked", "ExecutionSucceeded", "ExecutionFailed", "ExecutionUnknown"})
TERMINAL = frozenset({"Completed", "Failed"})

Rows = Sequence[AuditEntry]


@dataclass(frozen=True)
class Violation:
    rule: str
    index: int  # position in the trace (excluded rows removed)


# --- atomic propositions (§6.1 table) ------------------------------------------------


def _guard(row: AuditEntry, name: str) -> bool:
    return row.guards.get(name) is True


def _execute(row: AuditEntry) -> bool:
    return row.event == "TOOL_EXECUTION_STARTED"


def _policy_allowed(row: AuditEntry, execution_id: str | None) -> bool:
    return row.event == "POLICY_ALLOWED" and row.execution_id == execution_id


def _human_review(row: AuditEntry) -> bool:
    return row.state_after == "AwaitingHumanReview"


def _human_authorized(row: AuditEntry, execution_id: str | None, content_hash: str | None) -> bool:
    return (row.event == "POLICY_ALLOWED" and _guard(row, "ContentApprovalValid")
            and row.execution_id == execution_id and row.content_hash == content_hash)


def _audit_recorded(row: AuditEntry, execution_id: str | None) -> bool:
    return row.event == "AUDIT_RECORDED" and row.execution_id == execution_id


def _next(rows: Rows, i: int, holds: Callable[[AuditEntry], bool], at_end: bool) -> bool:
    return holds(rows[i + 1]) if i + 1 < len(rows) else at_end


# --- the rules: each answers "does rule hold at position i?" -------------------------


def _t1(rows: Rows, i: int) -> bool:  # G(Execute(e) -> Y PolicyAllowed(e))
    r = rows[i]
    return not _execute(r) or (i > 0 and _policy_allowed(rows[i - 1], r.execution_id))


def _t2(rows: Rows, i: int) -> bool:  # G(Execute(e) -> InPlan)
    return not _execute(rows[i]) or _guard(rows[i], "InPlan")


def _t3(rows: Rows, i: int) -> bool:  # G(Execute(e) -> IdentityVerified)
    return not _execute(rows[i]) or _guard(rows[i], "IdentityVerified")


def _t4(rows: Rows, i: int) -> bool:  # G(Execute(e) -> PatientContextPresent)
    return not _execute(rows[i]) or _guard(rows[i], "PatientContextPresent")


def _t5(rows: Rows, i: int) -> bool:  # G(MEDICAL_QUESTION_DETECTED -> HumanReview)
    return rows[i].event != "MEDICAL_QUESTION_DETECTED" or _human_review(rows[i])


def _t6(rows: Rows, i: int) -> bool:  # G(MedicalAnswer(e,h) -> (ContentApprovalValid(e,h) & Y O HumanAuthorized(e,h)))
    r = rows[i]
    if not (_execute(r) and _guard(r, "medical_content_flag")):
        return True
    earlier = any(_human_authorized(rows[j], r.execution_id, r.content_hash) for j in range(i))
    return _guard(r, "ContentApprovalValid") and earlier


def _t7(rows: Rows, i: int) -> bool:  # G(RETRY_EXHAUSTED -> HumanReview)
    return rows[i].event != "RETRY_EXHAUSTED" or _human_review(rows[i])


def _t8(rows: Rows, i: int) -> bool:  # G(Ready -> ReadinessComplete)
    return rows[i].state_after != "Ready" or _guard(rows[i], "ReadinessComplete")


def _t9(rows: Rows, i: int) -> bool:  # G(Execute(e) -> X AuditRecorded(e))
    r = rows[i]
    return not _execute(r) or _next(rows, i, lambda n: _audit_recorded(n, r.execution_id), at_end=True)


def _t10(rows: Rows, i: int) -> bool:  # G((DOCUMENT_UPLOADED & DocumentValid) -> Classifying)
    r = rows[i]
    return not (r.event == "DOCUMENT_UPLOADED" and _guard(r, "DocumentValid")) or r.state_after == "Classifying"


def _t11(rows: Rows, i: int) -> bool:  # G(Terminal -> X Terminal)
    return rows[i].state_after not in TERMINAL or _next(rows, i, lambda n: n.state_after in TERMINAL, at_end=True)


def _t12(rows: Rows, i: int) -> bool:  # G(Execute(e) -> AttemptsAvailable)
    return not _execute(rows[i]) or _guard(rows[i], "AttemptsAvailable")


RULES: tuple[tuple[str, Callable[[Rows, int], bool]], ...] = (
    ("T1", _t1), ("T2", _t2), ("T3", _t3), ("T4", _t4), ("T5", _t5), ("T6", _t6),
    ("T7", _t7), ("T8", _t8), ("T9", _t9), ("T10", _t10), ("T11", _t11), ("T12", _t12),
)


def trace_rows(entries: Rows) -> list[AuditEntry]:
    return [e for e in entries if e.record_type not in EXCLUDED_RECORD_TYPES]


def violations(entries: Rows) -> list[Violation]:
    """Every (rule, position) that does not hold on this trace."""
    rows = trace_rows(entries)
    return [Violation(rule, i) for i in range(len(rows)) for rule, holds in RULES if not holds(rows, i)]


class TemporalMonitor:
    """The TraceMonitor port of the State Manager (Core design §7)."""

    def check(self, trace: Rows, candidate: AuditEntry) -> str | None:
        if candidate.record_type in EXCLUDED_RECORD_TYPES:
            return None
        before = set(violations(trace))
        for violation in violations([*trace, candidate]):
            if violation not in before:
                return violation.rule
        return None
```

- [ ] **Step 9: Replace** `backend/hospital_agent/policy/readiness.py` with the version below. `hours_until` now comes from the stored `appointment_at`:

```python
"""Readiness feasibility with Z3 (spec §9.1) and the Readiness Check that acts on it.

ask_patient_is_safe() is the §9.1 model unchanged: it searches for a legal SLA
scenario in which an uploaded document would NOT be verified and reviewed before
the appointment. Only UNSAT (no such scenario) makes asking the patient safe;
sat, unknown, a timeout, a Z3 error or an invalid deadline all escalate.

The spec injects an audit service into the Z3 function; here the verdict is
returned and the Readiness Check records it (Policy design decision 2): an
escalation keeps the counterexample in its audit row's policy_reasons.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import isfinite

from z3 import And, Ints, Or, Solver, Z3Exception, sat, unsat

from ..naming import Component, EscalationKind, Event, State
from ..state_manager import StateManager, TransitionResult

Z3_TIMEOUT_MS = 5000
# Policy design decision 3: the longest legal upload window in the §9.1 model.
PATIENT_UPLOAD_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Z3Verdict:
    safe: bool
    result: str  # unsat | sat | unknown | invalid_deadline | error
    detail: str | None = None  # the counterexample model, or the reason Z3 gave


def ask_patient_is_safe(hours_until: object) -> Z3Verdict:
    if type(hours_until) not in (int, float) or not isfinite(hours_until) or hours_until < 0:
        return Z3Verdict(False, "invalid_deadline")
    try:
        upload_h, verify_h, review_h, doc_type = Ints("upload_h verify_h review_h doc_type")
        s = Solver()
        s.set(timeout=Z3_TIMEOUT_MS)
        s.add(review_h >= 0, review_h <= 4)
        s.add(Or(
            And(doc_type == 1, upload_h >= 0, upload_h <= 24, verify_h >= 1, verify_h <= 2),
            And(doc_type == 2, upload_h >= 0, upload_h <= 8, verify_h >= 4, verify_h <= 8)))
        s.add(upload_h + verify_h + review_h > hours_until)
        result = s.check()
        if result == unsat:
            return Z3Verdict(True, "unsat")
        if result == sat:
            return Z3Verdict(False, "sat", str(s.model()))
        return Z3Verdict(False, "unknown", s.reason_unknown())
    except Z3Exception as exc:
        return Z3Verdict(False, "error", str(exc))


class ReadinessCheck:
    """Runs in AssessingReadiness and emits the §3 outcome (Readiness Check, inside the Policy Service)."""

    def __init__(self, state_manager: StateManager, *, z3=ask_patient_is_safe) -> None:
        self.state_manager, self.z3 = state_manager, z3

    def run(self, case_id: str) -> TransitionResult:
        """hours_until is derived from the appointment time CheckAppointment stored (Execution
        design §5) - never taken from a caller. No stored appointment -> invalid_deadline."""
        sm = self.state_manager
        case = sm.load(case_id)
        if case.readiness_complete:
            return sm.apply(case_id, Event.READINESS_PASSED, {}, Component.READINESS_CHECK)
        hours_until = (None if case.appointment_at is None
                       else (case.appointment_at - sm.clock()).total_seconds() / 3600)
        verdict = self.z3(hours_until)
        if verdict.safe:
            payload = {"z3_result": verdict.result, "patient_deadline": sm.clock() + PATIENT_UPLOAD_WINDOW}
            return sm.apply(case_id, Event.MISSING_INFORMATION_DETECTED, payload, Component.READINESS_CHECK)
        reasons = [f"z3:{verdict.result}"] + ([f"z3_detail:{verdict.detail}"] if verdict.detail else [])
        return sm.escalation.signal(case_id, EscalationKind.Z3_COUNTEREXAMPLE, State.ASSESSING_READINESS,
                                    Component.READINESS_CHECK, reasons=reasons)
```

- [ ] **Step 10: Replace** `backend/hospital_agent/wiring.py` with:

```python
"""How application code builds a State Manager: always with the real Temporal Monitor and
the real ExecutorReverified (Execution design §3.1).

There is no other production path - a State Manager with a permissive monitor or port
exists only in tests. rule_version records the transition table's
version plus a hash of the policy files in force (§12.2).
"""
from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

from sqlalchemy.engine import Engine

from .execution.verify import verify_decision
from .guards import GuardPorts
from .policy.temporal import TemporalMonitor
from .state_manager import RULE_VERSION, StateManager

POLICY_DIR = Path(__file__).with_name("policy")
POLICY_FILES = ("policy.rego", "rules.pl", "flows.dl")


@cache
def policy_version() -> str:
    digest = hashlib.sha256()
    for name in POLICY_FILES:
        digest.update((POLICY_DIR / name).read_bytes())
    return digest.hexdigest()[:12]


def build_state_manager(engine: Engine) -> StateManager:
    """The real Temporal Monitor and the real ExecutorReverified - there is no other port left."""
    return StateManager(engine, TemporalMonitor(), GuardPorts(executor_reverified=verify_decision),
                        rule_version=f"{RULE_VERSION}+policy-{policy_version()}")
```

- [ ] **Step 11: Replace the test support files**

`backend/tests/conftest.py`:

```python
"""Shared fixtures. Database tests run inside Docker against the hospital_test database:

    docker compose run --rm backend pytest
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from hospital_agent.state_manager import StateManager
from hospital_agent.wiring import build_state_manager

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} is not set - run the tests in Docker: docker compose run --rm backend pytest", pytrace=False)
    return value


@pytest.fixture(scope="session")
def owner_engine() -> Engine:
    """The database owner (hospital_owner): runs migrations and cleans tables between tests."""
    engine = create_engine(_env("TEST_MIGRATION_DATABASE_URL"))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def migrated(owner_engine: Engine) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["url"] = _env("TEST_MIGRATION_DATABASE_URL")
    config.attributes["configure_logger"] = False
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.fixture
def app_engine(migrated: None, owner_engine: Engine) -> Engine:
    """A clean database, reached as hospital_app - the role the application uses."""
    with owner_engine.begin() as conn:
        conn.execute(text("TRUNCATE audit_log, approvals, executions, cases RESTART IDENTITY"))
    engine = create_engine(_env("TEST_DATABASE_URL"))
    yield engine
    engine.dispose()


@pytest.fixture
def sm(app_engine: Engine) -> StateManager:
    """The production State Manager: real Temporal Monitor and real ExecutorReverified."""
    return build_state_manager(app_engine)
```

`backend/tests/test_policy_core.py`:

```python
"""The Core changes sub-project 2 needs (Policy design §6, §7): rule_version, decision
evidence, override consumption, escalation reasons, CaseRecord.readiness_complete.

Policy decisions are emitted directly here (as the Policy Service would), so these
tests do not depend on the engines.
"""
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from hospital_agent.case import new_case
from hospital_agent.naming import Component, EscalationKind, Event, State
from hospital_agent.state_manager import RULE_VERSION, ReprocessLimitExceeded
from hospital_agent.wiring import build_state_manager, policy_version
from tests.driver import Driver


def decision(sm, d: Driver, event: Event, **payload):
    """A decision as the Policy Service emits it, bound to the case as it is now (ExecutorReverified)."""
    case = d.case
    base = {"action": case.current_action.value, "policy_result": "Allow", "policy_reasons": [],
            "execution_id": f"EXEC-{case.state_version}", "decision_token": "tok",
            "decided_state_version": case.state_version, "plan_hash": case.plan_hash,
            "current_step": case.current_step}
    return sm.apply(d.case_id, event, {**base, **payload}, Component.POLICY_SERVICE)


def test_readiness_complete_property():
    case = new_case("CASE-1", "P-1", datetime.now(UTC))
    assert not case.readiness_complete
    assert replace(case, required_documents=["a"], held_documents=["a", "b"]).readiness_complete
    assert not replace(case, required_documents=["a", "c"], held_documents=["a"]).readiness_complete


def test_audit_rows_carry_the_policy_version(app_engine):
    d = Driver(build_state_manager(app_engine), app_engine)
    d.submit()
    assert len(policy_version()) == 12
    assert d.trace()[0].rule_version == f"{RULE_VERSION}+policy-{policy_version()}"


def test_policy_decision_rows_keep_the_evidence(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    decision(sm, d, Event.POLICY_ALLOWED, evidence={"ContentApprovalValid": True, "medical_content_flag": False,
                                                    "not_a_bool": "x"})
    row = d.trace()[-1]
    assert row.guards == {"ExecutorReverified": True, "retrieval_action": True,
                          "ContentApprovalValid": True, "medical_content_flag": False}


def test_m1_evidence_cannot_forge_or_override_a_real_guard_result(sm, app_engine):
    """M1: evidence may only ever add ContentApprovalValid/medical_content_flag - an unknown key
    is dropped, and a name clash with a real guard result is decided by the real result."""
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    decision(sm, d, Event.POLICY_ALLOWED, evidence={"ExecutorReverified": False, "SomeOtherFlag": True,
                                                    "ContentApprovalValid": True, "medical_content_flag": False})
    row = d.trace()[-1]
    assert row.guards == {"ExecutorReverified": True, "retrieval_action": True,
                          "ContentApprovalValid": True, "medical_content_flag": False}


def test_evidence_on_other_events_is_ignored(sm, app_engine):
    """Only the Policy Service may attach evidence - an external event cannot forge HumanAuthorized."""
    d = Driver(sm, app_engine)
    d.submit()
    sm.apply(d.case_id, Event.REQUEST_VALIDATED, {"text": "x", "identity_verified": True,
                                                  "evidence": {"ContentApprovalValid": True}},
             Component.SESSION_SERVICE)
    assert "ContentApprovalValid" not in d.trace()[-1].guards


def test_escalation_reasons_are_audited(sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    d.validate()
    sm.escalation.signal(d.case_id, EscalationKind.SAFETY_ESCALATION, State.CLASSIFYING,
                         Component.CLASSIFIER_SERVICE, reasons=["safety:HighRisk"])
    assert d.trace()[-1].policy_reasons == ["safety:HighRisk"]


def _approved_policy_review(sm, d: Driver) -> str:
    d.to_classified()
    d.plan()
    d.propose()
    decision(sm, d, Event.POLICY_HUMAN_REVIEW_REQUIRED, policy_result="RequireHumanReview")
    case = d.case
    approval_id = d.approval("approve", plan_hash=case.plan_hash, current_step=case.current_step)
    assert d.human(Event.HUMAN_APPROVED, approval_id).state_after is State.PLANNING
    d.propose()
    return approval_id


def _consumed_at(app_engine, approval_id):
    with app_engine.connect() as conn:
        return conn.execute(text("SELECT consumed_at FROM approvals WHERE approval_id = :id"),
                            {"id": approval_id}).scalar_one()


def test_the_next_policy_decision_consumes_the_override(sm, app_engine):
    """Policy design decision 4: consumed by the next Policy decision, in the same transaction."""
    d = Driver(sm, app_engine)
    approval_id = _approved_policy_review(sm, d)
    assert decision(sm, d, Event.POLICY_DENIED, policy_result="Deny",
                    policy_review_override_id=approval_id).committed
    assert _consumed_at(app_engine, approval_id) is not None


def test_an_already_consumed_override_fails_closed(sm, app_engine):
    d = Driver(sm, app_engine)
    approval_id = _approved_policy_review(sm, d)
    decision(sm, d, Event.POLICY_ALLOWED, policy_review_override_id=approval_id)
    d.retrieved()
    d.advance()
    d.propose()
    with pytest.raises(ReprocessLimitExceeded):
        decision(sm, d, Event.POLICY_ALLOWED, policy_review_override_id=approval_id)
    assert (d.state, d.case.current_step) == (State.PLANNING, 2)
```

`backend/tests/test_policy_d_tests.py`:

```python
"""The §16 D-tests that the Policy layer proves (Policy design §9). D29 is in tests/test_prolog.py."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent import repository
from hospital_agent.case import ApprovalRecord
from hospital_agent.fsm import TRANSITIONS
from hospital_agent.naming import Event, EscalationKind, State
from hospital_agent.policy.service import InstructionSource, OutgoingMessage, PolicyService
from tests.driver import Driver

MEDICAL = OutgoingMessage(evaluated=True, medical_content_flag=True, content_hash="HASH-MED-1")


def at_step(d: Driver, step: int) -> None:
    """Drive a fresh case to Planning at `step` with the step proposed (docs held, so readiness passes)."""
    d.to_classified()
    d.plan()
    for _ in range(step - 1):
        if d.case.current_step == 3:
            break
        d.retrieve_step(required_documents=["referral"], held_documents=["referral"])
        d.advance()
    if step == 4:
        d.retrieve_step()                 # LoadInstructions -> AssessingReadiness
        d.assess()                        # everything held -> Ready
        d.plan_delivery()
    d.propose()
    assert (d.state, d.case.current_step) == (State.PLANNING, step)


def reasons(d: Driver) -> list[str]:
    return d.trace()[-1].policy_reasons


def content_approval(d: Driver, execution_id: str, **changes) -> ApprovalRecord:
    now = datetime.now(UTC)
    base = ApprovalRecord(
        approval_id="APPR-C1", approval_type="ContentApproval", case_id=d.case_id, patient_id=d.patient_id,
        reviewer_id="coordinator_nurse", reviewer_role="clinical_staff", decision="approve",
        reason="message checked", shown_context_ref="ctx-C1", granted_at=now - timedelta(minutes=1),
        valid_until=now + timedelta(hours=1), execution_id=execution_id, action="SendStatusUpdate",
        content_hash=MEDICAL.content_hash)
    return replace(base, **changes)


def insert_approval(app_engine, approval: ApprovalRecord) -> str:
    """F2: the Policy Service loads a content approval by id - the caller inserts the row first."""
    with app_engine.begin() as conn:
        repository.insert_approval(conn, approval)
    return approval.approval_id


def test_d3_proposal_outside_the_plan_is_denied(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 2)
    request = d.request()
    result = d.allow(proposed_action=replace(request.proposed_action, from_step=3))
    assert (result.state_after, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.POLICY_DENIED)
    assert "action_not_in_plan" in reasons(d)


def test_d4_retrieval_before_identity_verification(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 2)
    decision = PolicyService(app_engine).decide(replace(d.case, identity_verified=False), d.request())
    assert decision.result == "Deny" and "identity_not_verified" in decision.reasons


def test_d5_missing_document_with_96_hours_asks_the_patient(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"])
    before = datetime.now(UTC)
    assert d.assess().state_after is State.AWAITING_PATIENT_INPUT
    assert timedelta(hours=23) < d.case.patient_deadline - before < timedelta(hours=25)


def test_d6_missing_document_with_20_hours_goes_to_a_human(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"], hours_until=20)
    assert d.assess().state_after is State.AWAITING_HUMAN_REVIEW
    assert d.case.escalation_kind is EscalationKind.Z3_COUNTEREXAMPLE
    audited = reasons(d)
    assert audited[0] == "z3:sat" and audited[1].startswith("z3_detail:") and "upload_h" in audited[1]


def test_d8_medical_output_without_content_approval(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    assert d.allow(outgoing_message=MEDICAL).state_after is State.AWAITING_HUMAN_REVIEW
    assert "medical_answer_attempt" in reasons(d)


def test_d10_empty_patient_id(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 2)
    decision = PolicyService(app_engine).decide(replace(d.case, patient_id=""), d.request())
    assert decision.result == "Deny" and "missing_patient_context" in decision.reasons


def test_d11_medical_output_with_a_matching_content_approval(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    request = d.request(outgoing_message=MEDICAL)
    approval_id = insert_approval(app_engine, content_approval(d, request.execution_id))
    request = replace(request, approval_id=approval_id)
    assert d.policy.apply(sm, d.case_id, request).state_after is State.DELIVERING
    row = d.trace()[-1]
    assert row.guards["ContentApprovalValid"] is True and row.content_hash == MEDICAL.content_hash


@pytest.mark.parametrize("change, extra", [
    ({"approval_type": "WorkflowDecision"}, "approval_is_workflow_only"),      # D13
    ({"valid_until": datetime(2020, 1, 1, tzinfo=UTC)}, None),                 # D13 expired
    ({"consumed_at": datetime(2026, 1, 1, tzinfo=UTC)}, None),                 # D13 used
    ({"content_hash": "HASH-OTHER"}, None),                                    # D13 another message
    ({"reviewer_role": "admin_staff"}, None),                                  # D22 role
])
def test_d13_d22_an_approval_not_bound_to_this_message_is_rejected(sm, app_engine, change, extra):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    request = d.request(outgoing_message=MEDICAL)
    approval_id = insert_approval(app_engine, content_approval(d, request.execution_id, **change))
    request = replace(request, approval_id=approval_id)
    assert d.policy.apply(sm, d.case_id, request).state_after is State.AWAITING_HUMAN_REVIEW
    assert "medical_answer_attempt" in reasons(d)
    if extra:
        assert extra in reasons(d)


def test_d22_an_approval_for_another_case(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    other = Driver(sm, app_engine, patient_id="P-99999")
    other.submit()  # a real case, so the approval's case_id FK is satisfiable
    request = d.request(outgoing_message=MEDICAL)
    approval_id = insert_approval(app_engine, content_approval(d, request.execution_id, case_id=other.case_id))
    request = replace(request, approval_id=approval_id)
    d.policy.apply(sm, d.case_id, request)
    assert "medical_answer_attempt" in reasons(d)


def test_f2_an_approval_id_not_in_the_table_is_treated_as_no_approval(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    request = d.request(outgoing_message=MEDICAL)
    request = replace(request, approval_id="APPR-DOES-NOT-EXIST")
    assert d.policy.apply(sm, d.case_id, request).state_after is State.AWAITING_HUMAN_REVIEW
    assert "medical_answer_attempt" in reasons(d)


def test_d16_document_id_to_the_appointment_system(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 1)
    request = d.request()
    d.allow(proposed_action=replace(request.proposed_action, patient_fields=("patient_id", "document_id")))
    assert d.state is State.AWAITING_HUMAN_REVIEW and "field_not_minimized" in reasons(d)


@pytest.mark.parametrize("source, expected", [
    (InstructionSource("INSTR-UNKNOWN", "3"), {"unapproved_instruction_source"}),
    (InstructionSource("INSTR-PREP-COLONOSCOPY", "2"), {"unapproved_instruction_source"}),
    (InstructionSource("INSTR-RETIRED-2025", "1"), {"unapproved_instruction_source", "instruction_source_expired"}),
])
def test_d17_instructions_only_from_the_approved_registry(sm, app_engine, source, expected):
    d = Driver(sm, app_engine)
    at_step(d, 3)
    d.allow(instruction_source=source)
    assert d.state is State.AWAITING_HUMAN_REVIEW and expected <= set(reasons(d))


def test_d18_high_risk_needs_a_scoped_one_shot_override(sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    d.validate()
    d.classify(safety_level="HighRisk")
    d.plan()
    d.propose()
    assert d.allow().state_after is State.AWAITING_HUMAN_REVIEW
    assert d.case.escalation_kind is EscalationKind.POLICY_REVIEW
    case = d.case
    d.human(Event.HUMAN_APPROVED, d.approval("approve", plan_hash=case.plan_hash, current_step=case.current_step))
    d.propose()
    assert d.allow().state_after is State.RETRIEVING_DATA          # the override, once
    d.retrieved()
    d.advance()
    d.propose()
    assert d.allow().state_after is State.AWAITING_HUMAN_REVIEW     # no reuse on the next step
    assert d.case.escalation_kind is EscalationKind.POLICY_REVIEW


def test_f1_override_is_only_the_approval_the_latest_human_approved_used(sm, app_engine):
    """F1: a double-submit leaves two open PolicyReview approvals for the same step. Only the
    one the latest committed HUMAN_APPROVED referenced may resume automation - the other
    stays open and cannot be silently picked up by a later Policy decision on the same step."""
    d = Driver(sm, app_engine)
    d.submit()
    d.validate()
    d.classify(safety_level="HighRisk")
    d.plan()
    d.propose()
    assert d.allow().state_after is State.AWAITING_HUMAN_REVIEW
    assert d.case.escalation_kind is EscalationKind.POLICY_REVIEW
    case = d.case
    older = d.approval("approve", plan_hash=case.plan_hash, current_step=case.current_step)
    newer = d.approval("approve", plan_hash=case.plan_hash, current_step=case.current_step)
    d.human(Event.HUMAN_APPROVED, older)
    d.propose()
    assert d.allow().state_after is State.RETRIEVING_DATA
    with app_engine.connect() as conn:
        older_row = repository.load_approval(conn, older)
        newer_row = repository.load_approval(conn, newer)
    assert older_row.consumed_at is not None
    assert newer_row.consumed_at is None
    d.transient_failure()
    d.propose()
    assert d.allow().state_after is State.AWAITING_HUMAN_REVIEW
    assert d.case.escalation_kind is EscalationKind.POLICY_REVIEW
    with app_engine.connect() as conn:
        newer_row = repository.load_approval(conn, newer)
    assert newer_row.consumed_at is None


def test_d30_a_temporal_violation_escalates_and_cannot_be_approved(sm, app_engine, monkeypatch):
    """A table bug lets Ready through without ReadinessComplete; the real monitor (T8) stops it."""
    buggy = tuple(replace(t, guards=()) if t.event is Event.READINESS_PASSED else t for t in TRANSITIONS)
    monkeypatch.setattr("hospital_agent.fsm.TRANSITIONS", buggy)
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"])
    result = d.readiness_passed()
    assert (result.committed, result.temporal_violation) == (False, "T8")
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.TEMPORAL_VIOLATION)
    assert not d.human(Event.HUMAN_APPROVED, d.approval("approve")).committed
    assert d.human(Event.HUMAN_REJECTED, d.approval("reject")).state_after is State.FAILED


def test_d31_plan_changed_after_plan_created(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 2)
    case = d.case
    tampered = replace(case, ordered_steps=[*case.ordered_steps[:3], {"step": 4, "action": "CheckAppointment"}])
    decision = PolicyService(app_engine).decide(tampered, d.request())
    assert decision.result == "Deny" and "plan_modified" in decision.reasons
```

- [ ] **Step 12: Edit** `backend/tests/driver.py` (Task 5 replaces this file; these edits keep it working until then).

In `Driver.__init__`, replace

```python
        self.policy = PolicyService(engine)
```

with

```python
        self.policy = PolicyService(engine)
        self.last_execution_id: str | None = None
```

Replace the body of `allow`

```python
        """The real Policy Service decides the current step (Allow for a well-formed request)."""
        return self.policy.apply(self.sm, self.case_id, self.request(**overrides))
```

with

```python
        """The real Policy Service decides the current step (Allow for a well-formed request)."""
        request = self.request(**overrides)
        self.last_execution_id = request.execution_id
        return self.policy.apply(self.sm, self.case_id, request)
```

Replace

```python
    def assess(self, hours_until: object = 96) -> TransitionResult:
```

with

```python
    def assess(self) -> TransitionResult:
```

and, in its body,

```python
        return ReadinessCheck(self.sm).run(self.case_id, hours_until)
```

with

```python
        return ReadinessCheck(self.sm).run(self.case_id)
```

Replace

```python
    def to_assessing_readiness(self, required: list[str], held: list[str]) -> None:
        self.to_classified()
        self.plan()
        self.retrieve_step()
```

with

```python
    def to_assessing_readiness(self, required: list[str], held: list[str], hours_until: float = 96) -> None:
        self.to_classified()
        self.plan()
        self.retrieve_step(appointment_at=datetime.now(UTC) + timedelta(hours=hours_until))
```

(`datetime`, `UTC` and `timedelta` are already imported there.)

- [ ] **Step 13: Edit** `backend/tests/test_scenarios.py` (Task 7 replaces this file).

Put this line directly above `from hospital_agent.naming import NON_TRANSITION_EVENTS, Event, State`, followed by a blank line:

```python
from datetime import UTC, datetime, timedelta
```

In `test_scenario_1_normal_flow` and `test_scenario_3_technical_failure`, replace

```python
    d.retrieve_step()                                   # CheckAppointment
```

with

```python
    d.retrieve_step(appointment_at=datetime.now(UTC) + timedelta(hours=96))  # CheckAppointment
```

Replace all four `d.assess(96)` calls with `d.assess()`.

In `test_scenario_3_technical_failure`, replace the docstring (the one starting `"""F5: nothing in the Core increments attempt_count`) with

```python
    """Each attempt is started through the State Manager, which counts it (attempt_count)
    and writes the ExecutionStarted pair; the Tool Executor that also makes the call and
    records its outcome replaces this in obs.golden."""
```

and replace

```python
        d.allow()
        d.transient_failure()
    d.propose()
    d.allow()
    d.retry_exhausted()
```

with

```python
        d.allow()
        d.sm.start_execution(d.case_id, d.last_execution_id)
        d.transient_failure()
    d.propose()
    d.allow()
    d.sm.start_execution(d.case_id, d.last_execution_id)
    d.retry_exhausted()
```

- [ ] **Step 14: Run the new test**

Run: `docker compose run --rm backend pytest tests/test_execution_core.py -v`
Expected: all PASS.

- [ ] **Step 15: Run the whole suite**

Run: `docker compose run --rm backend pytest -q`
Expected: `328 passed`.

- [ ] **Step 16: Commit**

```bash
git add backend/hospital_agent/fsm.py backend/hospital_agent/guards.py backend/hospital_agent/state_manager.py backend/hospital_agent/escalation.py backend/hospital_agent/wiring.py backend/hospital_agent/policy/service.py backend/hospital_agent/policy/temporal.py backend/hospital_agent/policy/readiness.py backend/tests/conftest.py backend/tests/test_policy_core.py backend/tests/test_policy_d_tests.py backend/tests/driver.py backend/tests/test_scenarios.py backend/tests/test_execution_core.py
git commit -m "Record execution intent, start and outcome in the State Manager

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The Tool Executor and the scripted demo components

**Files:**
- Create: `backend/hospital_agent/execution/executor.py`, `backend/hospital_agent/scripted.py`
- Modify (full new content below): `backend/tests/driver.py`
- Test: `backend/tests/test_execution.py`

**Interfaces:**
- Consumes:
  - `StateManager.start_execution`, `.apply(..., execution_outcome=)`, `.escalation.signal(..., execution_outcome=)`, `.load`;
  - `gateway.ACTION_TARGETS`, `ToolGateway`, `ToolResult`, `OK`, `TRANSIENT_FAILURE`;
  - `retry.after_failure`, `verify.EXECUTING_STATES`;
  - `PolicyService.apply`, `ReadinessCheck.run(case_id)`.
- Produces:
  - `executor.ToolExecutor(state_manager, gateway)`, with `.execute(case_id, execution_id) -> TransitionResult` and `.finish(case_id, execution, result) -> TransitionResult`.
  - `scripted.ScriptedAgents(sm, engine, gateway, patient_id="P-10041")`, with the methods `submit, validate, verification_failed, upload, classify, medical_question, plan, propose, advance, plan_delivery, request, allow, execute, run_step, assess, approval, human, trace`.
  - `scripted.PLAN`, `APPROVED_SOURCE`, `STATUS_MESSAGE`, `EXECUTING_STATES`.
  - `tests.driver.Driver(sm, engine, patient_id="P-10041", gateway=None)`, a subclass of `ScriptedAgents`.

- [ ] **Step 1: Write the failing test** `backend/tests/test_execution.py`:

```python
"""The Tool Executor end to end on Postgres, plus the execution D-tests of §16 (Execution design §8)."""
import pytest

from hospital_agent import repository
from hospital_agent.db import executions
from hospital_agent.execution.gateway import MockGateway
from hospital_agent.execution.verify import REVERIFICATION_FAILED
from hospital_agent.naming import EscalationKind, Event, State
from tests.driver import Driver
from tests.test_policy_d_tests import MEDICAL, at_step, content_approval, insert_approval


def execution(d: Driver, execution_id: str):
    with d.engine.connect() as conn:
        return repository.load_execution(conn, execution_id)


def rows(d: Driver, record_type: str) -> list:
    return [row for row in d.trace() if row.record_type == record_type]


def to_step_2(d: Driver) -> None:
    d.to_classified()
    d.plan()
    d.run_step()
    d.advance()


# --- the path of one call (§1, §18.2) --------------------------------------------------------

def test_a_call_writes_the_started_pair_then_its_outcome(sm, app_engine):
    gw = MockGateway()
    d = Driver(sm, app_engine, gateway=gw)
    d.to_classified()
    d.plan()
    d.run_step()
    tail = [(row.record_type, row.event, row.state_after) for row in d.trace()[-4:]]
    assert tail == [
        ("ExecutionStarted", "TOOL_EXECUTION_STARTED", "RetrievingData"),
        ("ExecutionStarted", "AUDIT_RECORDED", "RetrievingData"),
        ("ExecutionSucceeded", "DATA_RETRIEVED", "RetrievingData"),
        ("Transition", "DATA_RETRIEVED", "Planning"),
    ]
    assert gw.calls == [("CheckAppointment", {"patient_id": d.patient_id}, f"{d.case_id}:1:0:1")]
    assert execution(d, d.last_execution_id).status == "succeeded"
    assert d.case.attempt_count == 1 and d.case.appointment_at is not None


def test_a_decision_is_never_executed_twice(sm, app_engine):
    gw = MockGateway()
    d = Driver(sm, app_engine, gateway=gw)
    d.to_classified()
    d.plan()
    d.run_step()
    replay = d.execute()  # the same execution_id again
    assert (replay.committed, replay.reason, d.state) == (False, REVERIFICATION_FAILED, State.PLANNING)
    assert len(gw.calls) == 1
    [blocked] = rows(d, "Blocked")
    assert (blocked.event, blocked.policy_reasons) == ("TOOL_EXECUTION_STARTED", [REVERIFICATION_FAILED])


def test_a_decision_that_drifted_before_the_call_escalates(sm, app_engine):
    gw = MockGateway()
    d = Driver(sm, app_engine, gateway=gw)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    with app_engine.begin() as conn:  # something changed the binding after the decision was accepted
        conn.execute(executions.update()
                     .where(executions.c.execution_id == d.last_execution_id)
                     .values(plan_hash="0" * 64))
    d.execute()
    assert gw.calls == []
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.EXECUTION_UNKNOWN)


# --- retry (§12.1) ---------------------------------------------------------------------------

def test_d7_three_attempts_then_retry_exhausted(sm, app_engine):
    gw = MockGateway(failures={"CheckDocuments": 5})
    d = Driver(sm, app_engine, gateway=gw)
    to_step_2(d)
    for _ in range(3):
        d.run_step()
    events = [row.event for row in d.trace() if row.record_type == "Transition"]
    assert events.count("POLICY_ALLOWED") == 4 and events[-1] == "RETRY_EXHAUSTED"
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.RETRY_EXHAUSTED)
    assert not d.propose().committed  # no fourth automatic attempt
    assert [call[0] for call in gw.calls].count("CheckDocuments") == 3


def test_d12_every_attempt_is_audited(sm, app_engine):
    d = Driver(sm, app_engine, gateway=MockGateway(failures={"CheckDocuments": 3}))
    to_step_2(d)
    for _ in range(3):
        d.run_step()
    started = [row for row in rows(d, "ExecutionStarted") if row.event == "TOOL_EXECUTION_STARTED"]
    assert [(row.action, row.attempt_number) for row in started[1:]] == [("CheckDocuments", n) for n in (1, 2, 3)]
    assert len(rows(d, "ExecutionFailed")) == 3


def test_d23_a_new_step_starts_with_a_fresh_attempt_budget(sm, app_engine):
    d = Driver(sm, app_engine, gateway=MockGateway(failures={"CheckAppointment": 2}))
    d.to_classified()
    d.plan()
    for _ in range(3):
        d.run_step()
    assert d.case.attempt_count == 3
    d.advance()
    assert (d.case.current_step, d.case.attempt_count) == (2, 0)


@pytest.mark.parametrize("gateway", [
    MockGateway(failures={"CheckDocuments": 1}, non_idempotent=frozenset({"CheckDocuments"})),  # D27
    MockGateway(errors=frozenset({"CheckDocuments"})),                                        # a hard error
])
def test_d27_a_failure_that_cannot_be_retried_goes_to_a_human(sm, app_engine, gateway):
    d = Driver(sm, app_engine, gateway=gateway)
    to_step_2(d)
    d.run_step()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.NON_IDEMPOTENT_FAILURE)
    assert [call[0] for call in gateway.calls].count("CheckDocuments") == 1


def test_human_approval_after_retry_exhausted_opens_a_new_cycle(sm, app_engine):
    d = Driver(sm, app_engine, gateway=MockGateway(failures={"CheckDocuments": 3}))
    to_step_2(d)
    for _ in range(3):
        d.run_step()
    d.human(Event.HUMAN_APPROVED, d.approval("approve"))
    assert (d.state, d.case.retry_cycle, d.case.attempt_count) == (State.PLANNING, 1, 0)
    d.run_step()
    assert d.state is State.PLANNING and execution(d, d.last_execution_id).idempotency_key.endswith(":2:1:1")


# --- delivery (§12.5, D11) --------------------------------------------------------------

def test_d11_an_approved_medical_message_is_delivered_once(sm, app_engine):
    gw = MockGateway()
    d = Driver(sm, app_engine, gateway=gw)
    at_step(d, 4)
    request = d.request(outgoing_message=MEDICAL)
    approval_id = insert_approval(app_engine, content_approval(d, request.execution_id))
    d.allow(execution_id=request.execution_id, outgoing_message=MEDICAL, approval_id=approval_id)
    d.execute()
    assert d.state is State.COMPLETED
    assert list(gw.delivered.values()) == [{"patient_id": d.patient_id, "content_hash": MEDICAL.content_hash}]
    with app_engine.connect() as conn:
        assert repository.load_approval(conn, approval_id).consumed_at is not None
```

- [ ] **Step 2: Run it and see it fail**

Run: `docker compose run --rm backend pytest tests/test_execution.py -v`
Expected: FAIL. `Driver.__init__()` rejects the `gateway` argument (`TypeError: … unexpected keyword argument 'gateway'`), and `Driver` has no `run_step`.

- [ ] **Step 3: Create** `backend/hospital_agent/execution/executor.py`:

```python
"""Tool Executor - the only component that calls an external system (spec §1, §3.1, §12; Execution design §3).

execute() runs one accepted decision end to end:

    start   (one transaction)  re-verify the decision, attempt_count+1, the STARTED/AUDIT pair
    call    (no transaction)   the external system, with only the minimized patient fields
    finish  (one transaction)  the outcome row + the event that follows (Retry Manager on failure)

A start that fails re-verification makes no call and escalates as ExecutionUnknown
(Execution design decision 2) - unless the case has already left RetrievingData /
Delivering (a replayed decision), where the Blocked start row is the whole answer; a start refused for exhausted attempts emits
RETRY_EXHAUSTED; a temporal violation escalates as TemporalViolation.
"""
from __future__ import annotations

from typing import Any

from ..case import CaseRecord, ExecutionRecord
from ..naming import Action, Component, EscalationKind, Event
from ..state_manager import ExecutionOutcome, StateManager, TransitionResult
from .gateway import ACTION_TARGETS, OK, TRANSIENT_FAILURE, ToolGateway, ToolResult
from .retry import after_failure
from .verify import EXECUTING_STATES


class ToolExecutor:
    def __init__(self, state_manager: StateManager, gateway: ToolGateway) -> None:
        self.state_manager, self.gateway = state_manager, gateway

    def execute(self, case_id: str, execution_id: str) -> TransitionResult:
        sm = self.state_manager
        start = sm.start_execution(case_id, execution_id)
        if not start.started:
            case = sm.load(case_id)
            if case.state not in EXECUTING_STATES:
                return TransitionResult(case_id, False, case.state, case.state, reason=start.reason)
            if start.temporal_violation:
                return sm.escalation.signal(case_id, EscalationKind.TEMPORAL_VIOLATION, case.state,
                                            Component.TEMPORAL_MONITOR, reasons=[start.reason])
            if start.reason == "attempts_exhausted":
                return sm.apply(case_id, Event.RETRY_EXHAUSTED, {"execution_id": execution_id}, Component.TOOL_EXECUTOR)
            return sm.escalation.signal(case_id, EscalationKind.EXECUTION_UNKNOWN, case.state,
                                        Component.TOOL_EXECUTOR, reasons=[start.reason])
        case, execution = sm.load(case_id), start.execution
        result = self.gateway.call(execution.action, self._parameters(case, execution), execution.idempotency_key)
        return self.finish(case_id, execution, result)

    def finish(self, case_id: str, execution: ExecutionRecord, result: ToolResult) -> TransitionResult:
        sm = self.state_manager
        execution_id = execution.execution_id
        if result.kind == OK:
            outcome = ExecutionOutcome(execution_id, "succeeded")
            if execution.action == Action.SEND_STATUS_UPDATE.value:
                return sm.apply(case_id, Event.CASE_RESOLVED, {"execution_id": execution_id},
                                Component.RESPONSE_DELIVERY, execution_outcome=outcome)
            return sm.apply(case_id, Event.DATA_RETRIEVED, {"execution_id": execution_id, **result.data},
                            Component.TOOL_EXECUTOR, execution_outcome=outcome)

        reason = f"tool:{result.kind}:{result.data.get('error', '')}".rstrip(":")
        outcome = ExecutionOutcome(execution_id, "failed", reason)
        case = sm.load(case_id)
        verdict = after_failure(case, idempotent=self.gateway.idempotent(execution.action),
                                transient=result.kind == TRANSIENT_FAILURE)
        if verdict.escalation is not None:
            return sm.escalation.signal(case_id, verdict.escalation, case.state, Component.TOOL_EXECUTOR,
                                        reasons=[reason], execution_outcome=outcome)
        payload = {"execution_id": execution_id, "idempotency_key": execution.idempotency_key, "idempotent": True}
        return sm.apply(case_id, verdict.event, payload, Component.TOOL_EXECUTOR, execution_outcome=outcome)

    @staticmethod
    def _parameters(case: CaseRecord, execution: ExecutionRecord) -> dict[str, Any]:
        """Only the patient fields the target may receive (spec §11), plus the message reference."""
        _, fields = ACTION_TARGETS[execution.action]
        parameters: dict[str, Any] = {name: getattr(case, name) for name in fields}
        if execution.action == Action.SEND_STATUS_UPDATE.value:
            parameters["content_hash"] = execution.content_hash
        return parameters
```

- [ ] **Step 4: Create** `backend/hospital_agent/scripted.py`:

```python
"""Scripted stand-ins for the components that do not exist yet (Execution design §6).

ScriptedAgents plays the Session Service, the Classifier, the Planner, the Agent
Orchestrator and the human reviewers with fixed demo answers; everything else it
drives is real - the State Manager, the Policy Service (OPA + Prolog), the Readiness
Check (Z3), the Temporal Monitor and the Tool Executor. Each event comes from the
component that owns it (§13.2).

Used by `python -m obs.golden` and, through tests/driver.py, by the tests. Sub-project 4
replaces the Classifier, Planner and Orchestrator parts with the LLM-backed ones, and
sub-project 5 the reviewers.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine

from . import repository
from .case import ApprovalRecord, CaseRecord
from .execution.executor import ToolExecutor
from .execution.gateway import ACTION_TARGETS, ToolGateway
from .naming import Action, Component, Event, State
from .policy.readiness import ReadinessCheck
from .policy.service import InstructionSource, OutgoingMessage, PolicyRequest, PolicyService, ProposedAction
from .state_manager import StateManager, TransitionResult

PLAN = [
    {"step": 1, "action": "CheckAppointment"},
    {"step": 2, "action": "CheckDocuments"},
    {"step": 3, "action": "LoadInstructions"},
    {"step": 4, "action": "SendStatusUpdate"},
]
APPROVED_SOURCE = InstructionSource("INSTR-PREP-COLONOSCOPY", "3")
STATUS_MESSAGE = OutgoingMessage(evaluated=True, medical_content_flag=False, content_hash="HASH-STATUS-1")
EXECUTING_STATES = frozenset({State.RETRIEVING_DATA, State.DELIVERING})


class ScriptedAgents:
    def __init__(self, sm: StateManager, engine: Engine, gateway: ToolGateway, patient_id: str = "P-10041") -> None:
        self.sm, self.engine, self.patient_id = sm, engine, patient_id
        self.case_id: str | None = None
        self.policy = PolicyService(engine)
        self.executor = ToolExecutor(sm, gateway)
        self.readiness = ReadinessCheck(sm)
        self.last_execution_id: str | None = None

    # --- reading ---------------------------------------------------------------------

    @property
    def case(self) -> CaseRecord:
        return self.sm.load(self.case_id)

    @property
    def state(self) -> State:
        return self.case.state

    def trace(self) -> list[repository.AuditEntry]:
        with self.engine.connect() as conn:
            return repository.load_trace(conn, self.case_id)

    def _emit(self, event: Event, payload: dict, source: Component) -> TransitionResult:
        return self.sm.apply(self.case_id, event, payload, source)

    # --- patient / Session Service ---------------------------------------------------------

    def submit(self) -> TransitionResult:
        result = self.sm.apply(None, Event.REQUEST_SUBMITTED, {"patient_id": self.patient_id}, Component.EXTERNAL)
        self.case_id = result.case_id
        return result

    def validate(self, identity_verified: bool = True) -> TransitionResult:
        payload = {"text": "When is my appointment and which documents do I need?", "identity_verified": identity_verified}
        return self._emit(Event.REQUEST_VALIDATED, payload, Component.SESSION_SERVICE)

    def verification_failed(self) -> TransitionResult:
        return self._emit(Event.PATIENT_VERIFICATION_FAILED, {}, Component.SESSION_SERVICE)

    def upload(self, document_id: str, **document) -> TransitionResult:
        payload = {"document": {"document_id": document_id, "format": "pdf", "patient_id": self.patient_id, **document}}
        return self._emit(Event.DOCUMENT_UPLOADED, payload, Component.SESSION_SERVICE)

    # --- Classifier / Planner / Orchestrator ------------------------------------------------

    def classify(self, safety_level: str = "MediumRisk") -> TransitionResult:
        payload = {"intent": "AppointmentPreparation", "safety_level": safety_level}
        return self._emit(Event.INTENT_CLASSIFIED, payload, Component.CLASSIFIER_SERVICE)

    def medical_question(self) -> TransitionResult:
        payload = {"intent": "MedicalQuestion", "safety_level": "HighRisk"}
        return self._emit(Event.MEDICAL_QUESTION_DETECTED, payload, Component.CLASSIFIER_SERVICE)

    def plan(self) -> TransitionResult:
        return self._emit(Event.PLAN_CREATED, {"plan_complete": True, "ordered_steps": PLAN}, Component.PLANNER_SERVICE)

    def propose(self) -> TransitionResult:
        case = self.case
        proposal = {"action": case.current_action.value, "from_step": case.current_step}
        return self._emit(Event.ACTION_PROPOSED, {"proposed_action": proposal}, Component.PLANNER_SERVICE)

    def advance(self) -> TransitionResult:
        return self._emit(Event.STEP_ADVANCED, {}, Component.AGENT_ORCHESTRATOR)

    def plan_delivery(self) -> TransitionResult:
        return self._emit(Event.DELIVERY_PLANNED, {}, Component.AGENT_ORCHESTRATOR)

    # --- Policy Service / Tool Executor -------------------------------------------------------

    def request(self, **overrides) -> PolicyRequest:
        """A well-formed PolicyRequest for the current plan step; overrides replace fields."""
        case = self.case
        action = case.current_action
        target, fields = ACTION_TARGETS[action.value]
        request = PolicyRequest(
            execution_id=f"EXEC-{uuid.uuid4().hex[:8]}",
            proposed_action=ProposedAction(action.value, case.current_step, target, fields),
            outgoing_message=STATUS_MESSAGE if action is Action.SEND_STATUS_UPDATE else None,
            instruction_source=APPROVED_SOURCE if action is Action.LOAD_INSTRUCTIONS else None,
        )
        return replace(request, **overrides)

    def allow(self, **overrides) -> TransitionResult:
        """The real Policy Service decides the current step (Allow for a well-formed request)."""
        request = self.request(**overrides)
        self.last_execution_id = request.execution_id
        return self.policy.apply(self.sm, self.case_id, request)

    def execute(self) -> TransitionResult:
        """The real Tool Executor runs the decision allow() just accepted."""
        return self.executor.execute(self.case_id, self.last_execution_id)

    def run_step(self, **overrides) -> TransitionResult:
        """One plan step, as the Agent Orchestrator will run it: propose -> decide -> execute."""
        self.propose()
        decided = self.allow(**overrides)
        if decided.committed and decided.state_after in EXECUTING_STATES:
            return self.execute()
        return decided

    # --- Readiness Check ------------------------------------------------------------------

    def assess(self) -> TransitionResult:
        """The real Readiness Check with Z3 (§9.1), on the stored appointment time."""
        return self.readiness.run(self.case_id)

    # --- human reviewers -----------------------------------------------------------------

    def approval(self, decision: str, **fields) -> str:
        """Insert a WorkflowDecision for the current escalation and return its id."""
        case = self.case
        now = datetime.now(UTC)
        approval_id = f"APPR-{uuid.uuid4().hex[:6]}"
        record = ApprovalRecord(
            approval_id=approval_id,
            approval_type="WorkflowDecision",
            case_id=case.case_id,
            patient_id=case.patient_id,
            reviewer_id="coordinator_nurse",
            reviewer_role="clinical_staff",
            decision=decision,
            reason="reviewed by staff",
            shown_context_ref=f"ctx-{approval_id}",
            granted_at=now - timedelta(minutes=1),
            valid_until=now + timedelta(hours=1),
            escalation_kind=case.escalation_kind.value if case.escalation_kind else None,
        )
        with self.engine.begin() as conn:
            repository.insert_approval(conn, replace(record, **fields))
        return approval_id

    def human(self, event: Event, approval_id: str) -> TransitionResult:
        return self._emit(event, {"approval_id": approval_id}, Component.EXTERNAL)
```

- [ ] **Step 5: Replace** `backend/tests/driver.py` with the version below. It keeps only the test-only shortcuts and inherits everything else from `ScriptedAgents`:

```python
"""The scripted demo components (hospital_agent.scripted) plus test-only shortcuts.

The shortcuts emit a component's event directly - without the real Tool Executor or
Readiness Check - so a test can put a case in an exact spot (a given document set, an
exhausted retry, a delivery that failed) and check one guard or rule there.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine

from hospital_agent import repository
from hospital_agent.case import ExecutionRecord
from hospital_agent.execution.gateway import MockGateway, ToolGateway
from hospital_agent.naming import Component, Event
from hospital_agent.scripted import PLAN, ScriptedAgents
from hospital_agent.state_manager import StateManager, TransitionResult

__all__ = ["PLAN", "Driver"]


class Driver(ScriptedAgents):
    def __init__(self, sm: StateManager, engine: Engine, patient_id: str = "P-10041",
                 gateway: ToolGateway | None = None) -> None:
        super().__init__(sm, engine, gateway or MockGateway(), patient_id)

    # --- direct emissions (no real Tool Executor / Readiness Check) ---------------------------

    def retrieved(self, **result) -> TransitionResult:
        return self._emit(Event.DATA_RETRIEVED, result, Component.TOOL_EXECUTOR)

    def transient_failure(self) -> TransitionResult:
        payload = {"idempotency_key": f"idem-{uuid.uuid4().hex[:8]}", "idempotent": True}
        return self._emit(Event.TOOL_TRANSIENT_FAILURE, payload, Component.TOOL_EXECUTOR)

    def retry_exhausted(self) -> TransitionResult:
        return self._emit(Event.RETRY_EXHAUSTED, {}, Component.TOOL_EXECUTOR)

    def deliver(self, status: str = "succeeded") -> TransitionResult:
        case = self.case
        execution_id = f"EXEC-{uuid.uuid4().hex[:8]}"
        with self.engine.begin() as conn:
            repository.insert_execution(conn, ExecutionRecord(
                execution_id=execution_id,
                case_id=case.case_id,
                patient_id=case.patient_id,
                action="SendStatusUpdate",
                step=case.current_step,
                retry_cycle=case.retry_cycle,
                attempt_number=1,
                idempotency_key=f"idem-{execution_id}",
                status=status,
            ))
        return self._emit(Event.CASE_RESOLVED, {"execution_id": execution_id}, Component.RESPONSE_DELIVERY)

    def missing_information(self, z3_result: str = "unsat") -> TransitionResult:
        payload = {"z3_result": z3_result, "patient_deadline": datetime.now(UTC) + timedelta(hours=96)}
        return self._emit(Event.MISSING_INFORMATION_DETECTED, payload, Component.READINESS_CHECK)

    def readiness_passed(self) -> TransitionResult:
        return self._emit(Event.READINESS_PASSED, {}, Component.READINESS_CHECK)

    # --- scenario prefixes -------------------------------------------------------------------

    def to_classified(self) -> None:
        self.submit()
        self.validate()
        self.classify()

    def retrieve_step(self, **result) -> None:
        """propose -> allow -> retrieved (direct), for the current plan step."""
        self.propose()
        self.allow()
        self.retrieved(**result)

    def to_assessing_readiness(self, required: list[str], held: list[str], hours_until: float = 96) -> None:
        self.to_classified()
        self.plan()
        self.retrieve_step(appointment_at=datetime.now(UTC) + timedelta(hours=hours_until))
        self.advance()
        self.retrieve_step(required_documents=required, held_documents=held)
        self.advance()
        self.retrieve_step()
```

- [ ] **Step 6: Run the new test and the whole suite**

Run: `docker compose run --rm backend pytest tests/test_execution.py -v`
Expected: all PASS.

Run: `docker compose run --rm backend pytest -q`
Expected: `338 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/hospital_agent/execution/executor.py backend/hospital_agent/scripted.py backend/tests/driver.py backend/tests/test_execution.py
git commit -m "Add the Tool Executor and the scripted demo components

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: SLA Worker, restart recovery and startup

**Files:**
- Create: `backend/hospital_agent/execution/sla.py`, `backend/hospital_agent/execution/recovery.py`, `backend/hospital_agent/execution/background.py`
- Modify (full new content below): `backend/hospital_agent/api/app.py`
- Test: `backend/tests/test_sla_recovery.py`

**Interfaces:**
- Consumes: `repository.expired_patient_deadlines`, `repository.executions_with_status`, `StateManager.clock`, `ExecutionOutcome`, `EscalationCoordinator.signal`, `wiring.build_state_manager`.
- Produces:
  - `sla.SlaWorker(state_manager)`, with `.tick() -> list[TransitionResult]` and `.run_in_background(interval_seconds) -> threading.Event`.
  - `sla.DEFAULT_INTERVAL_SECONDS = 30.0`.
  - `recovery.recover(state_manager) -> list[TransitionResult]`.
  - `background.sla_interval_seconds()`, which reads `SLA_INTERVAL_SECONDS`.
  - `background.start_background(state_manager, interval_seconds) -> threading.Event`.

- [ ] **Step 1: Write the failing test** `backend/tests/test_sla_recovery.py`:

```python
"""The SLA Worker and restart recovery (spec §3.1 PatientSlaExpired, §12.2; Execution design §5)."""
from datetime import timedelta

from hospital_agent import repository
from hospital_agent.execution.background import start_background
from hospital_agent.execution.gateway import MockGateway
from hospital_agent.execution.recovery import recover
from hospital_agent.execution.sla import SlaWorker
from hospital_agent.naming import Component, EscalationKind, Event, State
from tests.driver import Driver


def execution(d: Driver, execution_id: str):
    with d.engine.connect() as conn:
        return repository.load_execution(conn, execution_id)


def to_started(d: Driver) -> None:
    """A CheckAppointment call has started - and the process dies before its outcome."""
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    assert d.sm.start_execution(d.case_id, d.last_execution_id).started


# --- SLA Worker (§3.1 PatientSlaExpired) -----------------------------------------------------

def to_awaiting_patient(d: Driver) -> None:
    d.to_classified()
    d.plan()
    for _ in range(2):
        d.run_step()
        d.advance()
    d.run_step()
    d.assess()
    assert d.state is State.AWAITING_PATIENT_INPUT


def test_sla_worker_escalates_only_after_the_deadline(sm, app_engine):
    d = Driver(sm, app_engine)
    to_awaiting_patient(d)
    deadline = d.case.patient_deadline
    worker = SlaWorker(sm)
    assert worker.tick() == []
    sm.clock = lambda: deadline + timedelta(seconds=1)
    [result] = worker.tick()
    assert result.committed
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.PATIENT_SLA_EXPIRED)


def test_a_stale_timeout_is_discarded(sm, app_engine):
    d = Driver(sm, app_engine)
    to_awaiting_patient(d)
    registered = d.case.state_version
    d.upload("blood_test")
    stale = sm.apply(d.case_id, Event.TIMEOUT_EXPIRED, {"registered_state_version": registered}, Component.SLA_WORKER)
    assert not stale.committed and d.state is State.CLASSIFYING


# --- restart recovery (§12.2, D28) -----------------------------------------------------------

def test_d28_a_started_execution_without_outcome_escalates_and_is_never_replayed(sm, app_engine):
    gw = MockGateway()
    d = Driver(sm, app_engine, gateway=gw)
    to_started(d)
    [result] = recover(sm)
    assert result.committed and gw.calls == []
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.EXECUTION_UNKNOWN)
    assert execution(d, d.last_execution_id).status == "unknown"
    assert [row.outcome for row in d.trace() if row.record_type == "ExecutionUnknown"] == ["unknown"]
    assert recover(sm) == []
    d.execute()
    assert gw.calls == []


def test_startup_recovers_before_the_sla_worker_starts(sm, app_engine):
    d = Driver(sm, app_engine)
    to_started(d)
    stop = start_background(sm, interval_seconds=3600)
    try:
        assert d.case.escalation_kind is EscalationKind.EXECUTION_UNKNOWN
    finally:
        stop.set()


def test_an_intent_that_never_started_is_left_alone(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    assert recover(sm) == [] and execution(d, d.last_execution_id).status == "intent"
    assert d.state is State.RETRIEVING_DATA
```

- [ ] **Step 2: Run it and see it fail**

Run: `docker compose run --rm backend pytest tests/test_sla_recovery.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'hospital_agent.execution.background'`.

- [ ] **Step 3: Create** `backend/hospital_agent/execution/sla.py`:

```python
"""SLA / Timer Worker - the patient's deadline (spec §3.1 PatientSlaExpired, §13.2; Execution design §5).

tick() finds cases waiting for the patient whose deadline has passed and emits
TIMEOUT_EXPIRED with the version it read. The State Manager checks that version
(PatientSlaExpired + the optimistic lock), so an event for a case that has moved on
is discarded. The deadline itself is registered when the case enters
AwaitingPatientInput (MISSING_INFORMATION_DETECTED or a human's new deadline).
"""
from __future__ import annotations

import threading

from .. import repository
from ..naming import Component, Event
from ..state_manager import StateManager, TransitionResult

DEFAULT_INTERVAL_SECONDS = 30.0


class SlaWorker:
    def __init__(self, state_manager: StateManager) -> None:
        self.state_manager = state_manager

    def tick(self) -> list[TransitionResult]:
        sm = self.state_manager
        with sm.engine.connect() as conn:
            expired = repository.expired_patient_deadlines(conn, sm.clock())
        return [
            sm.apply(case.case_id, Event.TIMEOUT_EXPIRED, {"registered_state_version": case.state_version},
                     Component.SLA_WORKER)
            for case in expired
        ]

    def run_in_background(self, interval_seconds: float = DEFAULT_INTERVAL_SECONDS) -> threading.Event:
        """Tick every `interval_seconds` on a daemon thread; set the returned event to stop it."""
        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(interval_seconds):
                self.tick()

        threading.Thread(target=loop, name="sla-worker", daemon=True).start()
        return stop
```

- [ ] **Step 4: Create** `backend/hospital_agent/execution/recovery.py`:

```python
"""Recovery after a restart (spec §12.2, §14; Execution design §5).

An execution that started but has no outcome may or may not have reached the external
system. It is never replayed: its row becomes 'unknown', an ExecutionUnknown outcome row
is written, and the case escalates to a human - all in one transaction. Rows still in
'intent' made no call and are left for the orchestrator.
"""
from __future__ import annotations

from .. import repository
from ..naming import Component, EscalationKind
from ..state_manager import ExecutionOutcome, StateManager, TransitionResult


def recover(state_manager: StateManager) -> list[TransitionResult]:
    with state_manager.engine.connect() as conn:
        running = repository.executions_with_status(conn, "started")
    results = []
    for execution in running:
        case = state_manager.load(execution.case_id)
        results.append(state_manager.escalation.signal(
            execution.case_id, EscalationKind.EXECUTION_UNKNOWN, case.state, Component.TOOL_EXECUTOR,
            reasons=["execution_unknown:restart"],
            execution_outcome=ExecutionOutcome(execution.execution_id, "unknown", "restart"),
        ))
    return results
```

- [ ] **Step 5: Create** `backend/hospital_agent/execution/background.py`:

```python
"""What runs beside the API (Execution design §5): restart recovery once, then the SLA Worker.

Recovery must finish before anything else touches the executions table, so it runs
synchronously at startup; the SLA Worker then ticks on its own daemon thread.
"""
from __future__ import annotations

import os
import threading

from ..state_manager import StateManager
from .recovery import recover
from .sla import DEFAULT_INTERVAL_SECONDS, SlaWorker


def sla_interval_seconds() -> float:
    return float(os.environ.get("SLA_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))


def start_background(state_manager: StateManager, interval_seconds: float) -> threading.Event:
    """Recover interrupted executions, start the SLA Worker, and return its stop event."""
    recover(state_manager)
    return SlaWorker(state_manager).run_in_background(interval_seconds)
```

- [ ] **Step 6: Replace** `backend/hospital_agent/api/app.py` with:

```python
"""FastAPI app. In the Core it only reads: /health and the Case Monitor endpoints (design §9).

Every request reads the cases/audit_log rows from Postgres - there is no in-memory
State, so the answers are the same before and after a restart. When the app owns its
engine (a real server, not a test), startup first recovers interrupted executions and
then starts the SLA Worker (Execution design §5).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .. import repository
from ..db import make_engine
from ..execution.background import sla_interval_seconds, start_background
from ..naming import State
from ..wiring import build_state_manager
from .schemas import AuditRecord, CaseDetail, CaseSummary


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def create_app(engine: Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = engine is None
        app.state.engine = make_engine() if owned else engine
        stop_sla = start_background(build_state_manager(app.state.engine), sla_interval_seconds()) if owned else None
        yield
        if owned:
            stop_sla.set()
            app.state.engine.dispose()

    app = FastAPI(title="Hospital Patient Agent", lifespan=lifespan)

    @app.get("/health")
    def health(db: Engine = Depends(get_engine)) -> JSONResponse:
        try:
            with db.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok", "database": "ok"})

    @app.get("/cases", response_model=list[CaseSummary])
    def list_cases(state: State | None = None, db: Engine = Depends(get_engine)) -> list[CaseSummary]:
        with db.connect() as conn:
            return [CaseSummary.model_validate(case) for case in repository.list_cases(conn, state)]

    @app.get("/cases/{case_id}", response_model=CaseDetail)
    def get_case(case_id: str, db: Engine = Depends(get_engine)) -> CaseDetail:
        with db.connect() as conn:
            case = repository.load_case(conn, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        return CaseDetail.model_validate(case)

    @app.get("/cases/{case_id}/audit", response_model=list[AuditRecord])
    def get_audit(case_id: str, db: Engine = Depends(get_engine)) -> list[AuditRecord]:
        with db.connect() as conn:
            if repository.load_case(conn, case_id) is None:
                raise HTTPException(status_code=404, detail="case not found")
            return [AuditRecord.model_validate(entry) for entry in repository.load_trace(conn, case_id)]

    return app


app = create_app()
```

- [ ] **Step 7: Run the new test and the whole suite**

Run: `docker compose run --rm backend pytest tests/test_sla_recovery.py -v`
Expected: all PASS.

Run: `docker compose run --rm backend pytest -q`
Expected: `343 passed`.

- [ ] **Step 8: Check that the server still starts** (recovery plus the SLA thread, on the main database)

Run: `docker compose up --build -d`, then `curl -s http://127.0.0.1:8000/health`
Expected: `{"status":"ok","database":"ok"}`. Then run `docker compose down` (keep the volume).

- [ ] **Step 9: Commit**

```bash
git add backend/hospital_agent/execution/sla.py backend/hospital_agent/execution/recovery.py backend/hospital_agent/execution/background.py backend/hospital_agent/api/app.py backend/tests/test_sla_recovery.py
git commit -m "Add the SLA Worker, restart recovery and their startup

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: `python -m obs.golden` and the complete golden traces

**Files:**
- Create: `backend/obs/__init__.py` (empty), `backend/obs/golden.py`
- Modify (full new content below): `backend/tests/test_scenarios.py`

**Interfaces:**
- Consumes: `ScriptedAgents`, `MockGateway`, `wiring.build_state_manager`, `policy.temporal.trace_rows`, `state_manager.OUTCOME_RECORD_TYPES`, `tests.spec_tables.golden_traces`.
- Produces:
  - `obs.golden.run_scenario(number, sm, engine) -> ScenarioRun`, where `ScenarioRun` has `number, title, final_state, entries`.
  - `obs.golden.trace_lines(entries) -> list[tuple[str, str]]`.
  - `obs.golden.render(run) -> str`.
  - `obs.golden.main()`.

- [ ] **Step 1: Replace** `backend/tests/test_scenarios.py` with the complete §15 comparison, the failing test:

```python
"""The three scenarios of §0 against the golden traces of §15 - complete, from the running system.

Everything is real except the components sub-projects 4-5 build (hospital_agent.scripted):
State Manager, Policy Service (OPA + Prolog), Readiness Check (Z3), Temporal Monitor and
Tool Executor with the mock external systems. Every (state after, event) line of §15 must
match, including TOOL_EXECUTION_STARTED / AUDIT_RECORDED, and so must the audit row
totals: one row per trace line plus one outcome row per execution.
"""
import subprocess
import sys

import pytest

from hospital_agent.naming import State
from hospital_agent.state_manager import OUTCOME_RECORD_TYPES
from obs.golden import render, run_scenario, trace_lines
from tests.spec_tables import golden_traces


@pytest.mark.parametrize("number, audit_rows", [(1, 35), (2, 4), (3, 54)])
def test_scenario_matches_the_golden_trace(sm, app_engine, number, audit_rows):
    run = run_scenario(number, sm, app_engine)
    assert trace_lines(run.entries) == golden_traces()[number]
    assert len(run.entries) == audit_rows
    assert run.final_state is State.COMPLETED


def test_scenario_3_makes_three_attempts_then_one_after_approval(sm, app_engine):
    run = run_scenario(3, sm, app_engine)
    outcomes = [row.record_type for row in run.entries if row.record_type in OUTCOME_RECORD_TYPES.values()]
    assert outcomes == ["ExecutionSucceeded", "ExecutionFailed", "ExecutionFailed", "ExecutionFailed",
                        "ExecutionSucceeded", "ExecutionSucceeded", "ExecutionSucceeded"]


def test_render_uses_the_spec_15_format(sm, app_engine):
    lines = render(run_scenario(2, sm, app_engine)).splitlines()
    assert lines[0] == "SCENARIO 2  medical escalation"
    assert lines[1].startswith("[Received            ] REQUEST_SUBMITTED")
    assert lines[-1] == "final: Completed   audit rows: 4"


def test_obs_golden_command_prints_the_three_traces(migrated):
    out = subprocess.run([sys.executable, "-m", "obs.golden"], capture_output=True, text=True, check=True).stdout
    finals = [line for line in out.splitlines() if line.startswith("final:")]
    assert finals == ["final: Completed   audit rows: 35", "final: Completed   audit rows: 4",
                      "final: Completed   audit rows: 54"]
```

- [ ] **Step 2: Run it and see it fail**

Run: `docker compose run --rm backend pytest tests/test_scenarios.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'obs'`.

- [ ] **Step 3: Create** an empty `backend/obs/__init__.py`, then `backend/obs/golden.py`:

```python
"""python -m obs.golden - the §15 golden traces, produced by the running system.

Spec §15: "ה־traces להלן נגזרו ידנית ... לפני הגשה יש להחליפם בפלט המערכת: python3 -m obs.golden".
Each scenario runs end to end on the real State Manager, Policy Service (OPA + Prolog),
Readiness Check (Z3), Temporal Monitor and Tool Executor against the mock external
systems; only the components sub-projects 4-5 will build are scripted
(hospital_agent.scripted). Only the mock's script changes between scenarios (§0).

It runs on the test database (Execution design decision 6): it migrates it and clears
its tables first, and never touches the main database.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from hospital_agent.execution.gateway import MockGateway
from hospital_agent.naming import Event, State
from hospital_agent.policy.temporal import trace_rows
from hospital_agent.repository import AuditEntry
from hospital_agent.scripted import ScriptedAgents
from hospital_agent.state_manager import StateManager
from hospital_agent.wiring import build_state_manager

BACKEND_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ScenarioRun:
    number: int
    title: str
    final_state: State
    entries: list[AuditEntry]  # every audit row of the case, in order


def _scenario_1(a: ScriptedAgents) -> None:
    a.submit()
    a.validate()
    a.classify()
    a.plan()
    a.run_step()          # CheckAppointment
    a.advance()
    a.run_step()          # CheckDocuments: blood_test is missing
    a.advance()
    a.run_step()          # LoadInstructions -> AssessingReadiness
    a.assess()            # Z3 unsat: safe to ask the patient
    a.upload("blood_test")
    a.classify()          # re-classified, readiness in progress
    a.assess()            # everything held -> Ready
    a.plan_delivery()
    a.run_step()          # SendStatusUpdate -> Completed


def _scenario_2(a: ScriptedAgents) -> None:
    a.submit()
    a.validate()
    a.medical_question()
    a.human(Event.HUMAN_RESOLVED_CASE, a.approval("resolve"))


def _scenario_3(a: ScriptedAgents) -> None:
    a.submit()
    a.validate()
    a.classify()
    a.plan()
    a.run_step()          # CheckAppointment
    a.advance()
    for _ in range(3):    # the document system times out three times; no fourth attempt
        a.run_step()
    a.human(Event.HUMAN_APPROVED, a.approval("approve"))  # fixed - a new, bounded retry cycle
    a.run_step()          # CheckDocuments succeeds
    a.advance()
    a.run_step()          # LoadInstructions
    a.assess()
    a.upload("blood_test")
    a.classify()
    a.assess()
    a.plan_delivery()
    a.run_step()          # SendStatusUpdate


# number -> (title, the mock's script, the scenario)
SCENARIOS: dict[int, tuple[str, Callable[[], MockGateway], Callable[[ScriptedAgents], None]]] = {
    1: ("normal operational flow", MockGateway, _scenario_1),
    2: ("medical escalation", MockGateway, _scenario_2),
    3: ("technical failure  (3 automatic attempts)", lambda: MockGateway(failures={"CheckDocuments": 3}), _scenario_3),
}


def run_scenario(number: int, sm: StateManager, engine: Engine) -> ScenarioRun:
    title, gateway, play = SCENARIOS[number]
    agents = ScriptedAgents(sm, engine, gateway())
    play(agents)
    return ScenarioRun(number, title, agents.state, agents.trace())


def trace_lines(entries: list[AuditEntry]) -> list[tuple[str, str]]:
    """(state after, event) of each trace row - what §15 lists; outcome and Blocked rows are not lines."""
    return [(row.state_after, row.event) for row in trace_rows(entries)]


def _notes(row: AuditEntry) -> str:
    """Display only - the tests compare states, events and counts."""
    if row.event in (Event.TOOL_TRANSIENT_FAILURE, Event.RETRY_EXHAUSTED):
        return f"attempt={row.attempt_number}"
    if row.event == Event.ACTION_PROPOSED:
        return f"action={row.action}"
    if row.event in (Event.HUMAN_APPROVED, Event.HUMAN_REJECTED, Event.HUMAN_RESOLVED_CASE):
        return f"approval={row.approval_id}"
    if row.event == Event.TOOL_EXECUTION_STARTED:
        return f"execution={row.execution_id} attempt={row.attempt_number}"
    return ""


def render(run: ScenarioRun) -> str:
    lines = [f"SCENARIO {run.number}  {run.title}"]
    for row in trace_rows(run.entries):
        lines.append(f"[{row.state_after:<20}] {row.event:<28} {_notes(row)}".rstrip())
    lines.append(f"final: {run.final_state.value}   audit rows: {len(run.entries)}")
    return "\n".join(lines)


def _prepare_test_database() -> Engine:
    owner_url = os.environ["TEST_MIGRATION_DATABASE_URL"]
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["url"] = owner_url
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    owner = create_engine(owner_url)
    with owner.begin() as conn:
        conn.execute(text("TRUNCATE audit_log, approvals, executions, cases RESTART IDENTITY"))
    owner.dispose()
    return create_engine(os.environ["TEST_DATABASE_URL"])


def main() -> int:
    engine = _prepare_test_database()
    try:
        sm = build_state_manager(engine)
        print("\n\n".join(render(run_scenario(number, sm, engine)) for number in SCENARIOS))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the test**

Run: `docker compose run --rm backend pytest tests/test_scenarios.py -v`
Expected: 6 PASS. Every §15 line matches, including `TOOL_EXECUTION_STARTED` / `AUDIT_RECORDED`, with 35 / 4 / 54 audit rows.

- [ ] **Step 5: Run the command and the whole suite**

Run: `docker compose run --rm backend python -m obs.golden`
Expected: the three traces in §15 format, ending in `final: Completed   audit rows: 35`, `… 4` and `… 54`.

Run: `docker compose run --rm backend pytest -q`
Expected: `346 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/obs/__init__.py backend/obs/golden.py backend/tests/test_scenarios.py
git commit -m "Add obs.golden: the section 15 golden traces from the running system

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Decisions and hand-off in the docs

**Files:**
- Modify: `docs/spec_corrections.md` (append 6 rows), `CLAUDE.md` (full new content below)

- [ ] **Step 1: Append** these rows to the end of the "Decisions the spec leaves open" table in `docs/spec_corrections.md`, directly after row 18:

```markdown
| 19 | §18.2's `executions` has no column for what a Policy decision was bound to, and `cases` has no appointment time. | Migration `0002` adds `executions.state_version`, `plan_hash`, `approval_id`, `content_hash`, `medical_content_flag` and `cases.appointment_at`. `POLICY_ALLOWED` writes the `executions` row (`status = intent`) in its own transaction; `ExecutorReverified` compares the row with the case before the call. | `alembic/versions/0002_execution_binding.py`, `state_manager.py` `_execution_intent`, `execution/verify.py` |
| 20 | What happens when `ExecutorReverified` fails right before the call? | No external call (§14), a Blocked `TOOL_EXECUTION_STARTED` row (`executor_reverification_failed`), the row goes to `failed`, and the case escalates as `ExecutionUnknown`, the closest kind in the RetrievingData / Delivering allowlist. A replayed decision on a case that has already left those states only gets the Blocked row. | `execution/executor.py` |
| 21 | Which actions are idempotent (§12.1 `IsIdempotent`)? | All four automatic actions. The mock patient channel ignores a repeated `idempotency_key`. D27 scripts a non-idempotent action on the mock. | `execution/gateway.py` `IDEMPOTENT_ACTIONS` |
| 22 | Which `record_type` do the execution audit rows have, and are they part of the temporal trace? | `TOOL_EXECUTION_STARTED` and `AUDIT_RECORDED` are `ExecutionStarted` rows and are in the trace. The outcome rows (`ExecutionSucceeded` / `ExecutionFailed` / `ExecutionUnknown`), written in the same transaction as the event that follows, are not, like Blocked rows (decision 12 of the Policy design). They are what makes §15's totals 35 / 4 / 54. | `policy/temporal.py` `EXCLUDED_RECORD_TYPES`, `state_manager.py` `_record_outcome` |
| 23 | What do the mock external systems return? | A colonoscopy appointment 96 hours ahead; required documents `referral` and `blood_test`, with `referral` already held; the instructions `INSTR-PREP-COLONOSCOPY` v3; a patient channel that confirms delivery. Only the mock's script (failures, errors) changes between scenarios. | `execution/gateway.py` `MockGateway` |
| 24 | Which database does `python -m obs.golden` use? | The test database (`TEST_DATABASE_URL`), migrated and emptied first; it never touches the main database. | `obs/golden.py` |
```

- [ ] **Step 2: Replace** `CLAUDE.md` with the version below. It adds the `obs.golden` command, updates "Working in the backend", and replaces "Hand-off to sub-project 3" with "Hand-off to sub-project 4":

````markdown
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Sub-projects 1 (Core) and 2 (Policy) are implemented in `backend/` (design and plan under `docs/superpowers/`). The authoritative input is the **binding demo spec** `Hospital_Agent_Clean.docx` (Hebrew, final-project scope). A Markdown copy lives in `docs/spec/`, one file per spec section: **spec §N → `docs/spec/NN-*.md`** (index: `docs/spec/README.md`). The docx is the source of truth. `docs/spec/` is generated, so don't hand-edit it. After the docx changes, regenerate it:

```bash
python scripts/spec_to_md.py
```

(Requires `python-docx`.)

- **Scope is exactly the three scenarios in §0**: normal flow with a missing document, medical escalation, and technical failure with bounded retry. The spec calls the "full characterization" (אפיון מלא) a vision document, so do not build beyond the demo.
- The spec keeps pointing to a **companion document (המסמך הנלווה)** that uses the same section numbers, and its implementation conditions are binding. It is **not in the repo**. When a detail is deferred to it, ask the user instead of inventing it.
- Spec inconsistencies found while implementing, and decisions the spec leaves open, are in `docs/spec_corrections.md`.

## Commands

Run from the repo root. The backend runs in Docker (Python 3.13). The repo is mounted into the container, so code changes need no rebuild; dependency changes do.

```bash
docker compose up --build
```

Postgres on `localhost:54322` (database `hospital`, owner `hospital_owner`; the app connects as `hospital_app`), API on `localhost:8000`. Migrations run on start.

```bash
docker compose run --rm backend pytest
```

All tests, against the separate `hospital_test` database. One test:

```bash
docker compose run --rm backend pytest tests/test_fsm.py::test_table_matches_spec_3_row_by_row -v
```

After changing `backend/pyproject.toml`: run `uv lock` in `backend/`, then `docker compose build backend`. `docker compose down -v` wipes the database volume and re-runs `db/init/`.

The §9.2 consistency proof prints `7 abstract properties passed (9 UNSAT queries)`:

```bash
docker compose run --rm backend python -m hospital_agent.policy.consistency
```

After changing `policy/flows.dl`, regenerate the OPA data file (a test fails if the committed file drifts from the Datalog export):

```bash
docker compose run --rm backend python -m hospital_agent.policy.build_minimized
```

The §15 golden traces, from the running system (on the test database; prints `final: Completed   audit rows: 35`, `4`, `54`):

```bash
docker compose run --rm backend python -m obs.golden
```

## Working in the backend (`backend/hospital_agent/`)

- `fsm.py` is the §3 table as data. Each row keeps its Guard cell verbatim in `spec_guard`, and `tests/test_fsm.py` compares all 41 rows with `docs/spec/03-transitions-guards.md`. Change the spec first, then the row.
- A guard (`guards.py`) returns `None` when it holds, or a reason code. A fact that another component determines is trusted only from its owning component: for a system-owned event, because `StateManager` has already checked that the event came from its owner (`naming.EVENT_OWNER`); for an external event (e.g. `DOCUMENT_UPLOADED`, which has no owner), the guard itself checks `GuardContext.source` (e.g. `DocumentValid` requires `Component.SESSION_SERVICE`).
- Only `StateManager.apply()` writes State. `HUMAN_REVIEW_REQUIRED` enters only through `EscalationCoordinator.signal()` - routing every internal escalation through it, instead of letting a component emit `HUMAN_REVIEW_REQUIRED` directly, is a convention inside the trusted computing base (§6.5): the code enforces it, but §6.4's safety proofs assume every component that could call `signal()` keeps to it.
- Guards evaluated outside the State Manager are ports (`GuardPorts`); `wiring` plugs in the real `ExecutorReverified` (`execution/verify.py`). Test doubles - including the permissive monitors some State Manager unit tests use - live only in `tests/fakes.py`; application code has no permissive defaults.
- Application code builds a State Manager only through `wiring.build_state_manager()`: the real Temporal Monitor, and the policy files' hash in `rule_version`. Policy decisions go through `PolicyService.apply()`, readiness through `ReadinessCheck.run()`.
- `policy/policy.rego` is spec §8 verbatim; `policy/rules.pl` is spec §10 without its CASE-482 example facts (those live in `tests/fixtures/case_482.pl`); `policy/flows.dl` is spec §11. Tests compare all three with `docs/spec/`. `tests/opa_reference.py` must agree with the real OPA on every input in `tests/policy_inputs.py` - change them together.
- The Temporal Monitor (`policy/temporal.py`) reads guard results and evidence from the audit rows' `guards` JSON. A new rule needs its evidence recorded there by whoever emits the event; only Policy decision events may carry `evidence`.
- `db.py` mirrors the Alembic migrations, and `tests/test_schema.py` fails if they drift. A schema change is a new migration, never an edit to `0001`. Every new migration must `GRANT` the new tables to `hospital_app` (§18.2) - `SELECT, INSERT, UPDATE` for an ordinary table, but `SELECT, INSERT` only for an audit-style append-only table (as `0001` does for `audit_log`), so the DB itself, not just the app, enforces that Audit can't be changed or deleted.
- `hospital_agent/scripted.py` plays the components that don't exist yet (Classifier, Planner, Orchestrator, reviewers) around the real Policy Service, Readiness Check and Tool Executor; `tests/driver.py` adds test-only shortcuts that emit one event directly. The Tool Executor is the only code that calls an external system (`execution/gateway.py`); `POLICY_ALLOWED` writes the `executions` intent row, and `StateManager.start_execution()` writes the STARTED / AUDIT_RECORDED pair.

## Hand-off to sub-project 4 (LLM)

- `hospital_agent/scripted.py` plays the Session Service, Classifier, Planner, Agent Orchestrator and reviewers with fixed demo answers; everything it drives is real. Sub-project 4 replaces the Classifier, Planner and Orchestrator parts: the LLM-backed ones must emit the same events from the same components (`naming.EVENT_OWNER`), and `python -m obs.golden` must still print 35 / 4 / 54 audit rows.
- A Policy request's `outgoing_message` is where the Response Evaluator's verdict enters (`evaluated`, `medical_content_flag`, `content_hash`). Only the Evaluator sets `evaluated`.
- The Tool Executor needs nothing from the LLM: `ToolExecutor.execute(case_id, execution_id)` runs a decision the Policy Service accepted, and the Retry Manager decides what follows a failure. A retry goes back to `Planning` and needs a fresh `ACTION_PROPOSED`.

## What the system is

A hospital patient-service agent that handles *operational* requests (appointment status, required documents, approved preparation instructions). It must **never give medical answers automatically**. The design principle is "the LLM proposes, deterministic layers decide". Each case is an event-driven state machine. Every step the LLM proposes must pass layered formal checks before any external call. Any unknown condition **fails closed**: the system stops and escalates to a human, never continues (§14).

## Architecture (big picture)

**State machine (§2, §3).** There are 12 states and 26 events. The transition table in §3 plus the guards in §3.1 are the *only* legal transitions. **Only the State Manager writes state.** If no table row's guards hold for a (state, event) pair, the event is `Blocked: guard_failed`: state stays unchanged and a `Blocked` audit row is written. Terminal states (`Completed`, `Failed`) absorb all later events.

**Every transition is one Postgres transaction (§18.2).** It reads `cases` at the current `state_version`, INSERTs into `audit_log`, UPDATEs `cases ... WHERE state_version=?`, and, when needed, UPDATEs `executions` and `approvals.consumed_at`. If zero rows update, the event is reprocessed against the fresh state. The Temporal Monitor must approve the extended trace before commit. If the Monitor is unavailable, nothing commits. There are four tables: `cases`, `executions` (the outbox), `audit_log` (the app role has INSERT only), and `approvals`.

**The path of a single tool call:**
1. The Planner emits `ACTION_PROPOSED`.
2. The State Manager checks the `InPlan` and `PlanIntact` guards.
3. The Policy Service runs OPA and Prolog. **The two must agree.** A disagreement or an unavailable engine means Deny.
4. The result is `POLICY_ALLOWED`, and state moves to `RetrievingData` or `Delivering`.
5. The Tool Executor re-verifies everything (`ExecutorReverified`: `decision_token`, `state_version`, `plan_hash`, step, `idempotency_key`).
6. An `executions` row is committed. `attempt_count` is incremented *before* the call.
7. `TOOL_EXECUTION_STARTED` and `AUDIT_RECORDED` are written together as one atomic pair. Neither event changes state.
8. The external call runs outside the transaction.
9. A result event follows.

After a restart, an execution with no outcome escalates as `ExecutionUnknown` and is **never replayed automatically**.

**Policy Service = five engines (§7–§11).**
- **OPA 1.9.0 / Rego v1** (`package hospital_agent.policy`). Precedence is Deny > RequireHumanReview > Allow > default Deny. The bundle data is `data.hospital_agent.minimized_fields` and `data.hospital_agent.approved_instruction_sources`. Implementation: `policy/policy.rego` (the spec's Rego verbatim) is evaluated at run time by the `opa` 1.9.0 binary in the backend container (`policy/opa_runner.py`); `tests/opa_reference.py` is a test-only Python evaluator that must agree with it on every input in `tests/policy_inputs.py`.
- **Prolog.** Handles role and action authorization and gives `explain/4` reasons. Dynamic facts are cleared and reloaded for each request, in isolation. Implementation: a Python engine, inside the backend service, that parses and runs the spec's `.pl` files as written (the pattern continues from the earlier course project `AI_Hospital`). The spec's reference engine is SWI-Prolog 9.2.9 - `docs/spec/10-prolog.md`'s queries are reproduced as tests to compare results, but SWI-Prolog is not installed or invoked.
- **Datalog** (a subset with tabling). Models sensitive-field flows. `build_minimized.py` exports `flows.dl` to `minimized_fields.json`, which goes into the OPA bundle. Implementation: a bottom-up Python engine (`policy/datalog.py`) running the spec's `.dl` file as written; the spec's reference engine is likewise SWI-Prolog 9.2.9.
- **Z3 4.15.4 (Python).**
  - §9.1: a readiness SLA check. **Only `unsat` means safe** to ask the patient for a document. `sat`, `unknown`, timeout, an exception or invalid input all escalate (`Z3Counterexample`). A failed audit write blocks commit.
  - §9.2: cross-layer consistency proofs (7 properties, 9 UNSAT queries).
- **Temporal Monitor.** Evaluates the past-time LTL rules T1–T12 (§6) over each case's audit trace before every commit. A violation blocks the transition and escalates as `TemporalViolation`.

**LLM (§18.5).** One model, four separate calls: Intent, Safety, Planner, and Response Evaluator. Each has its own prompt, temperature 0, and a JSON Schema. The Evaluator never sees the Planner's prompt. Output that fails its schema is rejected, and 3 consecutive schema failures escalate (`ClassificationFailed` / `PlanningFailed`). Model and prompt versions go into `rule_version`. **The LLM never supplies authoritative facts.** It cannot set `approved`/`valid` flags or `outgoing_message.evaluated` (only the Response Evaluator sets that), and it cannot assert approvals.

**Escalation.** Internal components only *signal*. Only the **Escalation Coordinator** emits the canonical `HUMAN_REVIEW_REQUIRED` event, carrying `escalation_kind` and `escalated_from_state`. `HUMAN_APPROVED` can resume a case only for these kinds, each with a required field:

| `escalation_kind` | Required field |
|---|---|
| `PatientVerificationFailed` | `verified_identity_ref` |
| `RetryExhausted` | none (opens a new retry cycle) |
| `PolicyReview` | a one-shot override bound to `plan_hash` + `current_step` |
| `Z3Counterexample` | new `patient_deadline` |
| `PatientSlaExpired` | new `patient_deadline` |

Every other escalation (MedicalQuestion, SafetyEscalation, TemporalViolation, PolicyDenied, …) can only be **resolved or rejected**.

**Two approval kinds (§12.4–12.5), both single-use.** `consumed_at` is written in the same transaction as the transition that uses the approval.
- `WorkflowDecision` approves a process decision.
- `ContentApproval` approves one exact message. It is bound to `execution_id` + `action` + `content_hash`, and only `clinical_staff` can grant it.

A WorkflowDecision is **never** a medical content approval (`approval_is_workflow_only`).

**Retry (§12.1).** `max_attempts=3` per step per `retry_cycle`. A retry goes back to `Planning` and needs a fresh proposal and a fresh policy approval. `STEP_ADVANCED` resets both counters. `HUMAN_APPROVED` on `RetryExhausted` sets `retry_cycle+1` and `attempt_count=0`. A non-idempotent transient failure escalates immediately (`NonIdempotentFailure`).

**Event ownership (§13.2).** Only five events can come from outside: `REQUEST_SUBMITTED`, `DOCUMENT_UPLOADED`, and the three human decisions. The other 21 are system-owned, and injecting one from outside gives `Blocked: system_owned_event`.

**Three separate logs (§12.3).**
- **Audit:** append-only. It is the trace the temporal rules are checked against. It stores IDs, decisions and `content_hash` only.
- **Data Log:** medical content. It can be deleted, leaving a tombstone.
- **Application Log:** must never contain `patient_id`, request content or secrets.

## Behaviors that are easy to get wrong

- **`plan_hash`** = `sha256(json.dumps(ordered_steps, separators=(",", ":"), sort_keys=True))`. This matches OPA `json.marshal` and reproduces the spec's example hash `70471a82…`. Any other serialization breaks `plan_modified`.
- **Plan shape:** the fixed plan is `CheckAppointment → CheckDocuments → LoadInstructions → SendStatusUpdate`. After `LoadInstructions`, `DATA_RETRIEVED` goes to `AssessingReadiness` with no `STEP_ADVANCED`. Delivery starts only from `Ready` via `DELIVERY_PLANNED` (guard `DeliveryStepPending`). `Completed` requires `DeliveryConfirmed`.
- **Classifying finishes only when both Intent and Safety have returned (D21).** Precedence:
  1. MedicalQuestion → `MEDICAL_QUESTION_DETECTED`.
  2. Otherwise, HighRisk/CriticalRisk → `SafetyEscalation`.
  3. Otherwise → `INTENT_CLASSIFIED`.
  A valid uploaded document is re-classified, going back to `Classifying` (T10). Retrieved content and uploads are re-checked for safety.
- **Readiness order:** full readiness first, then a Z3-safe document request, then escalation. Readiness is computed from tool results (`required_documents` vs `held_documents`) and is never declared by the caller. The request for a missing document is a UI template (`missing_document_ids`, `missing_document_request_template_id`), not an external call.
- **Actions** (§5): the Planner may only propose the four automatic actions. `CloseMedicalCase` and `AnswerClinicalQuestion` are human-only. The Policy Service maps once between PascalCase names (OPA/State) and snake_case names (Prolog/Datalog). Note that `AnswerClinicalQuestion` maps to `answer_clinical_q`.
- **What is proven:** only safety, not liveness (§6.4). The proofs assume a correct TCB (§6.5). Misclassification is measured empirically (D33) and is not proven.

## Binding conventions (§17)

| Kind | Convention | Example |
|---|---|---|
| States | PascalCase | `AwaitingHumanReview` |
| Events | UPPER_SNAKE_CASE, Object_Verb | `REQUEST_SUBMITTED` |
| Actions | PascalCase, Verb-Object | `CheckDocuments` |
| Guards | PascalCase | `ReadinessComplete` |
| Fields | snake_case | `attempt_count` |

The declared event-name exceptions are `HUMAN_RESOLVED_CASE` and `TOOL_TRANSIENT_FAILURE`. The event list (§2.2) and the action list (§5) are **closed**. Use these exact names in code, tables and traces, and don't add new ones without a spec change. Unknown actions → `action_not_supported`.

## Verification targets

- **Acceptance suite:** tests D1–D35 (§16). Name each test after its D-number. §6.2 and §13 map every temporal rule (T1–T12) and invariant (INV-1…12) to the D-tests that cover it.
- **Golden traces (§15):** the traces in the spec were derived by hand and must be replaced by real system output from `python3 -m obs.golden` before submission. Expected audit row counts: scenario 1 = 35, scenario 2 = 4, scenario 3 = 54.
- **Runnable spec examples to reproduce as tests:**
  - the OPA input/output table (§8);
  - Z3 `hours_until` 96/32 → `unsat`, 20 → `sat` (§9.1);
  - the Z3 consistency script (§9.2);
  - Prolog queries (§10);
  - Datalog queries (§11).
- **Demo stubs:** the IdP is a fixed user list (§18.3). External systems (appointments, documents, instructions, patient channel) are mocks. All three scenarios run on the same code and model, and only patient input and mock responses change.

## Tech stack (decided by the user)

- **Backend:** Python + FastAPI.
- **Frontend:** React, with the patient screen and the staff screen described in §1.
- **Database:** PostgreSQL, using the four-table model in §18.2.
- **LLM:** OpenAI GPT-5.6 Luna for now. The user expects this may change, so keep the provider behind the Model Selector (§1). Record the model and prompt versions in `rule_version`.
- **Policy engines:** OPA (the `opa` 1.9.0 binary, plus a Python reference evaluator) and the Python Prolog/Datalog engines run **inside the backend service**. There is no OPA server and no sidecar, and SWI-Prolog is not installed - it is only the spec's reference engine, used to derive the expected results the tests compare against. Z3 is `z3-solver==4.15.4`, through its Python bindings.
- **Backend layout:** a **single FastAPI app** with one module per spec component (§1). The components are internal boundaries, not separate services.

## Still open (confirm with the user)

- The exact model ID string for the OpenAI API.
````

- [ ] **Step 3: Check nothing else changed**

Run: `docker compose run --rm backend pytest -q`
Expected: `346 passed`.

Run: `git diff --stat`
Expected: only these two files.

- [ ] **Step 4: Commit**

```bash
git add docs/spec_corrections.md CLAUDE.md
git commit -m "Document the Execution decisions and the hand-off to sub-project 4

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Done when

- `docker compose run --rm backend pytest` passes (346 tests).
- `docker compose run --rm backend python -m obs.golden` prints the three traces with `audit rows: 35 / 4 / 54`.
- `CLAUDE.md` and `docs/spec_corrections.md` are updated (design §7).
