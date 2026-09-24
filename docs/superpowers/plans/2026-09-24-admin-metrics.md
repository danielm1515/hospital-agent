# Admin Metrics Screen (Sub-project 14) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An `admin_staff`-only screen, `/staff/metrics`, showing system metrics aggregated read-only from the tables the agent already writes.

**Architecture:** `hospital_agent/metrics.py` holds one pure query function per metric group (A flow, B human load, C tools, D patient SLA, E policy) and `compute()`, which runs them all in one `REPEATABLE READ`, `READ ONLY` transaction with a 5 s statement timeout. `GET /api/admin/metrics?from=&to=` (new `routes_admin.py`, gated by a new `require_admin`) validates the window and serialises the result. The React page reads it through `api.getMetrics()` and draws stat tiles, one-series bar lists and a tools table, with no chart library.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 + psycopg 3, PostgreSQL 16, Alembic, pytest; React 18, TypeScript 5.9, Vite 5, Vitest 2 + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-24-admin-metrics-design.md` (read it first; this plan argues from it). Spec corrections row 82 (`docs/spec_corrections.md`).

## Global Constraints

- **Read-only feature.** Do not modify `fsm.py`, `guards.py`, `state_manager.py`, `human_review.py`, anything under `policy/` or `llm/`, `obs/golden.py`, or any existing route. The only DB write is migration 0005 (three indexes).
- **No new dependencies** — neither `backend/pyproject.toml` nor `frontend/package.json` changes.
- **Branch** `feature/admin-metrics` (already checked out). One commit per task. **Never push.** Every commit message ends with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Backend tests run only in the isolated compose project** `metrics-proto` — never the user's stack, never RDS (`.env` points at RDS). Every backend command in this plan is run as:
  ```bash
  cd "C:/Apps/פרויקט גמר AI/hospital-agent"
  export MSYS_NO_PATHCONV=1
  DC="docker compose -p metrics-proto -f docker-compose.yml -f C:/Users/DANIEL~1.MAM/AppData/Local/Temp/claude/C--Apps------------AI/4ba1785e-5a6d-42d4-88ab-e01babb23dab/scratchpad/compose.proto.yml"
  $DC run --rm backend pytest <args>
  ```
  The override pins the four database URLs and the Postgres passwords to the project's own `db` container and removes host ports. Baseline: `tests/test_api_staff.py` → 27 passed.
- **Frontend commands** run outside Docker from `frontend/`: `npm test` (Vitest once) and `npm run build`. `node_modules` is installed. Baseline: 23 files, 173 tests passed.
- **Window:** `[start, end)` — start inclusive, end exclusive, both timezone-aware, `end - start ≤ 90 days`.
- **Durations** are seconds as `float`. Every `extract(epoch FROM …)` is cast `::double precision` (Postgres returns `numeric` otherwise). A percentile or max over zero rows is `None`, never `0`.
- **Error codes:** `401 not_authenticated`, `403 admin_only`, `422 invalid_body` (a missing query parameter — the app's global validation handler), `422 invalid_range` (unparsable, naive or `from >= to`), `422 range_too_large` (> 90 days), `503 metrics_unavailable` (statement timeout).
- **Privacy (§12.3):** the response carries aggregates only — no `patient_id`, no `case_id`, no request text.
- **Staff label rule** (`frontend/src/pages/staff/labels.ts`): a Hebrew label is shown next to the code it translates, never instead of it; an unknown code falls back to itself.
- **CSS:** add only new `.metrics-*` selectors to `frontend/src/styles/app.css`; never redeclare an existing top-level selector (`app.css.test.ts` fails on a duplicate) and never edit `tokens.css` (it must stay byte-identical to the design file).
- **Dataviz decisions (validated):** one series per bar list → one color for every bar. The bar color is `#1189ae` in both themes — `validate_palette.js`: light vs `#ffffff` all PASS; dark vs `#14212b` all PASS. The dark theme's own `--brand-500` (`#2fabd3`, OKLCH L 0.693) FAILS the dark lightness band (0.48–0.67), and `#0e6f8e` fails contrast (2.87:1). Bars ≤ 24 px thick, 4 px rounded data-end, square baseline, value written at the tip in ink tokens. Stat-tile values in the sans face with proportional figures (not `.num`, which is mono). Meter track = `var(--brand-100)`, the same ramp's light step. A refetch keeps the previous render at reduced opacity.

## File Structure

| File | Responsibility |
|---|---|
| `backend/alembic/versions/0005_metrics_indexes.py` (create) | Three additive indexes for the window filters |
| `backend/hospital_agent/metrics.py` (create) | `Window`, the five group queries, `compute()` |
| `backend/hospital_agent/api/deps.py` (modify) | `require_admin` |
| `backend/hospital_agent/api/schemas.py` (modify) | The response models |
| `backend/hospital_agent/api/routes_admin.py` (create) | `GET /api/admin/metrics` |
| `backend/hospital_agent/api/app.py` (modify) | Include the admin router |
| `backend/tests/metrics_seed.py` (create) | Direct inserts at exact instants |
| `backend/tests/test_metrics_migration.py`, `test_metrics_flow.py`, `test_metrics_human.py`, `test_metrics_tools.py`, `test_metrics_sla_policy.py`, `test_metrics_compute.py`, `test_api_admin.py` (create) | Tests |
| `frontend/src/api/types.ts`, `frontend/src/api/client.ts` (modify) | `Metrics` types, `getMetrics()` |
| `frontend/src/pages/staff/metricsLabels.ts` (create) | Labels, formatting, range helpers (pure) |
| `frontend/src/pages/staff/MetricsParts.tsx` (create) | `Tile`, `Bars`, `Meter` |
| `frontend/src/pages/staff/Metrics.tsx` (create) | The page |
| `frontend/src/pages/staff/StaffRoutes.tsx` (modify) | Admin-only nav item and route |
| `frontend/src/styles/app.css` (modify) | `.metrics-*` rules |
| `docs/api.md`, `CLAUDE.md` (modify) | Documentation |

---

### Task 1: Migration 0005 — the window indexes

**Files:**
- Create: `backend/alembic/versions/0005_metrics_indexes.py`
- Test: `backend/tests/test_metrics_migration.py`

**Interfaces:**
- Produces: indexes `ix_audit_log_recorded_at`, `ix_cases_created_at`, `ix_executions_started_at`; Alembic head `0005`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_metrics_migration.py`:

```python
"""Migration 0005 (sub-project 14, design §6): the metrics' window indexes, and a clean downgrade."""
from alembic import command
from sqlalchemy import text

INDEXES = {"ix_audit_log_recorded_at", "ix_cases_created_at", "ix_executions_started_at"}


def _indexes(owner_engine) -> set[str]:
    with owner_engine.connect() as conn:
        return set(conn.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")).scalars())


def test_0005_adds_the_three_window_indexes(migrated, owner_engine):
    assert INDEXES <= _indexes(owner_engine)


def test_0005_downgrades_cleanly_and_upgrades_again(migrated, owner_engine, alembic_config):
    try:
        command.downgrade(alembic_config, "0004")
        assert not INDEXES & _indexes(owner_engine)
    finally:
        command.upgrade(alembic_config, "head")
    assert INDEXES <= _indexes(owner_engine)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$DC run --rm backend pytest tests/test_metrics_migration.py -v`
Expected: FAIL — `test_0005_adds_the_three_window_indexes` asserts the indexes are missing (and the downgrade test fails on revision `0004` → the set is already disjoint but the final assert fails).

- [ ] **Step 3: Write the migration**

`backend/alembic/versions/0005_metrics_indexes.py`:

```python
"""The admin metrics screen's window indexes (sub-project 14, design §6).

Every metric filters one timestamp to the requested window: audit_log.recorded_at for the
event groups, cases.created_at for the cohort group and executions.started_at for the tools.
None of them was indexed. Additive only; the metrics read, they never write.

Revision ID: 0005
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_audit_log_recorded_at", "audit_log", ["recorded_at"])
    op.create_index("ix_cases_created_at", "cases", ["created_at"])
    op.create_index("ix_executions_started_at", "executions", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_executions_started_at", table_name="executions")
    op.drop_index("ix_cases_created_at", table_name="cases")
    op.drop_index("ix_audit_log_recorded_at", table_name="audit_log")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `$DC run --rm backend pytest tests/test_metrics_migration.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0005_metrics_indexes.py backend/tests/test_metrics_migration.py
git commit -m "Add migration 0005: the metrics window indexes" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `metrics.py` foundation and group A (flow)

**Files:**
- Create: `backend/hospital_agent/metrics.py`
- Create: `backend/tests/metrics_seed.py`
- Test: `backend/tests/test_metrics_flow.py`

**Interfaces:**
- Produces (in `hospital_agent.metrics`): `MAX_WINDOW`, `STATEMENT_TIMEOUT`, `InvalidWindow(code)` with `.code`, `MetricsUnavailable`, `Window(start, end)` with `.params -> {"start", "end"}`, `Durations(count, p50, p95, max)`, `EMPTY_DURATIONS`, private helpers `_durations_sql(expr) -> str`, `_durations(row) -> Durations`, `_counts(conn, sql, params) -> dict[str, int]`, `_EVENTS` (the event-window SQL predicate on an unqualified `recorded_at`), `Flow(opened, by_state, by_outcome, completion)`, `COMPLETION_EVENTS`, `flow(conn, window) -> Flow`.
- Produces (in `tests.metrics_seed`): `T0`, `at(minutes) -> datetime`, `add_case(...)`, `add_row(...)`, `add_execution(...)`, `add_approval(...)`.

- [ ] **Step 1: Write the seed helper**

`backend/tests/metrics_seed.py`:

```python
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
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_metrics_flow.py`:

```python
"""Group A - the flow of the cases opened in the window (sub-project 14, design §4.1)."""
from datetime import timedelta

import pytest
from pytest import approx

from hospital_agent import metrics
from hospital_agent.metrics import EMPTY_DURATIONS, Durations, InvalidWindow, Window
from tests.metrics_seed import T0, add_case, add_row, at

WINDOW = Window(T0, T0 + timedelta(days=1))


def test_a_window_is_aware_ordered_and_at_most_90_days():
    for start, end in ((T0.replace(tzinfo=None), T0 + timedelta(hours=1)),
                       (T0, T0.replace(tzinfo=None) + timedelta(hours=1)),
                       (T0 + timedelta(hours=1), T0),
                       (T0, T0)):
        with pytest.raises(InvalidWindow) as invalid:
            Window(start, end)
        assert invalid.value.code == "invalid_range"
    assert Window(T0, T0 + timedelta(days=90)).params == {"start": T0, "end": T0 + timedelta(days=90)}
    with pytest.raises(InvalidWindow) as too_long:
        Window(T0, T0 + timedelta(days=90, seconds=1))
    assert too_long.value.code == "range_too_large"


def test_opened_cases_are_counted_by_state_inside_the_window_only(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-start", created_at=T0, state="Completed")  # exactly at start: in
        add_case(conn, "C-mid", created_at=at(60), state="AwaitingHumanReview")
        add_case(conn, "C-end", created_at=T0 + timedelta(days=1), state="Completed")  # exactly at end: out
        add_case(conn, "C-before", created_at=at(-1), state="Failed")
    with app_engine.connect() as conn:
        flow = metrics.flow(conn, WINDOW)
    assert flow.opened == 2
    assert flow.by_state == {"Completed": 1, "AwaitingHumanReview": 1}


def test_the_classification_outcome_reads_the_audit_not_intent_alone(app_engine):
    medical = dict(before="Classifying", after="AwaitingHumanReview")
    with app_engine.begin() as conn:
        # A real medical question leaves cases.intent NULL (RECORD_CLASSIFICATION runs only on
        # INTENT_CLASSIFIED) - the audit row is the only trace of it.
        add_case(conn, "C-med", created_at=at(1))
        add_row(conn, "C-med", "MEDICAL_QUESTION_DETECTED", at=at(2), **medical)
        add_case(conn, "C-safety", created_at=at(1))
        add_row(conn, "C-safety", "HUMAN_REVIEW_REQUIRED", at=at(2), **medical)
        add_case(conn, "C-prep", created_at=at(1), intent="AppointmentPreparation")
        add_case(conn, "C-unsup", created_at=at(1), intent="Unsupported")
        add_case(conn, "C-new", created_at=at(1))
        # a Blocked row is not a classification
        add_row(conn, "C-new", "MEDICAL_QUESTION_DETECTED", at=at(2), before="Received", after="Received",
                record_type="Blocked", reasons=["guard_failed"])
        # a later medical question outranks the intent the case was first classified with
        add_case(conn, "C-both", created_at=at(1), intent="AppointmentPreparation")
        add_row(conn, "C-both", "MEDICAL_QUESTION_DETECTED", at=at(3), **medical)
    with app_engine.connect() as conn:
        flow = metrics.flow(conn, WINDOW)
    assert flow.by_outcome == {"MedicalQuestion": 2, "EscalatedAtClassification": 1,
                               "AppointmentPreparation": 1, "Unsupported": 1, "NotClassified": 1}


def test_time_to_completion_is_split_by_how_the_case_completed(app_engine):
    done = dict(before="Delivering", after="Completed")
    with app_engine.begin() as conn:
        for case_id in ("C-auto1", "C-auto2", "C-human"):
            add_case(conn, case_id, created_at=at(0), state="Completed")
        add_row(conn, "C-auto1", "CASE_RESOLVED", at=at(1), **done)
        add_row(conn, "C-auto2", "CASE_RESOLVED", at=at(3), **done)
        add_row(conn, "C-human", "HUMAN_RESOLVED_CASE", at=at(10), before="AwaitingHumanReview", after="Completed")
    with app_engine.connect() as conn:
        flow = metrics.flow(conn, WINDOW)
    # [60, 180]: p50 = 120, p95 = 60 + 0.95 * 120 = 174
    assert flow.completion["CASE_RESOLVED"] == Durations(2, approx(120.0), approx(174.0), approx(180.0))
    assert flow.completion["HUMAN_RESOLVED_CASE"] == Durations(1, approx(600.0), approx(600.0), approx(600.0))


def test_an_empty_window_has_zero_counts_and_no_percentiles(app_engine):
    with app_engine.connect() as conn:
        flow = metrics.flow(conn, WINDOW)
    assert (flow.opened, flow.by_state, flow.by_outcome) == (0, {}, {})
    assert flow.completion == {"CASE_RESOLVED": EMPTY_DURATIONS, "HUMAN_RESOLVED_CASE": EMPTY_DURATIONS}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `$DC run --rm backend pytest tests/test_metrics_flow.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hospital_agent.metrics'`.

- [ ] **Step 4: Write `metrics.py` (foundation + group A)**

`backend/hospital_agent/metrics.py`:

```python
"""The admin metrics screen's numbers (sub-project 14, design
docs/superpowers/specs/2026-09-24-admin-metrics-design.md).

Read-only. Every number aggregates tables the agent already writes - audit_log, executions,
cases and approvals - so a metric can never disagree with the Audit (design §3). compute()
runs every group in one REPEATABLE READ, READ ONLY transaction: all groups see the same
snapshot, and a slow query ends the whole answer (MetricsUnavailable), never a partial one
(design §5).

Two kinds of window (design §4.0): the cohort group (flow) counts the cases *opened* in
[start, end); the event groups count what *happened* in it. Durations are seconds; a
percentile over no rows is None, never 0.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

MAX_WINDOW = timedelta(days=90)
STATEMENT_TIMEOUT = "5s"


class InvalidWindow(ValueError):
    """`code` is the API's 422 detail: invalid_range or range_too_large."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MetricsUnavailable(Exception):
    """A query ran past STATEMENT_TIMEOUT: the whole answer is refused, never a partial one."""


@dataclass(frozen=True)
class Window:
    start: datetime  # inclusive
    end: datetime  # exclusive

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise InvalidWindow("invalid_range")
        if self.end - self.start > MAX_WINDOW:
            raise InvalidWindow("range_too_large")

    @property
    def params(self) -> dict[str, datetime]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True)
class Durations:
    """Seconds. `count` is the number of measured intervals; the rest are None when it is 0."""

    count: int
    p50: float | None
    p95: float | None
    max: float | None


EMPTY_DURATIONS = Durations(0, None, None, None)

# The event groups' window, on an unqualified recorded_at (a query over audit_log alone).
_EVENTS = "recorded_at >= :start AND recorded_at < :end"


def _durations_sql(interval: str) -> str:
    # extract() is numeric and percentile_cont double precision: cast, so every value is a float.
    seconds = f"extract(epoch FROM {interval})::double precision"
    return (f"count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY {seconds}), "
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY {seconds}), max({seconds})")


def _durations(row: Sequence[Any]) -> Durations:
    return Durations(int(row[0]), row[1], row[2], row[3])


def _counts(conn: Connection, sql: str, params: Mapping[str, Any]) -> dict[str, int]:
    return {str(key): int(count) for key, count in conn.execute(text(sql), params)}


# --- A: flow (cohort - the cases opened in the window; design §4.1) ------------------------

COMPLETION_EVENTS = ("CASE_RESOLVED", "HUMAN_RESOLVED_CASE")
_COHORT = "c.created_at >= :start AND c.created_at < :end"

# design §4.1 A3: one outcome per case, first match wins. Not cases.intent alone -
# RECORD_CLASSIFICATION runs only on INTENT_CLASSIFIED, so a medical question or an
# escalation at classification leaves it NULL.
_OUTCOME = f"""
    SELECT CASE
             WHEN EXISTS (SELECT 1 FROM audit_log a WHERE a.case_id = c.case_id
                            AND a.record_type = 'Transition' AND a.event = 'MEDICAL_QUESTION_DETECTED')
               THEN 'MedicalQuestion'
             WHEN EXISTS (SELECT 1 FROM audit_log a WHERE a.case_id = c.case_id
                            AND a.record_type = 'Transition' AND a.event = 'HUMAN_REVIEW_REQUIRED'
                            AND a.state_before = 'Classifying')
               THEN 'EscalatedAtClassification'
             WHEN c.intent IS NOT NULL THEN c.intent
             ELSE 'NotClassified'
           END, count(*)
    FROM cases c WHERE {_COHORT} GROUP BY 1"""


@dataclass(frozen=True)
class Flow:
    opened: int
    by_state: dict[str, int]
    by_outcome: dict[str, int]
    completion: dict[str, Durations]  # CASE_RESOLVED (automatic) / HUMAN_RESOLVED_CASE (by staff)


def flow(conn: Connection, window: Window) -> Flow:
    by_state = _counts(conn, f"SELECT c.state, count(*) FROM cases c WHERE {_COHORT} GROUP BY 1", window.params)
    completion = dict.fromkeys(COMPLETION_EVENTS, EMPTY_DURATIONS)
    rows = conn.execute(text(f"""
        SELECT a.event, {_durations_sql('a.recorded_at - c.created_at')}
        FROM cases c
        JOIN audit_log a ON a.case_id = c.case_id
                        AND a.record_type = 'Transition' AND a.state_after = 'Completed'
        WHERE {_COHORT} GROUP BY a.event"""), window.params)
    completion.update({row[0]: _durations(row[1:]) for row in rows})
    return Flow(opened=sum(by_state.values()), by_state=by_state,
                by_outcome=_counts(conn, _OUTCOME, window.params), completion=completion)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `$DC run --rm backend pytest tests/test_metrics_flow.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/hospital_agent/metrics.py backend/tests/metrics_seed.py backend/tests/test_metrics_flow.py
git commit -m "Add the metrics window and group A, the flow of opened cases" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Group B — human load

**Files:**
- Modify: `backend/hospital_agent/metrics.py` (append after group A)
- Test: `backend/tests/test_metrics_human.py`

**Interfaces:**
- Consumes: `Window`, `Durations`, `EMPTY_DURATIONS`, `_EVENTS`, `_durations_sql`, `_durations`, `_counts` (Task 2).
- Produces: `DECISION_EVENTS`, `HumanLoad(escalations_entered, decisions, decided_by_kind, open_by_kind, time_to_decision, open_now, oldest_open_seconds)`, `human_load(conn, window) -> HumanLoad`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_metrics_human.py`:

```python
"""Group B - the load on people (sub-project 14, design §4.2)."""
from datetime import UTC, datetime, timedelta

from pytest import approx

from hospital_agent import metrics
from hospital_agent.metrics import EMPTY_DURATIONS, Durations, Window
from tests.metrics_seed import T0, add_approval, add_case, add_row, at

WINDOW = Window(T0, T0 + timedelta(days=1))
REVIEW = "AwaitingHumanReview"


def test_entries_decisions_kinds_and_time_to_decision(app_engine):
    with app_engine.begin() as conn:
        # C-1: escalated at 0, rejected at 30 min
        add_case(conn, "C-1", created_at=at(-5), state="Failed")
        add_row(conn, "C-1", "MEDICAL_QUESTION_DETECTED", at=at(0), before="Classifying", after=REVIEW)
        add_approval(conn, "APPR-1", "C-1", "MedicalQuestion", granted_at=at(30), decision="reject")
        add_row(conn, "C-1", "HUMAN_REJECTED", at=at(30), before=REVIEW, after="Failed", approval_id="APPR-1")
        # C-2: escalated twice - resumed after 10 min, escalated again, resolved after 20 min
        add_case(conn, "C-2", created_at=at(-5), state="Completed")
        add_row(conn, "C-2", "RETRY_EXHAUSTED", at=at(0), before="RetrievingData", after=REVIEW)
        add_approval(conn, "APPR-2a", "C-2", "RetryExhausted", granted_at=at(10), decision="approve")
        add_row(conn, "C-2", "HUMAN_APPROVED", at=at(10), before=REVIEW, after="Planning", approval_id="APPR-2a")
        add_row(conn, "C-2", "RETRY_EXHAUSTED", at=at(40), before="RetrievingData", after=REVIEW)
        add_approval(conn, "APPR-2b", "C-2", "RetryExhausted", granted_at=at(60))
        add_row(conn, "C-2", "HUMAN_RESOLVED_CASE", at=at(60), before=REVIEW, after="Completed", approval_id="APPR-2b")
        # design decision 6: _grant writes its approval in its own transaction, so a blocked
        # decision leaves one behind - it must not count as a decision.
        add_approval(conn, "APPR-orphan", "C-2", "RetryExhausted", granted_at=at(61), decision="approve")
        add_row(conn, "C-2", "HUMAN_APPROVED", at=at(61), before="Completed", after="Completed",
                record_type="Blocked", reasons=["guard_failed"])
    with app_engine.connect() as conn:
        load = metrics.human_load(conn, WINDOW)
    assert load.escalations_entered == 3
    assert load.decisions == {"HUMAN_APPROVED": 1, "HUMAN_RESOLVED_CASE": 1, "HUMAN_REJECTED": 1}
    assert load.decided_by_kind == {"MedicalQuestion": 1, "RetryExhausted": 2}
    # [600, 1200, 1800]: p50 = 1200, p95 = 1200 + 0.9 * 600 = 1740
    assert load.time_to_decision == Durations(3, approx(1200.0), approx(1740.0), approx(1800.0))


def test_the_open_queue_is_now_not_the_window(app_engine):
    now = datetime.now(UTC)
    with app_engine.begin() as conn:
        add_case(conn, "C-open", created_at=now - timedelta(hours=6), state=REVIEW, escalation_kind="PolicyDenied")
        # an earlier stint, already decided: the case's wait is measured from its latest entry
        add_row(conn, "C-open", "POLICY_DENIED", at=now - timedelta(hours=5), before="Planning", after=REVIEW)
        add_row(conn, "C-open", "HUMAN_APPROVED", at=now - timedelta(hours=4), before=REVIEW, after="Planning")
        add_row(conn, "C-open", "POLICY_DENIED", at=now - timedelta(hours=2), before="Planning", after=REVIEW)
        add_case(conn, "C-open2", created_at=now - timedelta(hours=1), state=REVIEW, escalation_kind="PolicyDenied")
        add_row(conn, "C-open2", "POLICY_DENIED", at=now - timedelta(minutes=30), before="Planning", after=REVIEW)
    with app_engine.connect() as conn:
        load = metrics.human_load(conn, WINDOW)  # WINDOW is 2026-09-01: nothing happened in it
    assert load.escalations_entered == 0
    assert load.time_to_decision == EMPTY_DURATIONS
    assert load.open_now == 2
    assert load.open_by_kind == {"PolicyDenied": 2}
    assert 7200 <= load.oldest_open_seconds < 7200 + 120


def test_an_empty_database_has_no_load(app_engine):
    with app_engine.connect() as conn:
        load = metrics.human_load(conn, WINDOW)
    assert load == metrics.HumanLoad(
        escalations_entered=0,
        decisions={"HUMAN_APPROVED": 0, "HUMAN_RESOLVED_CASE": 0, "HUMAN_REJECTED": 0},
        decided_by_kind={}, open_by_kind={}, time_to_decision=EMPTY_DURATIONS,
        open_now=0, oldest_open_seconds=None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$DC run --rm backend pytest tests/test_metrics_human.py -v`
Expected: FAIL — `AttributeError: module 'hospital_agent.metrics' has no attribute 'human_load'`.

- [ ] **Step 3: Append group B to `metrics.py`**

```python
# --- B: human load (events in the window; design §4.2) -------------------------------------

DECISION_EVENTS = ("HUMAN_APPROVED", "HUMAN_RESOLVED_CASE", "HUMAN_REJECTED")
_DECISIONS = "('HUMAN_APPROVED', 'HUMAN_RESOLVED_CASE', 'HUMAN_REJECTED')"


@dataclass(frozen=True)
class HumanLoad:
    escalations_entered: int
    decisions: dict[str, int]
    decided_by_kind: dict[str, int]  # decided in the window
    open_by_kind: dict[str, int]  # open now, whatever the window
    time_to_decision: Durations
    open_now: int
    oldest_open_seconds: float | None


def human_load(conn: Connection, window: Window) -> HumanLoad:
    params = window.params
    entered = conn.execute(text(f"""
        SELECT count(*) FROM audit_log
        WHERE record_type = 'Transition' AND state_after = 'AwaitingHumanReview' AND {_EVENTS}"""),
        params).scalar_one()
    decisions = dict.fromkeys(DECISION_EVENTS, 0)
    decisions.update(_counts(conn, f"""
        SELECT event, count(*) FROM audit_log
        WHERE record_type = 'Transition' AND event IN {_DECISIONS} AND {_EVENTS} GROUP BY 1""", params))
    # design decision 6: the approval the committed transition actually used. Never a count of
    # approvals rows - _grant writes one in its own transaction, and a blocked decision (or a
    # double submit) leaves one behind.
    decided_by_kind = _counts(conn, f"""
        SELECT ap.escalation_kind, count(*) FROM audit_log a
        JOIN approvals ap ON ap.approval_id = a.approval_id
        WHERE a.record_type = 'Transition' AND a.event IN {_DECISIONS}
          AND a.recorded_at >= :start AND a.recorded_at < :end AND ap.escalation_kind IS NOT NULL
        GROUP BY 1""", params)
    # Each entry into the review queue, paired with the next transition out of it for the same
    # case - while a case waits, a human decision is the only way out (§3).
    time_to_decision = _durations(conn.execute(text(f"""
        WITH entries AS (
            SELECT case_id, audit_id, recorded_at FROM audit_log
            WHERE record_type = 'Transition' AND state_after = 'AwaitingHumanReview' AND {_EVENTS}),
        decided AS (
            SELECT e.recorded_at AS entered_at,
                   (SELECT x.recorded_at FROM audit_log x
                    WHERE x.case_id = e.case_id AND x.audit_id > e.audit_id
                      AND x.record_type = 'Transition' AND x.state_before = 'AwaitingHumanReview'
                    ORDER BY x.audit_id LIMIT 1) AS decided_at
            FROM entries e)
        SELECT {_durations_sql('decided_at - entered_at')} FROM decided WHERE decided_at IS NOT NULL"""),
        params).one())
    open_by_kind = _counts(conn, """
        SELECT escalation_kind, count(*) FROM cases
        WHERE state = 'AwaitingHumanReview' AND escalation_kind IS NOT NULL GROUP BY 1""", {})
    # now() is the transaction's start, so the age agrees with every other number in the answer.
    open_now, oldest = conn.execute(text("""
        SELECT count(*), extract(epoch FROM now() - min(entered_at))::double precision FROM (
            SELECT c.case_id, max(a.recorded_at) AS entered_at FROM cases c
            JOIN audit_log a ON a.case_id = c.case_id AND a.record_type = 'Transition'
                            AND a.state_after = 'AwaitingHumanReview'
            WHERE c.state = 'AwaitingHumanReview' GROUP BY c.case_id) open_cases""")).one()
    return HumanLoad(int(entered), decisions, decided_by_kind, open_by_kind, time_to_decision,
                     int(open_now), oldest)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `$DC run --rm backend pytest tests/test_metrics_human.py tests/test_metrics_flow.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/hospital_agent/metrics.py backend/tests/test_metrics_human.py
git commit -m "Add metrics group B, the load on people" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Group C — external tools

**Files:**
- Modify: `backend/hospital_agent/metrics.py` (append after group B)
- Test: `backend/tests/test_metrics_tools.py`

**Interfaces:**
- Consumes: Task 2 helpers.
- Produces: `FAILURE_EVENTS`, `ToolAction(action, by_status, success_rate, latency)`, `FailureReason(outcome, reason, count)`, `Tools(actions, failure_events, failure_reasons, retried_calls, sources)` (`sources` defaults to `{}`; `compute()` fills it in Task 6), `tools(conn, window) -> Tools`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_metrics_tools.py`:

```python
"""Group C - the external tools (sub-project 14, design §4.3)."""
from datetime import timedelta

from pytest import approx

from hospital_agent import metrics
from hospital_agent.metrics import Durations, FailureReason, ToolAction, Window
from tests.metrics_seed import T0, add_case, add_execution, add_row, at

WINDOW = Window(T0, T0 + timedelta(days=1))
SECOND = timedelta(seconds=1)


def test_calls_success_rate_and_latency_per_action(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-1", created_at=at(0))
        add_execution(conn, "C-1", "CheckAppointment", "succeeded", started_at=at(1), finished_at=at(1) + 2 * SECOND)
        add_execution(conn, "C-1", "CheckAppointment", "succeeded", started_at=at(2), finished_at=at(2) + 4 * SECOND)
        add_execution(conn, "C-1", "CheckAppointment", "failed", started_at=at(3), finished_at=at(3) + 6 * SECOND,
                      attempt=2)
        # still running: counted as a call, not measured, not in the success rate
        add_execution(conn, "C-1", "CheckAppointment", "started", started_at=at(4))
        add_execution(conn, "C-1", "CheckDocuments", "unknown", started_at=at(5), finished_at=at(5) + SECOND)
        # never started (the executions row's first status): in no window
        add_execution(conn, "C-1", "CheckDocuments", "intent", started_at=None)
        add_execution(conn, "C-1", "CheckDocuments", "succeeded", started_at=at(-1), finished_at=at(-1) + SECOND)
    with app_engine.connect() as conn:
        tools = metrics.tools(conn, WINDOW)
    # [2, 4, 6]: p50 = 4, p95 = 4 + 0.9 * 2 = 5.8
    assert tools.actions == [
        ToolAction("CheckAppointment", {"failed": 1, "started": 1, "succeeded": 2}, approx(2 / 3),
                   Durations(3, approx(4.0), approx(5.8), approx(6.0))),
        ToolAction("CheckDocuments", {"unknown": 1}, 0.0, Durations(1, approx(1.0), approx(1.0), approx(1.0))),
    ]
    assert tools.retried_calls == 1


def test_failures_by_event_and_by_reason(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-1", created_at=at(0))
        add_row(conn, "C-1", "TOOL_TRANSIENT_FAILURE", at=at(1), before="RetrievingData", after="Planning")
        add_row(conn, "C-1", "TOOL_TRANSIENT_FAILURE", at=at(2), before="RetrievingData", after="Planning")
        add_row(conn, "C-1", "RETRY_EXHAUSTED", at=at(3), before="RetrievingData", after="AwaitingHumanReview")
        # the outcome rows, as the State Manager writes them: the reason in policy_reasons
        failed = dict(before="RetrievingData", after="RetrievingData", record_type="ExecutionFailed")
        add_row(conn, "C-1", "TOOL_TRANSIENT_FAILURE", at=at(1), reasons=["tool:transient_failure:timeout"], **failed)
        add_row(conn, "C-1", "TOOL_TRANSIENT_FAILURE", at=at(2), reasons=["tool:transient_failure:unavailable"],
                **failed)
        add_row(conn, "C-1", "RETRY_EXHAUSTED", at=at(3), reasons=["tool:transient_failure:timeout"], **failed)
        add_row(conn, "C-1", "HUMAN_REVIEW_REQUIRED", at=at(4), before="RetrievingData", after="RetrievingData",
                record_type="ExecutionUnknown")
        add_row(conn, "C-1", "DATA_RETRIEVED", at=at(5), before="RetrievingData", after="RetrievingData",
                record_type="ExecutionSucceeded")
    with app_engine.connect() as conn:
        tools = metrics.tools(conn, WINDOW)
    assert tools.failure_events == {"TOOL_TRANSIENT_FAILURE": 2, "RETRY_EXHAUSTED": 1}
    assert tools.failure_reasons == [
        FailureReason("ExecutionFailed", "tool:transient_failure:timeout", 2),
        FailureReason("ExecutionFailed", "tool:transient_failure:unavailable", 1),
        FailureReason("ExecutionUnknown", None, 1),
    ]


def test_an_empty_window_has_no_calls(app_engine):
    with app_engine.connect() as conn:
        tools = metrics.tools(conn, WINDOW)
    assert tools == metrics.Tools(actions=[], failure_events={"TOOL_TRANSIENT_FAILURE": 0, "RETRY_EXHAUSTED": 0},
                                  failure_reasons=[], retried_calls=0, sources={})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$DC run --rm backend pytest tests/test_metrics_tools.py -v`
Expected: FAIL — `ImportError: cannot import name 'FailureReason'`.

- [ ] **Step 3: Append group C to `metrics.py`**

Add `field` to the dataclasses import at the top of the file: `from dataclasses import dataclass, field`. Then append:

```python
# --- C: external tools (events in the window, by executions.started_at; design §4.3) ------

FAILURE_EVENTS = ("TOOL_TRANSIENT_FAILURE", "RETRY_EXHAUSTED")
FINISHED = ("succeeded", "failed", "unknown")
_STARTED = "started_at >= :start AND started_at < :end"


@dataclass(frozen=True)
class ToolAction:
    action: str
    by_status: dict[str, int]
    success_rate: float | None  # succeeded / finished; None when nothing finished
    latency: Durations  # finished_at - started_at, the whole Tool Executor call


@dataclass(frozen=True)
class FailureReason:
    outcome: str  # the audit record_type: ExecutionFailed | ExecutionUnknown
    reason: str | None  # as recorded, e.g. tool:transient_failure:timeout
    count: int


@dataclass(frozen=True)
class Tools:
    actions: list[ToolAction]
    failure_events: dict[str, int]
    failure_reasons: list[FailureReason]
    retried_calls: int
    sources: dict[str, str | None] = field(default_factory=dict)  # compute() sets it from app.state


def tools(conn: Connection, window: Window) -> Tools:
    params = window.params
    by_action: dict[str, dict[str, int]] = {}
    for action, status, count in conn.execute(text(
            f"SELECT action, status, count(*) FROM executions WHERE {_STARTED} GROUP BY 1, 2 ORDER BY 1, 2"),
            params):
        by_action.setdefault(action, {})[status] = int(count)
    latency = {row[0]: _durations(row[1:]) for row in conn.execute(text(f"""
        SELECT action, {_durations_sql('finished_at - started_at')} FROM executions
        WHERE {_STARTED} AND finished_at IS NOT NULL GROUP BY 1"""), params)}
    actions = []
    for action in sorted(by_action):
        statuses = by_action[action]
        finished = sum(statuses.get(status, 0) for status in FINISHED)
        actions.append(ToolAction(action, statuses, statuses.get("succeeded", 0) / finished if finished else None,
                                  latency.get(action, EMPTY_DURATIONS)))
    failure_events = dict.fromkeys(FAILURE_EVENTS, 0)
    failure_events.update(_counts(conn, f"""
        SELECT event, count(*) FROM audit_log
        WHERE record_type = 'Transition' AND event IN ('TOOL_TRANSIENT_FAILURE', 'RETRY_EXHAUSTED')
          AND {_EVENTS} GROUP BY 1""", params))
    failure_reasons = [FailureReason(outcome, reason, int(count)) for outcome, reason, count in conn.execute(text("""
        SELECT a.record_type, r.reason, count(*) FROM audit_log a
        LEFT JOIN LATERAL jsonb_array_elements_text(a.policy_reasons) AS r(reason) ON true
        WHERE a.record_type IN ('ExecutionFailed', 'ExecutionUnknown')
          AND a.recorded_at >= :start AND a.recorded_at < :end
        GROUP BY 1, 2 ORDER BY 1, 2"""), params)]
    retried = conn.execute(text(f"SELECT count(*) FROM executions WHERE {_STARTED} AND attempt_number > 1"),
                           params).scalar_one()
    return Tools(actions, failure_events, failure_reasons, int(retried))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `$DC run --rm backend pytest tests/test_metrics_tools.py tests/test_metrics_human.py tests/test_metrics_flow.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/hospital_agent/metrics.py backend/tests/test_metrics_tools.py
git commit -m "Add metrics group C, the external tools" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Groups D (patient SLA) and E (policy)

**Files:**
- Modify: `backend/hospital_agent/metrics.py` (append after group C)
- Test: `backend/tests/test_metrics_sla_policy.py`

**Interfaces:**
- Consumes: Task 2 helpers.
- Produces: `PatientSla(requests, met, breached, other, waiting, rate)`, `patient_sla(conn, window) -> PatientSla`, `POLICY_EVENTS`, `Policy(decisions, blocked, blocked_by_reason, blocked_by_event)`, `policy(conn, window) -> Policy`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_metrics_sla_policy.py`:

```python
"""Groups D and E - the patient's SLA and the policy layers (sub-project 14, design §4.4-§4.5)."""
from datetime import timedelta

from pytest import approx

from hospital_agent import metrics
from hospital_agent.metrics import PatientSla, Policy, Window
from tests.metrics_seed import T0, add_case, add_row, at

WINDOW = Window(T0, T0 + timedelta(days=1))
WAITING = "AwaitingPatientInput"
ASK = dict(before="AssessingReadiness", after=WAITING)


def test_each_wait_ends_met_breached_other_or_is_still_waiting(app_engine):
    with app_engine.begin() as conn:
        for case_id in ("C-met", "C-met-late", "C-late", "C-other", "C-wait", "C-twice"):
            add_case(conn, case_id, created_at=at(0))
        add_row(conn, "C-met", "MISSING_INFORMATION_DETECTED", at=at(1), **ASK)
        add_row(conn, "C-met", "DOCUMENT_UPLOADED", at=at(5), before=WAITING, after="Classifying")
        # the window decides which waits count, not when they ended
        add_row(conn, "C-met-late", "MISSING_INFORMATION_DETECTED", at=at(1), **ASK)
        add_row(conn, "C-met-late", "DOCUMENT_UPLOADED", at=T0 + timedelta(days=2), before=WAITING,
                after="Classifying")
        add_row(conn, "C-late", "MISSING_INFORMATION_DETECTED", at=at(1), **ASK)
        add_row(conn, "C-late", "TIMEOUT_EXPIRED", at=at(9), before=WAITING, after="AwaitingHumanReview")
        add_row(conn, "C-other", "MISSING_INFORMATION_DETECTED", at=at(1), **ASK)
        add_row(conn, "C-other", "HUMAN_REVIEW_REQUIRED", at=at(2), before=WAITING, after="AwaitingHumanReview")
        add_row(conn, "C-wait", "MISSING_INFORMATION_DETECTED", at=at(1), **ASK)
        # a rejected upload is a self-loop: the wait goes on
        add_row(conn, "C-wait", "DOCUMENT_UPLOADED", at=at(2), before=WAITING, after=WAITING)
        # two waits on one case: each is ended by the first exit after it
        add_row(conn, "C-twice", "MISSING_INFORMATION_DETECTED", at=at(1), **ASK)
        add_row(conn, "C-twice", "DOCUMENT_UPLOADED", at=at(2), before=WAITING, after="Classifying")
        add_row(conn, "C-twice", "MISSING_INFORMATION_DETECTED", at=at(3), **ASK)
        add_row(conn, "C-twice", "TIMEOUT_EXPIRED", at=at(4), before=WAITING, after="AwaitingHumanReview")
        # a wait that began before the window is not counted
        add_row(conn, "C-met", "MISSING_INFORMATION_DETECTED", at=at(-10), **ASK)
    with app_engine.connect() as conn:
        sla = metrics.patient_sla(conn, WINDOW)
    assert sla == PatientSla(requests=7, met=3, breached=2, other=1, waiting=1, rate=approx(0.6))


def test_no_waits_means_no_rate(app_engine):
    with app_engine.connect() as conn:
        assert metrics.patient_sla(conn, WINDOW) == PatientSla(0, 0, 0, 0, 0, None)


def test_policy_decisions_and_blocked_rows_by_reason_and_event(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-1", created_at=at(0))
        add_row(conn, "C-1", "POLICY_ALLOWED", at=at(1), before="Planning", after="RetrievingData")
        add_row(conn, "C-1", "POLICY_ALLOWED", at=at(2), before="Planning", after="RetrievingData")
        add_row(conn, "C-1", "POLICY_DENIED", at=at(3), before="Planning", after="AwaitingHumanReview")
        # a Blocked row is written with guards = {} and its reason in policy_reasons
        blocked = dict(before="Completed", after="Completed", record_type="Blocked")
        add_row(conn, "C-1", "HUMAN_APPROVED", at=at(4), reasons=["guard_failed"], **blocked)
        add_row(conn, "C-1", "HUMAN_REVIEW_REQUIRED", at=at(5), reasons=["invalid_escalation_reason"], **blocked)
        add_row(conn, "C-1", "HUMAN_APPROVED", at=at(6), reasons=["guard_failed"], **blocked)
        add_row(conn, "C-1", "HUMAN_APPROVED", at=at(-6), reasons=["guard_failed"], **blocked)  # before the window
    with app_engine.connect() as conn:
        result = metrics.policy(conn, WINDOW)
    assert result == Policy(
        decisions={"POLICY_ALLOWED": 2, "POLICY_DENIED": 1, "POLICY_HUMAN_REVIEW_REQUIRED": 0},
        blocked=3,
        blocked_by_reason={"guard_failed": 2, "invalid_escalation_reason": 1},
        blocked_by_event={"HUMAN_APPROVED": 2, "HUMAN_REVIEW_REQUIRED": 1})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$DC run --rm backend pytest tests/test_metrics_sla_policy.py -v`
Expected: FAIL — `ImportError: cannot import name 'PatientSla'`.

- [ ] **Step 3: Append groups D and E to `metrics.py`**

```python
# --- D: the patient's SLA (events: the waits that began in the window; design §4.4) ------


@dataclass(frozen=True)
class PatientSla:
    requests: int
    met: int  # the wait ended in DOCUMENT_UPLOADED -> Classifying
    breached: int  # the wait ended in TIMEOUT_EXPIRED
    other: int  # the wait ended in any other way out (a TemporalViolation escalation)
    waiting: int  # not ended yet
    rate: float | None  # met / (met + breached); None when neither happened


def patient_sla(conn: Connection, window: Window) -> PatientSla:
    # Each wait is ended by the next transition that leaves AwaitingPatientInput. state_after
    # must differ: a rejected upload is a DOCUMENT_UPLOADED self-loop that does not end it.
    # The metric reports the system's own verdict - which event ended the wait - and never
    # re-judges it against cases.patient_deadline.
    exits: dict[str | None, int] = {exit_event: int(count) for exit_event, count in conn.execute(text(f"""
        WITH entries AS (
            SELECT case_id, audit_id FROM audit_log
            WHERE record_type = 'Transition' AND event = 'MISSING_INFORMATION_DETECTED' AND {_EVENTS})
        SELECT (SELECT x.event FROM audit_log x
                WHERE x.case_id = e.case_id AND x.audit_id > e.audit_id AND x.record_type = 'Transition'
                  AND x.state_before = 'AwaitingPatientInput' AND x.state_after <> 'AwaitingPatientInput'
                ORDER BY x.audit_id LIMIT 1) AS exit_event,
               count(*)
        FROM entries e GROUP BY 1"""), window.params)}
    requests = sum(exits.values())
    met, breached, waiting = exits.get("DOCUMENT_UPLOADED", 0), exits.get("TIMEOUT_EXPIRED", 0), exits.get(None, 0)
    return PatientSla(requests, met, breached, requests - met - breached - waiting, waiting,
                      met / (met + breached) if met + breached else None)


# --- E: the policy layers (events in the window; design §4.5) ----------------------------

POLICY_EVENTS = ("POLICY_ALLOWED", "POLICY_DENIED", "POLICY_HUMAN_REVIEW_REQUIRED")


@dataclass(frozen=True)
class Policy:
    decisions: dict[str, int]
    blocked: int
    blocked_by_reason: dict[str, int]
    blocked_by_event: dict[str, int]


def policy(conn: Connection, window: Window) -> Policy:
    params = window.params
    decisions = dict.fromkeys(POLICY_EVENTS, 0)
    decisions.update(_counts(conn, f"""
        SELECT event, count(*) FROM audit_log
        WHERE record_type = 'Transition'
          AND event IN ('POLICY_ALLOWED', 'POLICY_DENIED', 'POLICY_HUMAN_REVIEW_REQUIRED')
          AND {_EVENTS} GROUP BY 1""", params))
    blocked_by_event = _counts(conn, f"""
        SELECT event, count(*) FROM audit_log WHERE record_type = 'Blocked' AND {_EVENTS} GROUP BY 1""", params)
    # design §4.5 E2: a Blocked row is written with guards = {}; its reason is in policy_reasons.
    blocked_by_reason = _counts(conn, """
        SELECT r.reason, count(*) FROM audit_log a
        CROSS JOIN LATERAL jsonb_array_elements_text(a.policy_reasons) AS r(reason)
        WHERE a.record_type = 'Blocked' AND a.recorded_at >= :start AND a.recorded_at < :end
        GROUP BY 1""", params)
    return Policy(decisions, sum(blocked_by_event.values()), blocked_by_reason, blocked_by_event)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `$DC run --rm backend pytest tests/test_metrics_sla_policy.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/hospital_agent/metrics.py backend/tests/test_metrics_sla_policy.py
git commit -m "Add metrics groups D and E, the patient's SLA and the policy layers" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `compute()` — one read-only snapshot

**Files:**
- Modify: `backend/hospital_agent/metrics.py` (imports + append)
- Test: `backend/tests/test_metrics_compute.py`

**Interfaces:**
- Consumes: `flow`, `human_load`, `tools`, `patient_sla`, `policy` (Tasks 2-5) — `compute()` looks each up as a module global at call time, so tests can monkeypatch them.
- Produces: `Metrics(window, generated_at, flow, human_load, tools, patient_sla, policy)`, `compute(engine, window, sources: Mapping[str, str | None]) -> Metrics` raising `MetricsUnavailable` on a statement timeout.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_metrics_compute.py`:

```python
"""compute(): every group in one REPEATABLE READ, READ ONLY snapshot (sub-project 14, design §5),
checked on seeded rows and on real flows driven through the State Manager."""
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from hospital_agent import metrics
from hospital_agent.metrics import MetricsUnavailable, Window
from tests.driver import Driver
from tests.metrics_seed import T0, add_case, at

WINDOW = Window(T0, T0 + timedelta(days=1))
MEDICAL = "Should I stop taking my blood thinner?"


def test_compute_returns_every_group_and_the_sources(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-1", created_at=at(1), state="Completed")
    result = metrics.compute(app_engine, WINDOW, {"appointments": "mock", "documents": None})
    assert result.window == WINDOW
    assert result.flow.opened == 1
    assert result.tools.sources == {"appointments": "mock", "documents": None}
    assert abs((result.generated_at - datetime.now(UTC)).total_seconds()) < 60


def test_the_snapshot_is_repeatable_read_with_a_statement_timeout(app_engine, monkeypatch):
    seen = {}
    real_flow = metrics.flow

    def spying_flow(conn, window):
        seen["isolation"] = conn.execute(text("SHOW transaction_isolation")).scalar_one()
        seen["read_only"] = conn.execute(text("SHOW transaction_read_only")).scalar_one()
        seen["timeout"] = conn.execute(text("SHOW statement_timeout")).scalar_one()
        return real_flow(conn, window)

    monkeypatch.setattr(metrics, "flow", spying_flow)
    metrics.compute(app_engine, WINDOW, {})
    assert seen == {"isolation": "repeatable read", "read_only": "on", "timeout": "5s"}


def test_the_transaction_refuses_a_write(app_engine, monkeypatch):
    monkeypatch.setattr(metrics, "flow", lambda conn, window: conn.execute(text("UPDATE cases SET state = state")))
    with pytest.raises(DBAPIError) as refused:
        metrics.compute(app_engine, WINDOW, {})
    assert isinstance(refused.value.orig, psycopg.errors.ReadOnlySqlTransaction)


def test_a_statement_timeout_refuses_the_whole_answer(app_engine, monkeypatch):
    monkeypatch.setattr(metrics, "STATEMENT_TIMEOUT", "50ms")
    monkeypatch.setattr(metrics, "flow", lambda conn, window: conn.execute(text("SELECT pg_sleep(1)")))
    with pytest.raises(MetricsUnavailable):
        metrics.compute(app_engine, WINDOW, {})


def test_real_flows_show_up_as_the_seeded_shapes_assume(sm, app_engine):
    medical = Driver(sm, app_engine)
    medical.submit()
    medical.validate(MEDICAL)
    medical.medical_question()
    retry = Driver(sm, app_engine, patient_id="P-20000")
    retry.to_classified()
    retry.plan()
    retry.propose()
    retry.allow()
    retry.retry_exhausted()
    now = datetime.now(UTC)
    result = metrics.compute(app_engine, Window(now - timedelta(hours=1), now + timedelta(hours=1)), {})
    assert result.flow.opened == 2
    assert result.flow.by_outcome["MedicalQuestion"] == 1
    assert result.human_load.escalations_entered == 2
    assert result.human_load.open_by_kind == {"MedicalQuestion": 1, "RetryExhausted": 1}
    assert result.human_load.open_now == 2
    assert result.tools.failure_events["RETRY_EXHAUSTED"] == 1
    assert result.policy.decisions["POLICY_ALLOWED"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$DC run --rm backend pytest tests/test_metrics_compute.py -v`
Expected: FAIL — `AttributeError: module 'hospital_agent.metrics' has no attribute 'compute'`.

- [ ] **Step 3: Add `compute()`**

Change the imports at the top of `metrics.py` to:

```python
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from psycopg import errors as pg_errors
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError
```

Append:

```python
# --- compute: every group, one snapshot (design §5) --------------------------------------


@dataclass(frozen=True)
class Metrics:
    window: Window
    generated_at: datetime
    flow: Flow
    human_load: HumanLoad
    tools: Tools
    patient_sla: PatientSla
    policy: Policy


def compute(engine: Engine, window: Window, sources: Mapping[str, str | None]) -> Metrics:
    """All five groups in one REPEATABLE READ, READ ONLY transaction, so they agree with each
    other; STATEMENT_TIMEOUT bounds every query, and a timeout refuses the whole answer."""
    try:
        with engine.connect() as raw:
            conn = raw.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
            with conn.begin():
                conn.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
                generated_at = conn.execute(text("SELECT now()")).scalar_one()
                return Metrics(
                    window=window,
                    generated_at=generated_at,
                    flow=flow(conn, window),
                    human_load=human_load(conn, window),
                    tools=replace(tools(conn, window), sources=dict(sources)),
                    patient_sla=patient_sla(conn, window),
                    policy=policy(conn, window),
                )
    except OperationalError as exc:
        if isinstance(exc.orig, pg_errors.QueryCanceled):
            raise MetricsUnavailable("statement_timeout") from None
        raise
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `$DC run --rm backend pytest tests/test_metrics_compute.py tests/test_metrics_flow.py tests/test_metrics_human.py tests/test_metrics_tools.py tests/test_metrics_sla_policy.py -v`
Expected: 19 passed.

If `test_real_flows_show_up_as_the_seeded_shapes_assume` fails on a count, the seeded shapes in `metrics_seed.py` diverge from what the agent writes: fix the query to match the real rows (and the seeded tests with it) — never the assertion on the real flow.

- [ ] **Step 5: Commit**

```bash
git add backend/hospital_agent/metrics.py backend/tests/test_metrics_compute.py
git commit -m "Compute every metrics group in one read-only snapshot" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `GET /api/admin/metrics`

**Files:**
- Modify: `backend/hospital_agent/api/deps.py`
- Modify: `backend/hospital_agent/api/schemas.py` (append)
- Create: `backend/hospital_agent/api/routes_admin.py`
- Modify: `backend/hospital_agent/api/app.py:39` and `:173`
- Modify: `docs/api.md`
- Test: `backend/tests/test_api_admin.py`

**Interfaces:**
- Consumes: `metrics.Window`, `metrics.InvalidWindow`, `metrics.compute`, `metrics.MetricsUnavailable` (Tasks 2, 6).
- Produces: `require_admin` in `api.deps`; `MetricsResponse` in `api.schemas`; the route. JSON shape (Task 8 types mirror it exactly): `{window: {start, end}, generated_at, flow: {opened, by_state, by_outcome, completion: {<event>: {count, p50, p95, max}}}, human_load: {escalations_entered, decisions, decided_by_kind, open_by_kind, time_to_decision, open_now, oldest_open_seconds}, tools: {actions: [{action, by_status, success_rate, latency}], failure_events, failure_reasons: [{outcome, reason, count}], retried_calls, sources: {appointments, documents}}, patient_sla: {requests, met, breached, other, waiting, rate}, policy: {decisions, blocked, blocked_by_reason, blocked_by_event}}`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_api_admin.py`:

```python
"""/api/admin/metrics (sub-project 14, design §5): admin_staff only, a validated window, and
aggregates without a single patient identifier."""
import logging

import pytest
from fastapi.testclient import TestClient

from hospital_agent import metrics
from hospital_agent.api.app import create_app
from hospital_agent.auth import demo_password
from tests.metrics_seed import add_case, add_row, at

NURSE, ADMIN, PATIENT = "coordinator_nurse", "admin_coordinator", "P-10041"
FROM, TO = "2026-09-01T00:00:00+00:00", "2026-09-02T00:00:00+00:00"
GROUPS = {"window", "generated_at", "flow", "human_load", "tools", "patient_sla", "policy"}


@pytest.fixture
def client(app_engine):
    with TestClient(create_app(app_engine)) as test_client:
        yield test_client


def headers(client, user_id):
    token = client.post("/api/auth/login", json={"user_id": user_id, "password": demo_password()}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def get(client, auth=None, **params):
    return client.get("/api/admin/metrics", params={"from": FROM, "to": TO, **params}, headers=auth or {})


def test_no_token_is_401(client):
    response = get(client)
    assert (response.status_code, response.json()["detail"]) == (401, "not_authenticated")


@pytest.mark.parametrize("user_id", [PATIENT, NURSE])
def test_anyone_but_admin_staff_is_403(client, user_id):
    response = get(client, headers(client, user_id))
    assert (response.status_code, response.json()["detail"]) == (403, "admin_only")


def test_admin_staff_gets_every_group(client):
    response = get(client, headers(client, ADMIN))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == GROUPS
    assert body["window"] == {"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"}
    # create_app(engine) starts no orchestrator, so neither source is reported
    assert body["tools"]["sources"] == {"appointments": None, "documents": None}


def test_a_missing_parameter_is_the_apps_invalid_body(client):
    response = client.get("/api/admin/metrics", params={"from": FROM}, headers=headers(client, ADMIN))
    assert (response.status_code, response.json()["detail"]) == (422, "invalid_body")


@pytest.mark.parametrize("params, code", [
    ({"from": "yesterday"}, "invalid_range"),
    ({"from": "2026-09-01T00:00:00"}, "invalid_range"),  # no time zone
    ({"from": TO, "to": FROM}, "invalid_range"),
    ({"to": "2026-12-01T00:00:01+00:00"}, "range_too_large"),
])
def test_a_bad_window_is_422_with_its_code(client, params, code):
    response = get(client, headers(client, ADMIN), **params)
    assert (response.status_code, response.json()["detail"]) == (422, code)


def test_a_timeout_is_503_and_never_a_partial_answer(client, monkeypatch):
    def timed_out(engine, window, sources):
        raise metrics.MetricsUnavailable("statement_timeout")

    monkeypatch.setattr(metrics, "compute", timed_out)
    response = get(client, headers(client, ADMIN))
    assert (response.status_code, response.json()["detail"]) == (503, "metrics_unavailable")


def test_the_answer_and_the_log_carry_no_patient_identifier(client, app_engine, caplog):
    with app_engine.begin() as conn:
        add_case(conn, "CASE-PRIVATE-1", created_at=at(1), state="AwaitingHumanReview",
                 escalation_kind="MedicalQuestion", patient_id="P-20000")
        add_row(conn, "CASE-PRIVATE-1", "MEDICAL_QUESTION_DETECTED", at=at(2), before="Classifying",
                after="AwaitingHumanReview", patient_id="P-20000")
    auth = headers(client, ADMIN)
    with caplog.at_level(logging.INFO, logger="hospital_agent.api.routes_admin"):
        response = get(client, auth)
    assert response.status_code == 200
    assert response.json()["flow"]["opened"] == 1
    for identifier in ("CASE-PRIVATE-1", "P-20000"):
        assert identifier not in response.text
        assert identifier not in caplog.text
    assert "admin_coordinator" in caplog.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$DC run --rm backend pytest tests/test_api_admin.py -v`
Expected: FAIL — every test gets `404` (the route does not exist).

- [ ] **Step 3: Add `require_admin` to `deps.py`**

Change `backend/hospital_agent/api/deps.py:14` from `from ..auth import Principal, auth_secret, verify_token` to:

```python
from ..auth import ADMIN_STAFF, Principal, auth_secret, verify_token
```

Append after `require_staff`:

```python
def require_admin(principal: Principal = Depends(current_principal)) -> Principal:
    """Sub-project 14: the metrics screen is admin_staff's only (design §5)."""
    if principal.role != ADMIN_STAFF:
        raise HTTPException(status_code=403, detail="admin_only")
    return principal
```

- [ ] **Step 4: Append the response models to `schemas.py`**

```python
# --- sub-project 14: the admin metrics screen (design 2026-09-24 §5) ----------------------
# Built from hospital_agent.metrics' dataclasses (from_attributes). Aggregates only: no model
# below has a patient_id, a case_id or any request content (§12.3).


class _FromMetrics(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MetricsWindow(_FromMetrics):
    start: datetime
    end: datetime


class DurationsView(_FromMetrics):
    """Seconds; every field but count is null when nothing was measured."""

    count: int
    p50: float | None
    p95: float | None
    max: float | None


class FlowView(_FromMetrics):
    opened: int
    by_state: dict[str, int]
    by_outcome: dict[str, int]
    completion: dict[str, DurationsView]


class HumanLoadView(_FromMetrics):
    escalations_entered: int
    decisions: dict[str, int]
    decided_by_kind: dict[str, int]
    open_by_kind: dict[str, int]
    time_to_decision: DurationsView
    open_now: int
    oldest_open_seconds: float | None


class ToolActionView(_FromMetrics):
    action: str
    by_status: dict[str, int]
    success_rate: float | None
    latency: DurationsView


class FailureReasonView(_FromMetrics):
    outcome: str
    reason: str | None
    count: int


class ToolsView(_FromMetrics):
    actions: list[ToolActionView]
    failure_events: dict[str, int]
    failure_reasons: list[FailureReasonView]
    retried_calls: int
    sources: dict[str, str | None]


class PatientSlaView(_FromMetrics):
    requests: int
    met: int
    breached: int
    other: int
    waiting: int
    rate: float | None


class PolicyView(_FromMetrics):
    decisions: dict[str, int]
    blocked: int
    blocked_by_reason: dict[str, int]
    blocked_by_event: dict[str, int]


class MetricsResponse(_FromMetrics):
    window: MetricsWindow
    generated_at: datetime
    flow: FlowView
    human_load: HumanLoadView
    tools: ToolsView
    patient_sla: PatientSlaView
    policy: PolicyView
```

- [ ] **Step 5: Create `routes_admin.py`**

`backend/hospital_agent/api/routes_admin.py`:

```python
"""/api/admin: admin_staff's API - sub-project 14's metrics (design
docs/superpowers/specs/2026-09-24-admin-metrics-design.md §5).

Read-only aggregates: no patient_id, case_id or request text ever leaves here (§12.3, design
§11). Viewing is not an Audit event - an audit_log row belongs to a case - so it is logged
here instead, with the viewer and the window only.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.engine import Engine

from .. import metrics
from ..auth import Principal
from .deps import get_engine, require_admin
from .schemas import MetricsResponse

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


def _instant(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid_range") from None


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics(request: Request, start: str = Query(alias="from"), end: str = Query(alias="to"),
                principal: Principal = Depends(require_admin),
                engine: Engine = Depends(get_engine)) -> MetricsResponse:
    """`from` inclusive, `to` exclusive, both ISO-8601 with a time zone, at most 90 days apart."""
    try:
        window = metrics.Window(_instant(start), _instant(end))
    except metrics.InvalidWindow as invalid:
        raise HTTPException(status_code=422, detail=invalid.code) from None
    sources = {"appointments": request.app.state.appointments_source,
               "documents": request.app.state.documents_source}
    try:
        result = metrics.compute(engine, window, sources)
    except metrics.MetricsUnavailable:
        logger.warning("metrics unavailable: statement timeout")
        raise HTTPException(status_code=503, detail="metrics_unavailable") from None
    logger.info("metrics viewed by %s for %s .. %s", principal.user_id,
                window.start.isoformat(), window.end.isoformat())
    return MetricsResponse.model_validate(result)
```

- [ ] **Step 6: Include the router in `app.py`**

Change `backend/hospital_agent/api/app.py:39` from `from . import routes_auth, routes_patient, routes_staff` to:

```python
from . import routes_admin, routes_auth, routes_patient, routes_staff
```

After `app.include_router(routes_staff.router)` (around line 175) add:

```python
    app.include_router(routes_admin.router)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `$DC run --rm backend pytest tests/test_api_admin.py -v`
Expected: 11 passed.

If `test_admin_staff_gets_every_group` fails only on the `window` strings (Pydantic may serialise UTC as `+00:00` rather than `Z`), assert against what `datetime.fromisoformat(FROM)` serialises to in this project's other responses — check `tests/test_api_staff.py` for how `updated_at` is compared — and keep the assertion exact.

- [ ] **Step 8: Document the route in `docs/api.md`**

In the route table (section 2), after the `DELETE /api/staff/cases/{case_id}/data/{entry_id}` row, add:

```markdown
| GET | `/api/admin/metrics` | admin_staff | System metrics over a window (sub-project 14) |
```

In the "Codes used everywhere" paragraph, change `` `403 patients_only` / `403 staff_only` (the wrong role) `` to `` `403 patients_only` / `403 staff_only` / `403 admin_only` (the wrong role) ``.

Append at the end of the file:

````markdown
## 7. Admin routes

Sub-project 14 (`docs/superpowers/specs/2026-09-24-admin-metrics-design.md`). Every route needs
an `admin_staff` token; any other token gets `403 admin_only`.

### GET /api/admin/metrics

`?from=<ISO-8601>&to=<ISO-8601>` — `from` inclusive, `to` exclusive, both with a time zone, at
most 90 days apart. A missing parameter is `422 invalid_body`; an unparsable, zone-less or
reversed window is `422 invalid_range`; a longer one is `422 range_too_large`. Every query runs
in one read-only snapshot with a 5 s statement timeout; past it the answer is
`503 metrics_unavailable`, never a partial one. `200`:

```json
{
  "window": {"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"},
  "generated_at": "2026-09-24T10:00:00.123456Z",
  "flow": {
    "opened": 3,
    "by_state": {"Completed": 3},
    "by_outcome": {"AppointmentPreparation": 2, "MedicalQuestion": 1},
    "completion": {
      "CASE_RESOLVED": {"count": 2, "p50": 2.64, "p95": 2.74, "max": 2.75},
      "HUMAN_RESOLVED_CASE": {"count": 1, "p50": 0.03, "p95": 0.03, "max": 0.03}
    }
  },
  "human_load": {
    "escalations_entered": 2,
    "decisions": {"HUMAN_APPROVED": 1, "HUMAN_RESOLVED_CASE": 1, "HUMAN_REJECTED": 0},
    "decided_by_kind": {"MedicalQuestion": 1, "RetryExhausted": 1},
    "open_by_kind": {},
    "time_to_decision": {"count": 2, "p50": 0.011, "p95": 0.012, "max": 0.012},
    "open_now": 0,
    "oldest_open_seconds": null
  },
  "tools": {
    "actions": [
      {"action": "CheckDocuments", "by_status": {"failed": 3, "succeeded": 2}, "success_rate": 0.4,
       "latency": {"count": 5, "p50": 0.008, "p95": 0.009, "max": 0.009}}
    ],
    "failure_events": {"TOOL_TRANSIENT_FAILURE": 2, "RETRY_EXHAUSTED": 1},
    "failure_reasons": [{"outcome": "ExecutionFailed", "reason": "tool:transient_failure:timeout", "count": 3}],
    "retried_calls": 2,
    "sources": {"appointments": "appointment-service", "documents": "document-service"}
  },
  "patient_sla": {"requests": 2, "met": 2, "breached": 0, "other": 0, "waiting": 0, "rate": 1.0},
  "policy": {
    "decisions": {"POLICY_ALLOWED": 11, "POLICY_DENIED": 0, "POLICY_HUMAN_REVIEW_REQUIRED": 0},
    "blocked": 0, "blocked_by_reason": {}, "blocked_by_event": {}
  }
}
```

`flow` counts the cases **opened** in the window, in their current state; every other group
counts what **happened** in it. Durations are seconds; `p50` / `p95` / `max` are `null` when
`count` is 0. `by_outcome` is one of `MedicalQuestion`, `EscalatedAtClassification`,
`AppointmentPreparation`, `Unsupported`, `NotClassified`. `open_by_kind`, `open_now` and
`oldest_open_seconds` describe the review queue **now**, whatever the window. `sources` is
`null` for each system while the Agent Orchestrator is not running. The answer carries no
`patient_id`, `case_id` or request text.
````

- [ ] **Step 9: Commit**

```bash
git add backend/hospital_agent/api/deps.py backend/hospital_agent/api/schemas.py backend/hospital_agent/api/routes_admin.py backend/hospital_agent/api/app.py backend/tests/test_api_admin.py docs/api.md
git commit -m "Add GET /api/admin/metrics behind require_admin" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Frontend data layer — types, client, labels

**Files:**
- Modify: `frontend/src/api/types.ts` (append), `frontend/src/api/client.ts` (import + append)
- Create: `frontend/src/pages/staff/metricsLabels.ts`
- Test: `frontend/src/api/client.test.ts` (append), `frontend/src/pages/staff/metricsLabels.test.ts` (create)

**Interfaces:**
- Consumes: the JSON shape in Task 7's Interfaces block.
- Produces: types `Durations`, `Metrics`, `MetricsFlow`, `MetricsHumanLoad`, `MetricsToolAction`, `MetricsFailureReason`, `MetricsTools`, `MetricsPatientSla`, `MetricsPolicy`; `api.getMetrics(from: Date, to: Date): Promise<Metrics>`; from `metricsLabels.ts`: `OUTCOME_LABELS`, `EVENT_LABELS`, `STATUS_LABELS`, `REASON_LABELS`, `SOURCE_LABELS`, `ERROR_TEXT`, `FORMAL_KINDS`, `PRESETS`, `labelOf(labels, code)`, `formatCount(n)`, `formatPercent(ratio)`, `formatSeconds(seconds)`, `durationsText(d)`, `toRows(counts, label)`, `BarRow`, `LocalRange`, `toLocalInput(date)`, `fromLocalInput(value)`, `presetRange(hours, now)`.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/api/client.test.ts`:

```ts
describe('getMetrics', () => {
  it('sends the window as ISO instants, URL-encoded', async () => {
    mockOnce(200, {})
    await api.getMetrics(new Date('2026-09-17T00:00:00Z'), new Date('2026-09-24T00:00:00Z'))
    const [url, init] = lastCall()
    expect(url).toBe(
      '/api/admin/metrics?from=2026-09-17T00%3A00%3A00.000Z&to=2026-09-24T00%3A00%3A00.000Z',
    )
    expect(init.method).toBe('GET')
  })
})
```

`frontend/src/pages/staff/metricsLabels.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import {
  OUTCOME_LABELS,
  durationsText,
  formatPercent,
  formatSeconds,
  fromLocalInput,
  labelOf,
  presetRange,
  toLocalInput,
  toRows,
} from './metricsLabels'

describe('labelOf', () => {
  it('translates a known code and falls back to the code itself', () => {
    expect(labelOf(OUTCOME_LABELS, 'MedicalQuestion')).toBe('שאלה רפואית')
    expect(labelOf(OUTCOME_LABELS, 'SomethingNew')).toBe('SomethingNew')
    expect(labelOf(OUTCOME_LABELS, null)).toBe('—')
  })
})

describe('formatting', () => {
  it('writes seconds in the largest readable unit', () => {
    expect(formatSeconds(null)).toBe('—')
    expect(formatSeconds(0.0123)).toBe('12 ms')
    expect(formatSeconds(2.64)).toBe('2.6 s')
    expect(formatSeconds(90)).toBe('1.5 min')
    expect(formatSeconds(7200)).toBe('2.0 h')
  })

  it('writes a ratio as a whole percent', () => {
    expect(formatPercent(null)).toBe('—')
    expect(formatPercent(2 / 3)).toBe('67%')
  })

  it('summarises durations, or says there were none', () => {
    expect(durationsText({ count: 0, p50: null, p95: null, max: null })).toBe('אין מדידות')
    expect(durationsText({ count: 3, p50: 4, p95: 5.8, max: 6 })).toBe('p50 4.0 s · p95 5.8 s · max 6.0 s')
  })
})

describe('toRows', () => {
  it('orders the largest first, ties by code, with each row labelled', () => {
    const rows = toRows({ Unsupported: 2, MedicalQuestion: 5, AppointmentPreparation: 2 }, (code) =>
      labelOf(OUTCOME_LABELS, code),
    )
    expect(rows).toEqual([
      { code: 'MedicalQuestion', label: 'שאלה רפואית', count: 5 },
      { code: 'AppointmentPreparation', label: 'הכנה לתור', count: 2 },
      { code: 'Unsupported', label: 'לא נתמכת', count: 2 },
    ])
  })
})

describe('ranges', () => {
  it('round-trips a Date through a datetime-local value, to the minute', () => {
    const date = new Date(2026, 8, 24, 13, 5, 42)
    expect(toLocalInput(date)).toBe('2026-09-24T13:05')
    expect(fromLocalInput('2026-09-24T13:05')?.getTime()).toBe(new Date(2026, 8, 24, 13, 5).getTime())
    expect(fromLocalInput('')).toBeNull()
    expect(fromLocalInput('not a date')).toBeNull()
  })

  it('ends a preset at the next minute, so now is inside it', () => {
    const range = presetRange(24 * 7, new Date(2026, 8, 24, 13, 5, 42))
    expect(range).toEqual({ from: '2026-09-17T13:06', to: '2026-09-24T13:06' })
    const start = fromLocalInput(range.from)!
    const end = fromLocalInput(range.to)!
    expect(end.getTime() - start.getTime()).toBe(7 * 24 * 3600_000)
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test -- src/api/client.test.ts src/pages/staff/metricsLabels.test.ts`
Expected: FAIL — `api.getMetrics is not a function`; `Failed to resolve import "./metricsLabels"`.

- [ ] **Step 3: Append the types to `types.ts`**

```ts
// ---- Admin metrics (sub-project 14) ---------------------------------------

/** Seconds; every field but `count` is null when nothing was measured. */
export interface Durations {
  count: number
  p50: number | null
  p95: number | null
  max: number | null
}

/** The cases opened in the window, in their current state. */
export interface MetricsFlow {
  opened: number
  by_state: Record<string, number>
  by_outcome: Record<string, number>
  completion: Record<string, Durations>
}

export interface MetricsHumanLoad {
  escalations_entered: number
  decisions: Record<string, number>
  decided_by_kind: Record<string, number>
  /** The queue now, whatever the window - as are `open_now` and `oldest_open_seconds`. */
  open_by_kind: Record<string, number>
  time_to_decision: Durations
  open_now: number
  oldest_open_seconds: number | null
}

export interface MetricsToolAction {
  action: string
  by_status: Record<string, number>
  success_rate: number | null
  latency: Durations
}

export interface MetricsFailureReason {
  outcome: string
  reason: string | null
  count: number
}

export interface MetricsTools {
  actions: MetricsToolAction[]
  failure_events: Record<string, number>
  failure_reasons: MetricsFailureReason[]
  retried_calls: number
  sources: Record<string, string | null>
}

export interface MetricsPatientSla {
  requests: number
  met: number
  breached: number
  other: number
  waiting: number
  rate: number | null
}

export interface MetricsPolicy {
  decisions: Record<string, number>
  blocked: number
  blocked_by_reason: Record<string, number>
  blocked_by_event: Record<string, number>
}

/** `GET /api/admin/metrics?from=&to=` → 200 (`docs/api.md` §7). */
export interface Metrics {
  window: { start: IsoDateTime; end: IsoDateTime }
  generated_at: IsoDateTime
  flow: MetricsFlow
  human_load: MetricsHumanLoad
  tools: MetricsTools
  patient_sla: MetricsPatientSla
  policy: MetricsPolicy
}
```

- [ ] **Step 4: Add `getMetrics` to `client.ts`**

Add `Metrics,` to the `import type { … } from './types'` list (alphabetically, after `Me,`). Append at the end of the file:

```ts
// ---- Admin (sub-project 14) ------------------------------------------------

/** `GET /api/admin/metrics` - admin_staff only. `from` inclusive, `to` exclusive. */
export function getMetrics(from: Date, to: Date): Promise<Metrics> {
  const query = new URLSearchParams({ from: from.toISOString(), to: to.toISOString() })
  return request<Metrics>('GET', `/admin/metrics?${query.toString()}`)
}
```

- [ ] **Step 5: Create `metricsLabels.ts`**

```ts
/**
 * Labels, number formatting and the date range of the admin metrics screen (sub-project 14,
 * design docs/superpowers/specs/2026-09-24-admin-metrics-design.md §8).
 *
 * The staff screens' rule (./labels): a label is shown next to the code it translates,
 * never instead of it, and an unknown code falls back to itself.
 */
import type { Durations } from '../../api/types'

export const OUTCOME_LABELS: Record<string, string> = {
  MedicalQuestion: 'שאלה רפואית',
  EscalatedAtClassification: 'הוסלמה בשלב הסיווג',
  AppointmentPreparation: 'הכנה לתור',
  Unsupported: 'לא נתמכת',
  NotClassified: 'טרם סווגה',
}

export const EVENT_LABELS: Record<string, string> = {
  CASE_RESOLVED: 'הושלמה אוטומטית',
  HUMAN_RESOLVED_CASE: 'נסגרה ע״י צוות',
  HUMAN_APPROVED: 'אושרה והמשיכה',
  HUMAN_REJECTED: 'נדחתה ע״י צוות',
  TOOL_TRANSIENT_FAILURE: 'כשל זמני',
  RETRY_EXHAUSTED: 'הניסיונות מוצו',
  POLICY_ALLOWED: 'המדיניות אישרה',
  POLICY_DENIED: 'המדיניות דחתה',
  POLICY_HUMAN_REVIEW_REQUIRED: 'המדיניות העבירה לאדם',
}

export const STATUS_LABELS: Record<string, string> = {
  succeeded: 'הצליחו',
  failed: 'נכשלו',
  unknown: 'תוצאה לא ידועה',
  started: 'בביצוע',
}

export const REASON_LABELS: Record<string, string> = {
  'tool:transient_failure:timeout': 'timeout',
  'tool:transient_failure:unavailable': 'השירות לא זמין',
  guard_failed: 'אין מעבר חוקי',
  invalid_escalation_reason: 'סיבת הסלמה לא תקינה',
  system_owned_event: 'אירוע מערכת ממקור חיצוני',
}

export const SOURCE_LABELS: Record<string, string> = {
  mock: 'mock',
  'appointment-service': 'שירות התורים',
  'document-service': 'שירות המסמכים',
}

/** The escalations raised by the formal layers (design §4.5 E3). */
export const FORMAL_KINDS = ['TemporalViolation', 'Z3Counterexample', 'PlanningFailed'] as const

/** What the screen says for the API's own error codes; any other code is shown as is. */
export const ERROR_TEXT: Record<string, string> = {
  admin_only: 'אין הרשאה לצפות במדדים.',
  invalid_range: 'הטווח לא תקין: תחילתו חייבת להיות לפני סופו.',
  range_too_large: 'הטווח ארוך מ־90 יום.',
  metrics_unavailable: 'חישוב המדדים ארך יותר מדי. נסו טווח קצר יותר.',
  network_error: 'אין חיבור לשרת.',
}

export function labelOf(labels: Record<string, string>, code: string | null | undefined): string {
  if (!code) return '—'
  return labels[code] ?? code
}

const COUNT = new Intl.NumberFormat('he-IL')

export function formatCount(value: number): string {
  return COUNT.format(value)
}

export function formatPercent(ratio: number | null): string {
  return ratio === null ? '—' : `${Math.round(ratio * 100)}%`
}

/** Seconds, in the largest unit that keeps the number readable. */
export function formatSeconds(seconds: number | null): string {
  if (seconds === null) return '—'
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}

export function durationsText(durations: Durations): string {
  if (durations.count === 0) return 'אין מדידות'
  return [
    `p50 ${formatSeconds(durations.p50)}`,
    `p95 ${formatSeconds(durations.p95)}`,
    `max ${formatSeconds(durations.max)}`,
  ].join(' · ')
}

export interface BarRow {
  code: string
  label: string
  count: number
  /** React key, when two rows share a code. */
  key?: string
}

/** Counts as bar rows: the largest first, ties by code. */
export function toRows(counts: Record<string, number>, label: (code: string) => string): BarRow[] {
  return Object.entries(counts)
    .map(([code, count]) => ({ code, label: label(code), count }))
    .sort((a, b) => b.count - a.count || a.code.localeCompare(b.code))
}

/** Two `<input type="datetime-local">` values, in the browser's own time zone. */
export interface LocalRange {
  from: string
  to: string
}

const pad = (value: number) => String(value).padStart(2, '0')

export function toLocalInput(date: Date): string {
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

export function fromLocalInput(value: string): Date | null {
  if (!value.trim()) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** Presets first, then the custom range (dataviz: date range first, presets before custom). */
export const PRESETS = [
  { key: '24h', label: '24 שעות', hours: 24 },
  { key: '7d', label: '7 ימים', hours: 24 * 7 },
  { key: '30d', label: '30 יום', hours: 24 * 30 },
  { key: '90d', label: '90 יום', hours: 24 * 90 },
] as const

/** The preset's range, ending at the minute after `now` so that `now` itself is inside. */
export function presetRange(hours: number, now: Date): LocalRange {
  const end = new Date(now.getTime())
  end.setSeconds(0, 0)
  end.setMinutes(end.getMinutes() + 1)
  const start = new Date(end.getTime() - hours * 3600_000)
  return { from: toLocalInput(start), to: toLocalInput(end) }
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd frontend && npm test -- src/api/client.test.ts src/pages/staff/metricsLabels.test.ts`
Expected: PASS (the client file's existing tests plus the new ones; `metricsLabels.test.ts` 8 passed).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/api/client.test.ts frontend/src/pages/staff/metricsLabels.ts frontend/src/pages/staff/metricsLabels.test.ts
git commit -m "Add the metrics types, getMetrics and the screen's labels" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The metrics page

**Files:**
- Create: `frontend/src/pages/staff/MetricsParts.tsx`, `frontend/src/pages/staff/Metrics.tsx`
- Modify: `frontend/src/styles/app.css` (append)
- Test: `frontend/src/pages/staff/MetricsParts.test.tsx`, `frontend/src/pages/staff/Metrics.test.tsx` (create)

**Interfaces:**
- Consumes: everything Task 8 produces; `Alert`, `Button` (`components/`); `detailOf`, `escalationLabel`, `formatDateTime`, `stateLabel` (`./labels`).
- Produces: `Metrics` page component (named export `Metrics`); `Tile`, `Bars`, `Meter` in `MetricsParts.tsx`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/pages/staff/MetricsParts.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Bars, Meter, Tile } from './MetricsParts'

describe('Tile', () => {
  it('shows the value above its label', () => {
    render(<Tile label="נפתחו" value="12" note="בטווח" />)
    const tile = screen.getByText('נפתחו').closest('.metrics-tile')!
    expect(tile).toHaveTextContent('12')
    expect(tile).toHaveTextContent('בטווח')
  })
})

describe('Bars', () => {
  it('writes every value as text, scales to the largest and shows the code beside its label', () => {
    const { container } = render(
      <Bars
        rows={[
          { code: 'MedicalQuestion', label: 'שאלה רפואית', count: 4 },
          { code: 'Unsupported', label: 'לא נתמכת', count: 1 },
        ]}
        empty="אין"
      />,
    )
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('שאלה רפואית')
    expect(items[0]).toHaveTextContent('MedicalQuestion')
    expect(items[0]).toHaveTextContent('4')
    const fills = container.querySelectorAll<HTMLElement>('.metrics-bar-fill')
    expect(fills[0].style.width).toBe('100%')
    expect(fills[1].style.width).toBe('25%')
  })

  it('does not repeat a code that is its own label', () => {
    render(<Bars rows={[{ code: 'guard_failed', label: 'guard_failed', count: 1 }]} empty="אין" />)
    expect(screen.getAllByText('guard_failed')).toHaveLength(1)
  })

  it('says so when there is nothing to draw', () => {
    render(<Bars rows={[]} empty="אין פניות בטווח." />)
    expect(screen.getByText('אין פניות בטווח.')).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })
})

describe('Meter', () => {
  it('fills to the ratio and names it for assistive technology', () => {
    const { container } = render(<Meter ratio={0.6} label="עמידה בזמן" />)
    expect(screen.getByRole('img', { name: 'עמידה בזמן: 60%' })).toBeInTheDocument()
    expect(container.querySelector<HTMLElement>('.metrics-meter-fill')!.style.width).toBe('60%')
  })
})
```

`frontend/src/pages/staff/Metrics.test.tsx`:

```tsx
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import type { Metrics as MetricsData } from '../../api/types'
import { Metrics } from './Metrics'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  getMetrics: vi.fn(),
}))

const NONE = { count: 0, p50: null, p95: null, max: null }

const FIXTURE: MetricsData = {
  window: { start: '2026-09-17T00:00:00Z', end: '2026-09-24T00:00:00Z' },
  generated_at: '2026-09-24T10:00:00Z',
  flow: {
    opened: 12,
    by_state: { Completed: 7, Failed: 1, AwaitingHumanReview: 2, AwaitingPatientInput: 1, Planning: 1 },
    by_outcome: { AppointmentPreparation: 8, MedicalQuestion: 3, SomethingNew: 1 },
    completion: { CASE_RESOLVED: { count: 6, p50: 2.6, p95: 3.1, max: 3.4 }, HUMAN_RESOLVED_CASE: NONE },
  },
  human_load: {
    escalations_entered: 5,
    decisions: { HUMAN_APPROVED: 1, HUMAN_RESOLVED_CASE: 1, HUMAN_REJECTED: 1 },
    decided_by_kind: { MedicalQuestion: 2, TemporalViolation: 1 },
    open_by_kind: { RetryExhausted: 2 },
    time_to_decision: { count: 3, p50: 600, p95: 1740, max: 1800 },
    open_now: 2,
    oldest_open_seconds: 7200,
  },
  tools: {
    actions: [
      {
        action: 'CheckDocuments',
        by_status: { succeeded: 2, failed: 3 },
        success_rate: 0.4,
        latency: { count: 5, p50: 0.008, p95: 0.009, max: 0.009 },
      },
    ],
    failure_events: { TOOL_TRANSIENT_FAILURE: 2, RETRY_EXHAUSTED: 1 },
    failure_reasons: [{ outcome: 'ExecutionFailed', reason: 'tool:transient_failure:timeout', count: 3 }],
    retried_calls: 2,
    sources: { appointments: 'appointment-service', documents: null },
  },
  patient_sla: { requests: 5, met: 3, breached: 2, other: 0, waiting: 0, rate: 0.6 },
  policy: {
    decisions: { POLICY_ALLOWED: 11, POLICY_DENIED: 1, POLICY_HUMAN_REVIEW_REQUIRED: 0 },
    blocked: 2,
    blocked_by_reason: { guard_failed: 2 },
    blocked_by_event: { HUMAN_APPROVED: 2 },
  },
}

function tile(label: string): HTMLElement {
  return screen.getByText(label, { selector: '.metrics-tile-k' }).closest('.metrics-tile') as HTMLElement
}

function spanOf(call: number): number {
  const [start, end] = vi.mocked(api.getMetrics).mock.calls[call]
  return end.getTime() - start.getTime()
}

beforeEach(() => {
  vi.mocked(api.getMetrics).mockReset()
})

describe('Metrics', () => {
  it('loads the last 7 days and shows every group', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(FIXTURE)
    render(<Metrics />)

    expect(await screen.findByRole('heading', { name: 'זרימת פניות' })).toBeInTheDocument()
    expect(spanOf(0)).toBe(7 * 24 * 3600_000)
    for (const heading of ['עומס על הצוות', 'כלים חיצוניים', 'SLA מטופל', 'מדיניות']) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    }
    expect(tile('נפתחו')).toHaveTextContent('12')
    expect(tile('נדחו ע״י צוות')).toHaveTextContent('1')
    expect(tile('בטיפול')).toHaveTextContent('1') // 12 - 7 - 1 - 2 - 1
    expect(tile('פתוחות עכשיו')).toHaveTextContent('2')
    expect(tile('פתוחות עכשיו')).toHaveTextContent('2.0 h')
    expect(tile('עמידה בזמן')).toHaveTextContent('60%')
    expect(tile('חסימות')).toHaveTextContent('2')
  })

  it('shows a code the screen has no label for as itself', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(FIXTURE)
    render(<Metrics />)
    expect(await screen.findByText('SomethingNew')).toBeInTheDocument()
  })

  it('lists each tool with its calls, success rate and latency', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(FIXTURE)
    render(<Metrics />)
    const row = (await screen.findByText('CheckDocuments')).closest('tr') as HTMLElement
    const cells = within(row).getAllByRole('cell')
    expect(cells.map((cell) => cell.textContent)).toEqual(['CheckDocuments', '5', '2', '3', '0', '40%', '8 ms', '9 ms', '9 ms'])
    expect(screen.getByText('שירות התורים')).toBeInTheDocument()
    expect(screen.getByText('לא דווח')).toBeInTheDocument()
  })

  it('refetches for a preset, keeping the previous numbers dimmed meanwhile', async () => {
    vi.mocked(api.getMetrics).mockResolvedValueOnce(FIXTURE).mockReturnValueOnce(new Promise(() => {}))
    const { container } = render(<Metrics />)
    await screen.findByRole('heading', { name: 'זרימת פניות' })

    await userEvent.click(screen.getByRole('button', { name: '24 שעות' }))
    expect(spanOf(1)).toBe(24 * 3600_000)
    expect(container.querySelector('.metrics-body')).toHaveClass('is-stale')
    expect(tile('נפתחו')).toHaveTextContent('12')
  })

  it('shows a custom range when submitted', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(FIXTURE)
    render(<Metrics />)
    await screen.findByRole('heading', { name: 'זרימת פניות' })

    fireEvent.change(screen.getByLabelText('מ־'), { target: { value: '2026-09-01T08:00' } })
    fireEvent.change(screen.getByLabelText('עד'), { target: { value: '2026-09-02T08:00' } })
    await userEvent.click(screen.getByRole('button', { name: 'הצג' }))

    const [start, end] = vi.mocked(api.getMetrics).mock.calls[1]
    expect(start.getTime()).toBe(new Date('2026-09-01T08:00').getTime())
    expect(end.getTime()).toBe(new Date('2026-09-02T08:00').getTime())
  })

  it('explains a refusal, shows its code and retries', async () => {
    vi.mocked(api.getMetrics).mockRejectedValueOnce(new ApiError(403, 'admin_only')).mockResolvedValueOnce(FIXTURE)
    render(<Metrics />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('אין הרשאה לצפות במדדים.')
    expect(alert).toHaveTextContent('admin_only')

    await userEvent.click(within(alert).getByRole('button', { name: 'נסה שוב' }))
    expect(await screen.findByRole('heading', { name: 'זרימת פניות' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test -- src/pages/staff/MetricsParts.test.tsx src/pages/staff/Metrics.test.tsx`
Expected: FAIL — `Failed to resolve import "./MetricsParts"` / `"./Metrics"`.

- [ ] **Step 3: Create `MetricsParts.tsx`**

```tsx
/**
 * The metrics screen's pieces (sub-project 14; dataviz): a stat tile, a one-series bar list
 * and a meter. No chart library - the project keeps no UI dependency beyond React and the
 * router.
 */
import { formatCount, formatPercent } from './metricsLabels'
import type { BarRow } from './metricsLabels'

/** Value, label, optional note. The value keeps proportional figures (not the mono `.num`). */
export function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="metrics-tile">
      <span className="metrics-tile-v">{value}</span>
      <span className="metrics-tile-k">{label}</span>
      {note && <span className="metrics-tile-note">{note}</span>}
    </div>
  )
}

/**
 * One series, so one color for every bar - never a value ramp on nominal categories. Every
 * value is written at its bar's tip, so the list is its own table view and no tooltip is
 * needed to read anything; the bar itself is decoration for assistive technology.
 */
export function Bars({ rows, empty }: { rows: BarRow[]; empty: string }) {
  if (rows.length === 0) return <p className="metrics-empty">{empty}</p>
  const top = Math.max(...rows.map((row) => row.count))
  return (
    <ul className="metrics-bars">
      {rows.map((row) => (
        <li key={row.key ?? row.code} className="metrics-bar">
          <span className="metrics-bar-k">
            {row.label}
            {row.label !== row.code && <span className="mono metrics-code">{row.code}</span>}
          </span>
          <span className="metrics-bar-lane" aria-hidden="true">
            <span className="metrics-bar-fill" style={{ width: `${top ? (row.count / top) * 100 : 0}%` }} />
          </span>
          <span className="metrics-bar-v">{formatCount(row.count)}</span>
        </li>
      ))}
    </ul>
  )
}

/** A ratio against 100%: the fill carries it, the track is a lighter step of the same ramp. */
export function Meter({ ratio, label }: { ratio: number | null; label: string }) {
  return (
    <span className="metrics-meter" role="img" aria-label={`${label}: ${formatPercent(ratio)}`}>
      <span className="metrics-meter-fill" style={{ width: `${ratio === null ? 0 : ratio * 100}%` }} />
    </span>
  )
}
```

- [ ] **Step 4: Create `Metrics.tsx`**

```tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import * as api from '../../api/client'
import type { Metrics as MetricsData } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { detailOf, escalationLabel, formatDateTime, stateLabel } from './labels'
import {
  ERROR_TEXT,
  EVENT_LABELS,
  FORMAL_KINDS,
  OUTCOME_LABELS,
  PRESETS,
  REASON_LABELS,
  SOURCE_LABELS,
  durationsText,
  formatCount,
  formatPercent,
  formatSeconds,
  fromLocalInput,
  labelOf,
  presetRange,
  toRows,
} from './metricsLabels'
import type { LocalRange } from './metricsLabels'
import { Bars, Meter, Tile } from './MetricsParts'

const event = (code: string) => labelOf(EVENT_LABELS, code)

/**
 * "מדדי מערכת" (sub-project 14, design §8): admin_staff only - the server's require_admin is
 * the gate, StaffRoutes only hides the link. One filter row scopes every group below it
 * (dataviz: date range first, presets before a custom range). A refetch keeps the previous
 * numbers on screen, dimmed, instead of flashing a loading state.
 */
export function Metrics() {
  const [range, setRange] = useState<LocalRange>(() => presetRange(24 * 7, new Date()))
  const [data, setData] = useState<MetricsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async (next: LocalRange) => {
    const start = fromLocalInput(next.from)
    const end = fromLocalInput(next.to)
    if (!start || !end) {
      setError('invalid_range')
      return
    }
    setLoading(true)
    try {
      setData(await api.getMetrics(start, end))
      setError(null)
    } catch (caught) {
      setError(detailOf(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  const initial = useRef(range)
  useEffect(() => {
    void load(initial.current)
  }, [load])

  function choosePreset(hours: number) {
    const next = presetRange(hours, new Date())
    setRange(next)
    void load(next)
  }

  return (
    <section className="staff-page metrics-page">
      <header className="page-head">
        <h1 className="page-h">מדדי מערכת</h1>
        <p className="lede">מספרים מצטברים מתוך ה־Audit. אין בהם פרטי מטופל.</p>
      </header>

      <form
        className="metrics-filters card"
        onSubmit={(submitted) => {
          submitted.preventDefault()
          void load(range)
        }}
      >
        <div className="metrics-presets" role="group" aria-label="טווחים מוכנים">
          {PRESETS.map((preset) => (
            <Button key={preset.key} variant="secondary" onClick={() => choosePreset(preset.hours)}>
              {preset.label}
            </Button>
          ))}
        </div>
        <label className="metrics-field">
          <span>מ־</span>
          <input
            type="datetime-local"
            value={range.from}
            onChange={(changed) => setRange({ ...range, from: changed.target.value })}
          />
        </label>
        <label className="metrics-field">
          <span>עד</span>
          <input
            type="datetime-local"
            value={range.to}
            onChange={(changed) => setRange({ ...range, to: changed.target.value })}
          />
        </label>
        <Button type="submit" variant="primary" busy={loading}>
          הצג
        </Button>
      </form>

      {error && (
        <Alert variant="error" title="לא הצלחנו לטעון את המדדים">
          {ERROR_TEXT[error] && <>{ERROR_TEXT[error]} </>}
          <span className="mono">{error}</span>{' '}
          <button type="button" className="linkbtn" onClick={() => void load(range)}>
            נסה שוב
          </button>
        </Alert>
      )}

      {data === null ? (
        !error && (
          <p className="page-loading" role="status">
            טוען…
          </p>
        )
      ) : (
        <div className={loading ? 'metrics-body is-stale' : 'metrics-body'} aria-busy={loading}>
          <p className="metrics-window">
            {formatDateTime(data.window.start)} – {formatDateTime(data.window.end)} · חושב ב־
            {formatDateTime(data.generated_at)}
          </p>
          <FlowGroup flow={data.flow} />
          <HumanLoadGroup load={data.human_load} />
          <ToolsGroup tools={data.tools} />
          <PatientSlaGroup sla={data.patient_sla} />
          <PolicyGroup policy={data.policy} decidedByKind={data.human_load.decided_by_kind} />
        </div>
      )}
    </section>
  )
}

function Group({ id, title, scope, children }: { id: string; title: string; scope: string; children: ReactNode }) {
  return (
    <section className="card metrics-group" aria-labelledby={id}>
      <h2 className="section-h" id={id}>
        {title}
      </h2>
      <p className="metrics-scope">{scope}</p>
      {children}
    </section>
  )
}

function FlowGroup({ flow }: { flow: MetricsData['flow'] }) {
  const state = (code: string) => flow.by_state[code] ?? 0
  const named = ['Completed', 'Failed', 'AwaitingHumanReview', 'AwaitingPatientInput']
  const inProgress = flow.opened - named.reduce((sum, code) => sum + state(code), 0)
  return (
    <Group id="metrics-flow" title="זרימת פניות" scope="הפניות שנפתחו בטווח, במצבן הנוכחי">
      <div className="metrics-tiles">
        <Tile label="נפתחו" value={formatCount(flow.opened)} />
        <Tile label="הושלמו" value={formatCount(state('Completed'))} />
        <Tile label="נדחו ע״י צוות" value={formatCount(state('Failed'))} />
        <Tile label="ממתינות לאדם" value={formatCount(state('AwaitingHumanReview'))} />
        <Tile label="ממתינות למטופל" value={formatCount(state('AwaitingPatientInput'))} />
        <Tile label="בטיפול" value={formatCount(inProgress)} />
      </div>
      <h3 className="metrics-sub">תוצאת הסיווג</h3>
      <Bars rows={toRows(flow.by_outcome, (code) => labelOf(OUTCOME_LABELS, code))} empty="אין פניות בטווח." />
      <h3 className="metrics-sub">לפי מצב</h3>
      <Bars rows={toRows(flow.by_state, stateLabel)} empty="אין פניות בטווח." />
      <h3 className="metrics-sub">זמן עד השלמה</h3>
      <dl className="metrics-facts">
        {Object.entries(flow.completion).map(([code, durations]) => (
          <div key={code} className="metrics-fact">
            <dt>
              {event(code)} <span className="mono metrics-code">{code}</span>
            </dt>
            <dd>{durationsText(durations)}</dd>
          </div>
        ))}
      </dl>
    </Group>
  )
}

function HumanLoadGroup({ load }: { load: MetricsData['human_load'] }) {
  return (
    <Group id="metrics-human" title="עומס על הצוות" scope="אירועים שקרו בטווח. התור הפתוח — נכון לעכשיו">
      <div className="metrics-tiles">
        <Tile label="כניסות לתור ההסלמות" value={formatCount(load.escalations_entered)} />
        <Tile
          label="פתוחות עכשיו"
          value={formatCount(load.open_now)}
          note={load.oldest_open_seconds === null ? undefined : `הוותיקה ממתינה ${formatSeconds(load.oldest_open_seconds)}`}
        />
        <Tile
          label="זמן עד הכרעה (p50)"
          value={formatSeconds(load.time_to_decision.p50)}
          note={durationsText(load.time_to_decision)}
        />
      </div>
      <h3 className="metrics-sub">הכרעות</h3>
      <Bars rows={toRows(load.decisions, event)} empty="אין הכרעות בטווח." />
      <h3 className="metrics-sub">הוכרעו בטווח, לפי סוג הסלמה</h3>
      <Bars rows={toRows(load.decided_by_kind, escalationLabel)} empty="אין הכרעות בטווח." />
      <h3 className="metrics-sub">פתוחות עכשיו, לפי סוג הסלמה</h3>
      <Bars rows={toRows(load.open_by_kind, escalationLabel)} empty="התור ריק." />
    </Group>
  )
}

function ToolsGroup({ tools }: { tools: MetricsData['tools'] }) {
  return (
    <Group id="metrics-tools" title="כלים חיצוניים" scope="קריאות שהתחילו בטווח">
      <div className="metrics-tiles">
        <Tile label="כשלים זמניים" value={formatCount(tools.failure_events.TOOL_TRANSIENT_FAILURE ?? 0)} />
        <Tile label="ניסיונות שמוצו" value={formatCount(tools.failure_events.RETRY_EXHAUSTED ?? 0)} />
        <Tile label="קריאות חוזרות" value={formatCount(tools.retried_calls)} />
      </div>
      {tools.actions.length === 0 ? (
        <p className="metrics-empty">אין קריאות בטווח.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table metrics-table">
            <thead>
              <tr>
                <th scope="col">פעולה</th>
                <th scope="col">קריאות</th>
                <th scope="col">הצליחו</th>
                <th scope="col">נכשלו</th>
                <th scope="col">לא ידוע</th>
                <th scope="col">אחוז הצלחה</th>
                <th scope="col">p50</th>
                <th scope="col">p95</th>
                <th scope="col">max</th>
              </tr>
            </thead>
            <tbody>
              {tools.actions.map((action) => {
                const calls = Object.values(action.by_status).reduce((sum, count) => sum + count, 0)
                return (
                  <tr key={action.action}>
                    <td className="mono">{action.action}</td>
                    <td>{formatCount(calls)}</td>
                    <td>{formatCount(action.by_status.succeeded ?? 0)}</td>
                    <td>{formatCount(action.by_status.failed ?? 0)}</td>
                    <td>{formatCount(action.by_status.unknown ?? 0)}</td>
                    <td className="metrics-rate">
                      <Meter ratio={action.success_rate} label={`אחוז הצלחה ${action.action}`} />
                      {formatPercent(action.success_rate)}
                    </td>
                    <td>{formatSeconds(action.latency.p50)}</td>
                    <td>{formatSeconds(action.latency.p95)}</td>
                    <td>{formatSeconds(action.latency.max)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <h3 className="metrics-sub">סיבות כשל</h3>
      <Bars
        rows={tools.failure_reasons.map((failure) => ({
          key: `${failure.outcome}|${failure.reason ?? ''}`,
          code: failure.reason ?? failure.outcome,
          label:
            labelOf(REASON_LABELS, failure.reason ?? failure.outcome) +
            (failure.outcome === 'ExecutionUnknown' ? ' (תוצאה לא ידועה)' : ''),
          count: failure.count,
        }))}
        empty="אין כשלים בטווח."
      />
      <h3 className="metrics-sub">מקורות מוגדרים</h3>
      <dl className="metrics-facts">
        {Object.entries(tools.sources).map(([system, source]) => (
          <div key={system} className="metrics-fact">
            <dt className="mono">{system}</dt>
            <dd>{source === null ? 'לא דווח' : labelOf(SOURCE_LABELS, source)}</dd>
          </div>
        ))}
      </dl>
    </Group>
  )
}

function PatientSlaGroup({ sla }: { sla: MetricsData['patient_sla'] }) {
  return (
    <Group id="metrics-sla" title="SLA מטופל" scope="בקשות מסמך שנשלחו בטווח, לפי מה שסיים כל המתנה">
      <div className="metrics-tiles">
        <Tile label="עמידה בזמן" value={formatPercent(sla.rate)} note="עמדו / (עמדו + חרגו)" />
        <Tile label="בקשות מסמך" value={formatCount(sla.requests)} />
        <Tile label="עמדו בזמן" value={formatCount(sla.met)} />
        <Tile label="חרגו" value={formatCount(sla.breached)} />
        <Tile label="יצאו אחרת" value={formatCount(sla.other)} />
        <Tile label="ממתינות" value={formatCount(sla.waiting)} />
      </div>
      <Meter ratio={sla.rate} label="עמידה בזמן" />
    </Group>
  )
}

function PolicyGroup({ policy, decidedByKind }: { policy: MetricsData['policy']; decidedByKind: Record<string, number> }) {
  const formal = Object.fromEntries(FORMAL_KINDS.map((kind) => [kind, decidedByKind[kind] ?? 0]))
  return (
    <Group id="metrics-policy" title="מדיניות" scope="אירועים שקרו בטווח">
      <div className="metrics-tiles">
        <Tile label="חסימות" value={formatCount(policy.blocked)} />
      </div>
      <h3 className="metrics-sub">החלטות מדיניות</h3>
      <Bars rows={toRows(policy.decisions, event)} empty="אין החלטות בטווח." />
      <h3 className="metrics-sub">חסימות לפי סיבה</h3>
      <Bars rows={toRows(policy.blocked_by_reason, (code) => labelOf(REASON_LABELS, code))} empty="אין חסימות בטווח." />
      <h3 className="metrics-sub">חסימות לפי אירוע</h3>
      <Bars rows={toRows(policy.blocked_by_event, (code) => code)} empty="אין חסימות בטווח." />
      <h3 className="metrics-sub">הסלמות של השכבות הפורמליות שהוכרעו בטווח</h3>
      <Bars rows={toRows(formal, escalationLabel)} empty="—" />
    </Group>
  )
}
```


- [ ] **Step 5: Append the styles to `app.css`**

```css
/* --- Sub-project 14: the admin metrics screen (dataviz) ---------------------------------- */
/* The bar/meter fill is the brand ramp's 500 step in both themes. validate_palette.js: light
   vs #ffffff all PASS; dark vs #14212b all PASS. The dark theme's own --brand-500 (#2fabd3,
   OKLCH L 0.693) is above the dark lightness band (0.48-0.67), and #0e6f8e is below 3:1. */
.metrics-page {
  --metrics-fill: #1189ae;
}

.metrics-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--space-3);
  padding: var(--space-4);
}

.metrics-presets {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-inline-end: auto;
}

.metrics-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: 13px;
  color: var(--ink-500);
}

.metrics-field input {
  font: inherit;
  font-size: 14px;
  color: var(--ink-900);
  background: var(--surface-000);
  border: 1px solid var(--line-300);
  border-radius: 8px;
  padding: var(--space-2) var(--space-3);
}

.metrics-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  transition: opacity 120ms ease;
}

.metrics-body.is-stale {
  opacity: 0.55;
}

.metrics-window,
.metrics-scope,
.metrics-empty {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--ink-500);
}

.metrics-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
}

.metrics-sub {
  margin: var(--space-2) 0 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-700);
}

.metrics-tiles {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--space-3);
}

.metrics-tile {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-3) var(--space-4);
  background: var(--surface-100);
  border-radius: 10px;
}

.metrics-tile-v {
  font-family: var(--font-sans);
  font-size: 28px;
  line-height: 34px;
  font-weight: 600;
  color: var(--ink-900);
}

.metrics-tile-k {
  font-size: 13px;
  color: var(--ink-700);
}

.metrics-tile-note {
  font-size: 12px;
  color: var(--ink-500);
}

.metrics-bars {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.metrics-bar {
  display: grid;
  grid-template-columns: minmax(140px, 32%) 1fr auto;
  align-items: center;
  gap: var(--space-3);
  padding: 2px var(--space-2);
  border-radius: 6px;
}

.metrics-bar:hover {
  background: var(--surface-100);
}

.metrics-bar-k {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: 14px;
  color: var(--ink-900);
}

.metrics-code {
  font-size: 12px;
  color: var(--ink-500);
}

.metrics-bar-lane {
  display: block;
  min-inline-size: 0;
}

/* <= 24px thick, square at the baseline (inline-start), 4px rounded data-end (inline-end).
   The width is set inline as a percentage; in RTL a block starts at the right, the baseline. */
.metrics-bar-fill {
  display: block;
  block-size: 12px;
  min-inline-size: 2px;
  background: var(--metrics-fill);
  border-start-end-radius: 4px;
  border-end-end-radius: 4px;
}

.metrics-bar-v {
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--ink-900);
}

.metrics-meter {
  display: inline-block;
  inline-size: 72px;
  block-size: 8px;
  vertical-align: middle;
  background: var(--brand-100);
  border-radius: 4px;
  overflow: hidden;
}

.metrics-meter-fill {
  display: block;
  block-size: 100%;
  background: var(--metrics-fill);
}

.metrics-rate {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.metrics-table td {
  font-variant-numeric: tabular-nums;
}

.metrics-facts {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.metrics-fact {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: 14px;
}

.metrics-fact dt {
  color: var(--ink-700);
}

.metrics-fact dd {
  margin: 0;
  color: var(--ink-900);
}

@media (max-width: 640px) {
  .metrics-bar {
    grid-template-columns: 1fr auto;
  }

  .metrics-bar-lane {
    grid-column: 1 / -1;
    grid-row: 2;
  }
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd frontend && npm test -- src/pages/staff/MetricsParts.test.tsx src/pages/staff/Metrics.test.tsx src/styles/app.css.test.ts`
Expected: PASS — MetricsParts 5, Metrics 6, app.css 3 (no duplicate selector, braces balanced).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/staff/MetricsParts.tsx frontend/src/pages/staff/MetricsParts.test.tsx frontend/src/pages/staff/Metrics.tsx frontend/src/pages/staff/Metrics.test.tsx frontend/src/styles/app.css
git commit -m "Add the admin metrics page: tiles, one-series bars and the tools table" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The admin-only route and nav item

**Files:**
- Modify: `frontend/src/pages/staff/StaffRoutes.tsx`
- Test: `frontend/src/pages/staff/StaffRoutes.test.tsx` (create)

**Interfaces:**
- Consumes: `Metrics` (Task 9), `useAuth` (`auth/AuthContext`), `renderWithAuth` / `makeUser` (`test/helpers`).

- [ ] **Step 1: Write the failing tests**

`frontend/src/pages/staff/StaffRoutes.test.tsx`:

```tsx
import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { makeUser, renderWithAuth } from '../../test/helpers'
import { StaffRoutes } from './StaffRoutes'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  getMetrics: vi.fn(),
  listReviews: vi.fn(),
}))

const staffArea = (
  <Routes>
    <Route path="/staff/*" element={<StaffRoutes />} />
  </Routes>
)

beforeEach(() => {
  vi.mocked(api.listReviews).mockResolvedValue([])
  vi.mocked(api.getMetrics).mockReturnValue(new Promise(() => {}))
})

describe('StaffRoutes', () => {
  it('gives admin_staff the metrics link and page', async () => {
    renderWithAuth(staffArea, { user: makeUser('admin_staff'), route: '/staff/metrics' })
    expect(screen.getByRole('link', { name: 'מדדי מערכת' })).toHaveAttribute('href', '/staff/metrics')
    expect(await screen.findByRole('heading', { name: 'מדדי מערכת', level: 1 })).toBeInTheDocument()
    expect(api.getMetrics).toHaveBeenCalledTimes(1)
  })

  it('hides the link from clinical_staff and sends the address back to the queue', async () => {
    renderWithAuth(staffArea, { user: makeUser('clinical_staff'), route: '/staff/metrics' })
    expect(screen.queryByRole('link', { name: 'מדדי מערכת' })).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'תור הסלמות' })).toBeInTheDocument()
    expect(api.getMetrics).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test -- src/pages/staff/StaffRoutes.test.tsx`
Expected: FAIL — no link named `מדדי מערכת`.

- [ ] **Step 3: Update `StaffRoutes.tsx`**

Replace the file with:

```tsx
import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import { AppShell } from '../../components/AppShell'
import { CaseMonitor } from './CaseMonitor'
import { Metrics } from './Metrics'
import { ReviewCase } from './ReviewCase'
import { ReviewQueue } from './ReviewQueue'

/**
 * The staff area (design §5): the review queue, one case in review, the Case Monitor and -
 * for admin_staff only (sub-project 14) - the system metrics. Hiding the link is a
 * convenience; the server's require_admin is the gate.
 */
export function StaffRoutes() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin_staff'
  return (
    <AppShell
      nav={[
        { to: '/staff', label: 'תור הסלמות', end: true },
        { to: '/staff/monitor', label: 'כל הפניות' },
        ...(isAdmin ? [{ to: '/staff/metrics', label: 'מדדי מערכת' }] : []),
      ]}
    >
      <Routes>
        <Route index element={<ReviewQueue />} />
        <Route path="cases/:caseId" element={<ReviewCase />} />
        <Route path="monitor" element={<CaseMonitor />} />
        <Route path="metrics" element={isAdmin ? <Metrics /> : <Navigate to="/staff" replace />} />
        <Route path="*" element={<Navigate to="/staff" replace />} />
      </Routes>
    </AppShell>
  )
}
```

- [ ] **Step 4: Run the whole frontend suite and the build**

Run: `cd frontend && npm test && npm run build`
Expected: every test passes (173 before + the new ones), and `tsc -b` + `vite build` finish without errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/staff/StaffRoutes.tsx frontend/src/pages/staff/StaffRoutes.test.tsx
git commit -m "Route admin_staff to the metrics screen from the staff nav" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Whole-system verification and documentation

**Files:**
- Modify: `CLAUDE.md` (project status paragraph)

- [ ] **Step 1: Run the full backend suite in the isolated stack**

Run: `$DC run --rm backend pytest -q`
Expected: all tests pass. Report the exact count.

- [ ] **Step 2: Prove the formal layers did not move**

Run:
```bash
$DC run --rm backend python -m obs.golden
$DC run --rm backend python -m hospital_agent.policy.consistency
$DC run --rm backend pytest tests/test_fsm.py::test_table_matches_spec_3_row_by_row -v
```
Expected: `final: Completed   audit rows: 35`, `4`, `54`; `7 abstract properties passed (9 UNSAT queries)`; 1 passed.

- [ ] **Step 3: Look at it (dataviz step 7)**

`obs.golden` just filled `hospital_test`. Bring up an isolated UI on a free host port, pointed at that data. Create `C:/Users/DANIEL~1.MAM/AppData/Local/Temp/claude/C--Apps------------AI/4ba1785e-5a6d-42d4-88ab-e01babb23dab/scratchpad/ui.proto.yml`:

```yaml
# Visual check only: the isolated backend reads hospital_test (the golden traces' rows), and
# the UI is published on 5374 so the user's 5273 stays untouched.
services:
  backend:
    environment:
      DATABASE_URL: postgresql+psycopg://hospital_app:hospital_app_dev@db:5432/hospital_test
  frontend:
    ports: !override
      - "127.0.0.1:5374:5173"
```

Run: `$DC -f C:/Users/DANIEL~1.MAM/AppData/Local/Temp/claude/C--Apps------------AI/4ba1785e-5a6d-42d4-88ab-e01babb23dab/scratchpad/ui.proto.yml up -d --build frontend` (it starts `db` and `backend` too). Open `http://localhost:5374/staff/login` in the browser pane, log in as `admin_coordinator` / `demo`, open "מדדי מערכת", choose "90 יום". Screenshot in light and dark (the theme toggle), and at 375 px width. Check: no label collision or clipped text; bars grow from the right (RTL baseline) with the rounded end on the left; every value is readable; the tools table fits or scrolls inside `.table-wrap`; the numbers match the golden traces (3 cases, 2 escalations, CheckDocuments 3 failed / 2 succeeded). Fix anything wrong in `app.css` / `Metrics.tsx`, rerun `npm test`, and commit the fix separately. Then log in as `coordinator_nurse` and confirm the link is absent. Finally: `$DC -f C:/Users/DANIEL~1.MAM/AppData/Local/Temp/claude/C--Apps------------AI/4ba1785e-5a6d-42d4-88ab-e01babb23dab/scratchpad/ui.proto.yml down`.

- [ ] **Step 4: Update `CLAUDE.md`**

In the *Project status* paragraph, after the sentence that ends `… unset, `CheckDocuments` stays on the mock and the patient keeps the text upload.`, insert:

```markdown
Sub-project 14 (`docs/superpowers/specs/2026-09-24-admin-metrics-design.md`, `docs/spec_corrections.md` row 82) is the owner's admin metrics screen: `GET /api/admin/metrics?from=&to=` (`admin_staff` only, `require_admin`; at most 90 days) aggregates the existing `audit_log`, `executions`, `cases` and `approvals` tables read-only in one `REPEATABLE READ` snapshot (`hospital_agent/metrics.py`), and `/staff/metrics` shows it; migration 0005 adds three indexes and is its only write.
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "Record sub-project 14 in the project status" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Clean up the isolated stack**

Run: `$DC down -v` — removes only the `metrics-proto` project's containers, network and volume (all created by this work). The user's `hospital-agent` project is untouched.
