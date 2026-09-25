# Staff Requests to the Patient (Sub-project 15) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From the review screen a staff member can ask the patient a clarifying question, ask for a specific document, or close / reject with a closing message; the patient answers from their screen; and a case a person has written to never goes back to the agent.

**Architecture:** One new state `AwaitingPatientReply` and two new external events (`PATIENT_REPLY_REQUESTED`, `PATIENT_REPLY_SUBMITTED`), added as a marked extension beside the spec's closed lists (`naming.EXTENSION_*`, `fsm.EXTENSION_TRANSITIONS`). "Never back to the agent" is enforced by a tightened `WorkflowDecisionValid`, a new past-time rule T13, and the structure of the rows. Staff messages are fixed templates (any staff) or clinical free text bound by a `ContentApproval`; the patient sees a message only when it passed one of the two.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 + psycopg 3, PostgreSQL 16, Alembic, pytest; React 18, TypeScript 5.9, Vitest 2 + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-25-patient-requests-design.md` (binding; read it first). `docs/spec_corrections.md` rows 83–88.

## Global Constraints

- **The spec's lists stay exactly as they are:** `fsm.TRANSITIONS` keeps its 41 rows; `docs/spec/` is never edited; the spec tests compare everything outside `EXTENSION_STATES` / `EXTENSION_EVENTS` exactly as before. New guards are snake_case (the PascalCase guard test compares against the spec).
- **Do not modify:** `policy/policy.rego`, `rules.pl`, `flows.dl`, `opa_runner.py`, `prolog.py`, `datalog.py`, `consistency.py`, anything under `llm/`, `obs/golden.py`, `_clinical_answer_approval`, `HumanReviewService.answer()`. T1–T12 are untouched; T13 is appended.
- **No row of the extension sets an escalation kind.** A case returns to review with the escalation it came with.
- **No new dependencies** (backend `pyproject.toml`, frontend `package.json`).
- **Branch** `feature/patient-requests` (checked out). One commit per task, each message ending with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. **Never push.**
- **Backend tests run only in the isolated compose project** — never the user's stack, never RDS (`.env` points at RDS):
  ```bash
  cd "C:/Apps/פרויקט גמר AI/hospital-agent"
  export MSYS_NO_PATHCONV=1
  DC="docker compose -p requests-proto -f docker-compose.yml -f C:/Users/DANIEL~1.MAM/AppData/Local/Temp/claude/C--Apps------------AI/4ba1785e-5a6d-42d4-88ab-e01babb23dab/scratchpad/compose.proto.yml"
  $DC run --rm backend pytest <args>
  ```
  The override pins the database URLs and passwords to the project's own `db` container and removes host ports. Baseline on this branch: backend 918 passed / 1 skipped; frontend 27 files / 201 tests.
- **Frontend** commands run outside Docker from `frontend/`: `npm test`, `npm run build`.
- **Privacy (§12.3):** the patient never sees an escalation kind, a reason, a code or an Audit row; the application log never carries a message, a reply or a `patient_id`.
- **Staff label rule** (CLAUDE.md): a Hebrew label beside the code, never instead of it; unknown codes fall back to themselves; Latin runs in Hebrew text inside `.mono`. **Logical CSS properties only.**

---

### Task 1: Names, schema and the patient's new status

**Files:**
- Modify: `backend/hospital_agent/naming.py`, `backend/hospital_agent/data_log.py`, `backend/hospital_agent/case.py`, `backend/hospital_agent/repository.py`, `backend/hospital_agent/db.py`, `backend/hospital_agent/session.py`
- Create: `backend/alembic/versions/0006_patient_requests.py`
- Modify tests: `backend/tests/test_naming.py`, `backend/tests/test_session.py`
- Create tests: `backend/tests/test_extension.py`, `backend/tests/test_migration_0006.py`

**Interfaces:**
- Produces: `State.AWAITING_PATIENT_REPLY`, `Event.PATIENT_REPLY_REQUESTED`, `Event.PATIENT_REPLY_SUBMITTED`, `naming.EXTENSION_STATES`, `naming.EXTENSION_EVENTS`; `DataKind.STAFF_MESSAGE` (`"staff_message"`), `DataKind.PATIENT_REPLY` (`"patient_reply"`); `CaseRecord.human_engaged: bool = False`, `reply_kind: str | None = None`, `requested_document: str | None = None`; the patient status `"needs_reply"`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_extension.py`:

```python
"""Sub-project 15's extension of the spec (docs/spec_corrections.md row 83): pinned here, so the
spec's own lists (test_naming, test_fsm) can keep comparing everything outside it exactly."""
from hospital_agent.naming import (
    EVENT_OWNER,
    EXTENSION_EVENTS,
    EXTENSION_STATES,
    EXTERNAL_EVENTS,
    TERMINAL_STATES,
    Event,
    State,
)


def test_the_extension_is_one_state_and_two_external_events():
    assert EXTENSION_STATES == {State.AWAITING_PATIENT_REPLY}
    assert EXTENSION_EVENTS == {Event.PATIENT_REPLY_REQUESTED, Event.PATIENT_REPLY_SUBMITTED}
    assert EXTENSION_EVENTS <= EXTERNAL_EVENTS
    assert not EXTENSION_EVENTS & set(EVENT_OWNER)
    assert not EXTENSION_STATES & TERMINAL_STATES
```

`backend/tests/test_migration_0006.py`:

```python
"""Migration 0006 (sub-project 15, design §11): three cases columns and two widened CHECKs."""
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

COLUMNS = {"human_engaged", "reply_kind", "requested_document"}


def _case_columns(owner_engine) -> set[str]:
    with owner_engine.connect() as conn:
        return set(conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'cases'")).scalars())


def _check(owner_engine, name: str) -> str:
    with owner_engine.connect() as conn:
        return conn.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"), {"n": name}).scalar_one()


def test_0006_adds_the_columns_and_widens_both_checks(migrated, owner_engine):
    assert COLUMNS <= _case_columns(owner_engine)
    assert "'request'" in _check(owner_engine, "ck_approvals_decision")
    kinds = _check(owner_engine, "ck_data_log_kind")
    assert "'staff_message'" in kinds and "'patient_reply'" in kinds


def test_0006_downgrades_cleanly_and_upgrades_again(migrated, owner_engine, alembic_config):
    try:
        command.downgrade(alembic_config, "0005")
        assert not COLUMNS & _case_columns(owner_engine)
        assert "'request'" not in _check(owner_engine, "ck_approvals_decision")
    finally:
        command.upgrade(alembic_config, "head")
    assert COLUMNS <= _case_columns(owner_engine)


def test_an_unknown_data_kind_is_still_refused(app_engine):
    with pytest.raises(IntegrityError), app_engine.begin() as conn:
        conn.execute(text("INSERT INTO cases (case_id, patient_id, state, state_version, identity_verified,"
                          " retry_cycle, attempt_count, held_documents, created_at, updated_at)"
                          " VALUES ('C-1', 'P-10041', 'Received', 1, true, 0, 0, '[]', now(), now())"))
        conn.execute(text("INSERT INTO data_log (entry_id, case_id, patient_id, kind, content, content_hash,"
                          " created_at) VALUES ('D-1', 'C-1', 'P-10041', 'gossip', 'x', 'h', now())"))
```

- [ ] **Step 2: Run them to see them fail**

Run: `$DC run --rm backend pytest tests/test_extension.py tests/test_migration_0006.py -v`
Expected: FAIL — `ImportError: cannot import name 'EXTENSION_EVENTS'`; the migration tests fail on the missing columns.

- [ ] **Step 3: Add the names**

In `backend/hospital_agent/naming.py`, after `FAILED = "Failed"` in `class State`:

```python
    # Sub-project 15's extension (docs/spec_corrections.md row 83) - not a §2.1 state.
    AWAITING_PATIENT_REPLY = "AwaitingPatientReply"
```

After `AUDIT_RECORDED = "AUDIT_RECORDED"` in `class Event`:

```python
    # Sub-project 15's extension (row 83) - not §2.2 events. Both are external (§13.2 has no
    # owner for them): a staff member's request, and the patient's reply through the Session Service.
    PATIENT_REPLY_REQUESTED = "PATIENT_REPLY_REQUESTED"
    PATIENT_REPLY_SUBMITTED = "PATIENT_REPLY_SUBMITTED"
```

After the `EXTERNAL_EVENTS = …` line:

```python
# Sub-project 15 (docs/spec_corrections.md row 83): the owner's extension of §2. The members join
# the same enums, so every component handles them like any other, and are listed here so the
# spec's own lists (tests/test_naming.py) are compared without them.
EXTENSION_STATES = frozenset({State.AWAITING_PATIENT_REPLY})
EXTENSION_EVENTS = frozenset({Event.PATIENT_REPLY_REQUESTED, Event.PATIENT_REPLY_SUBMITTED})
```

In `backend/hospital_agent/data_log.py`, `class DataKind`, after `OUTGOING_MESSAGE = "outgoing_message"`:

```python
    STAFF_MESSAGE = "staff_message"  # sub-project 15: a request or closing message a staff member sent
    PATIENT_REPLY = "patient_reply"  # sub-project 15: the patient's answer - never read by the Classifier
```

- [ ] **Step 4: Add the columns**

`backend/alembic/versions/0006_patient_requests.py`:

```python
"""Staff requests to the patient (sub-project 15, design §11).

Three cases columns - human_engaged (set once a staff member writes to the patient, never
cleared: the case never goes back to the agent), reply_kind and requested_document (what the
open request asks for) - and two CHECK constraints widened, without which the database itself
refuses the new writes: a WorkflowDecision may now be a 'request', and the Data Log holds
staff messages and patient replies. Additive only.

Revision ID: 0006
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

DECISIONS = "decision IN ('approve', 'reject', 'resolve')"
KINDS = "kind IN ('request_text', 'uploaded_document', 'instructions', 'outgoing_message')"


def upgrade() -> None:
    op.add_column("cases", sa.Column("human_engaged", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("cases", sa.Column("reply_kind", sa.Text))
    op.add_column("cases", sa.Column("requested_document", sa.Text))
    op.drop_constraint("ck_approvals_decision", "approvals", type_="check")
    op.create_check_constraint("ck_approvals_decision", "approvals",
                               "decision IN ('approve', 'reject', 'resolve', 'request')")
    op.drop_constraint("ck_data_log_kind", "data_log", type_="check")
    op.create_check_constraint(
        "ck_data_log_kind", "data_log",
        "kind IN ('request_text', 'uploaded_document', 'instructions', 'outgoing_message', "
        "'staff_message', 'patient_reply')")


def downgrade() -> None:
    op.drop_constraint("ck_data_log_kind", "data_log", type_="check")
    op.create_check_constraint("ck_data_log_kind", "data_log", KINDS)
    op.drop_constraint("ck_approvals_decision", "approvals", type_="check")
    op.create_check_constraint("ck_approvals_decision", "approvals", DECISIONS)
    op.drop_column("cases", "requested_document")
    op.drop_column("cases", "reply_kind")
    op.drop_column("cases", "human_engaged")
```

In `backend/hospital_agent/db.py`, table `cases`, after `Column("appointment_at", …),  # migration 0002`:

```python
    Column("human_engaged", Boolean, nullable=False),  # migration 0006
    Column("reply_kind", Text),  # migration 0006
    Column("requested_document", Text),  # migration 0006
```

In `backend/hospital_agent/case.py`, `CaseRecord`, after `appointment_at`:

```python
    # Sub-project 15 (design §5.2): set by PATIENT_REPLY_REQUESTED. human_engaged is never cleared -
    # a person has written to the patient, and the case never goes back to the agent.
    human_engaged: bool = False
    reply_kind: str | None = None  # "question" | "document" while AwaitingPatientReply
    requested_document: str | None = None  # the catalog type a document request asks for
```

In `backend/hospital_agent/repository.py`: in `_case_from_row`, after `appointment_at=row["appointment_at"],` add

```python
        human_engaged=row["human_engaged"],
        reply_kind=row["reply_kind"],
        requested_document=row["requested_document"],
```

and in `_case_values`, after `"appointment_at": case.appointment_at,` add

```python
        "human_engaged": case.human_engaged,
        "reply_kind": case.reply_kind,
        "requested_document": case.requested_document,
```

- [ ] **Step 5: The patient's status and the spec tests**

In `backend/hospital_agent/session.py`, `_STATUS`, after `State.FAILED: "closed",`:

```python
    State.AWAITING_PATIENT_REPLY: "needs_reply",  # sub-project 15
```

In `backend/tests/test_session.py`, the local `STATUS` dict, after `State.FAILED: "closed",`:

```python
    State.AWAITING_PATIENT_REPLY: "needs_reply",
```

In `backend/tests/test_naming.py`: add `EXTENSION_EVENTS,` and `EXTENSION_STATES,` to the `from hospital_agent.naming import (` list (alphabetically, before `EXTERNAL_EVENTS,`); then replace

```python
    assert [s.value for s in State] == [row[0] for row in rows]
    assert len(State) == 12
```
with
```python
    spec = [s for s in State if s not in EXTENSION_STATES]  # row 83: the extension is pinned in test_extension
    assert [s.value for s in spec] == [row[0] for row in rows]
    assert len(spec) == 12
```

replace

```python
    assert [e.value for e in Event] == [row[1] for row in rows]
    assert len(Event) == 26
```
with
```python
    spec = [e for e in Event if e not in EXTENSION_EVENTS]
    assert [e.value for e in spec] == [row[1] for row in rows]
    assert len(spec) == 26
```

and in `test_external_events_are_the_patient_and_the_three_human_decisions`, change the closing `}` of the literal set to `} | EXTENSION_EVENTS`.

- [ ] **Step 6: Run the tests**

Run: `$DC run --rm backend pytest tests/test_extension.py tests/test_migration_0006.py tests/test_naming.py tests/test_session.py tests/test_schema.py -v`
Expected: all pass. Then the whole suite once: `$DC run --rm backend pytest -q` — 918 + 4 new, all passing.

- [ ] **Step 7: Commit**

```bash
git add backend/hospital_agent/naming.py backend/hospital_agent/data_log.py backend/hospital_agent/case.py backend/hospital_agent/repository.py backend/hospital_agent/db.py backend/hospital_agent/session.py backend/alembic/versions/0006_patient_requests.py backend/tests/test_extension.py backend/tests/test_migration_0006.py backend/tests/test_naming.py backend/tests/test_session.py
git commit -m "Add the patient-reply state, its two events and migration 0006 as a marked extension" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The extension rows and their guards

**Files:**
- Modify: `backend/hospital_agent/fsm.py`, `backend/hospital_agent/guards.py`, `backend/hospital_agent/repository.py`
- Modify test: `backend/tests/test_extension.py` (append)
- Create test: `backend/tests/test_patient_request_fsm.py`

**Interfaces:**
- Consumes: Task 1's names and columns.
- Produces: `fsm.EXTENSION_TRANSITIONS`, `Effect.RECORD_REPLY_REQUEST`, `Effect.CLEAR_REPLY_REQUEST`; guards `reply_request_valid`, `patient_reply_valid`; `guards.HUMAN_ENGAGED = "human_engaged"`, `guards.REPLY_KINDS`, `guards.MAX_REPLY_WINDOW = timedelta(days=7)`; `DECISION_FOR_EVENT[PATIENT_REPLY_REQUESTED] = "request"`. PATIENT_REPLY_REQUESTED payload: `approval_id`, `reply_kind`, `requested_document` (None for a question), `content_hash`, optional `message_approval_id` (Task 4). PATIENT_REPLY_SUBMITTED payload: `reply_kind`, `content_hash`, and `document_type` for a document.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_extension.py`:

```python
from hospital_agent.fsm import EXTENSION_TRANSITIONS, TRANSITIONS


def test_the_extension_rows_stay_with_people():
    assert len(TRANSITIONS) == 41
    assert {(t.source, t.event, t.target) for t in EXTENSION_TRANSITIONS} == {
        (State.AWAITING_HUMAN_REVIEW, Event.PATIENT_REPLY_REQUESTED, State.AWAITING_PATIENT_REPLY),
        (State.AWAITING_PATIENT_REPLY, Event.PATIENT_REPLY_SUBMITTED, State.AWAITING_HUMAN_REVIEW),
        (State.AWAITING_PATIENT_REPLY, Event.TIMEOUT_EXPIRED, State.AWAITING_HUMAN_REVIEW),
    }
    # design §5.2: no extension row sets an escalation kind, and none leads to the agent
    assert all(t.escalation is None for t in EXTENSION_TRANSITIONS)
    spec_rows_from_reply = [t for t in TRANSITIONS if t.source is State.AWAITING_PATIENT_REPLY]
    assert spec_rows_from_reply == []
```

`backend/tests/test_patient_request_fsm.py`:

```python
"""The extension rows (sub-project 15, design §5): a staff request, the patient's reply, the
deadline, and the case never going back to the agent - through the real State Manager."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from hospital_agent.execution.sla import SlaWorker
from hospital_agent.naming import Component, EscalationKind, Event, State
from tests.driver import Driver


def escalated(sm, app_engine) -> Driver:
    """A RetryExhausted case - a kind a person may resume, so "approve" would go back to the agent."""
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    d.retry_exhausted()
    assert d.state is State.AWAITING_HUMAN_REVIEW
    return d


def request(sm, d, *, kind="question", document=None, deadline=None, decision="request"):
    approval_id = d.approval(decision, patient_deadline=deadline or datetime.now(UTC) + timedelta(hours=24))
    payload = {"approval_id": approval_id, "reply_kind": kind, "requested_document": document,
               "content_hash": "HASH-MESSAGE"}
    return sm.apply(d.case_id, Event.PATIENT_REPLY_REQUESTED, payload, Component.EXTERNAL)


def reply(sm, d, *, kind="question", document_type=None, source=Component.SESSION_SERVICE):
    payload = {"reply_kind": kind, "content_hash": "HASH-REPLY"}
    if document_type is not None:
        payload["document_type"] = document_type
    return sm.apply(d.case_id, Event.PATIENT_REPLY_SUBMITTED, payload, source)


def test_a_request_waits_for_the_patient_with_the_deadline_the_reviewer_signed(sm, app_engine):
    d = escalated(sm, app_engine)
    deadline = datetime.now(UTC) + timedelta(hours=30)
    result = request(sm, d, deadline=deadline)
    assert result.committed and result.state_after is State.AWAITING_PATIENT_REPLY
    case = d.case
    assert (case.human_engaged, case.reply_kind, case.requested_document) == (True, "question", None)
    assert case.patient_deadline == deadline
    assert case.escalation_kind is EscalationKind.RETRY_EXHAUSTED


def test_the_reply_returns_the_case_to_review_with_its_escalation(sm, app_engine):
    d = escalated(sm, app_engine)
    request(sm, d)
    result = reply(sm, d)
    assert result.committed and result.state_after is State.AWAITING_HUMAN_REVIEW
    case = d.case
    assert case.escalation_kind is EscalationKind.RETRY_EXHAUSTED
    assert (case.reply_kind, case.requested_document, case.patient_deadline) == (None, None, None)
    assert case.human_engaged is True


def test_a_reply_that_does_not_come_through_the_session_service_is_blocked(sm, app_engine):
    d = escalated(sm, app_engine)
    request(sm, d)
    result = reply(sm, d, source=Component.EXTERNAL)
    assert not result.committed and d.state is State.AWAITING_PATIENT_REPLY


def test_a_document_reply_must_be_the_requested_type(sm, app_engine):
    d = escalated(sm, app_engine)
    request(sm, d, kind="document", document="URINALYSIS")
    assert not reply(sm, d, kind="document", document_type="CBC").committed
    assert not reply(sm, d, kind="question").committed
    assert reply(sm, d, kind="document", document_type="URINALYSIS").committed


@pytest.mark.parametrize("kind, document", [("question", "CBC"), ("document", None), ("document", "X-RAY"),
                                            ("chat", None)])
def test_a_request_names_a_catalog_document_exactly_when_it_asks_for_one(sm, app_engine, kind, document):
    d = escalated(sm, app_engine)
    assert not request(sm, d, kind=kind, document=document).committed


@pytest.mark.parametrize("offset", [timedelta(minutes=-1), timedelta(days=7, minutes=1)])
def test_the_deadline_is_ahead_and_within_seven_days(sm, app_engine, offset):
    d = escalated(sm, app_engine)
    assert not request(sm, d, deadline=datetime.now(UTC) + offset).committed


def test_the_deadline_is_not_after_a_known_appointment(sm, app_engine):
    d = escalated(sm, app_engine)
    appointment = datetime.now(UTC) + timedelta(hours=10)
    with app_engine.begin() as conn:
        conn.execute(text("UPDATE cases SET appointment_at = :a WHERE case_id = :c"),
                     {"a": appointment, "c": d.case_id})
    assert not request(sm, d, deadline=appointment + timedelta(minutes=1)).committed
    assert request(sm, d, deadline=appointment).committed


def test_a_resolve_approval_cannot_make_a_request(sm, app_engine):
    d = escalated(sm, app_engine)
    result = request(sm, d, decision="resolve")
    assert not result.committed and result.reason == "approval_decision_mismatch"


def test_approve_after_a_request_is_refused_as_human_engaged(sm, app_engine):
    d = escalated(sm, app_engine)
    request(sm, d)
    reply(sm, d)
    result = d.human(Event.HUMAN_APPROVED, d.approval("approve"))
    assert not result.committed
    assert result.reason == "human_engaged"
    assert result.temporal_violation is None  # the guard refuses first - T13 is the backstop (Task 3)
    assert d.state is State.AWAITING_HUMAN_REVIEW


@pytest.mark.parametrize("event, target", [(Event.HUMAN_RESOLVED_CASE, State.COMPLETED),
                                           (Event.HUMAN_REJECTED, State.FAILED)])
def test_resolve_and_reject_still_close_after_a_reply(sm, app_engine, event, target):
    d = escalated(sm, app_engine)
    request(sm, d)
    reply(sm, d)
    decision = {Event.HUMAN_RESOLVED_CASE: "resolve", Event.HUMAN_REJECTED: "reject"}[event]
    result = d.human(event, d.approval(decision))
    assert result.committed and result.state_after is target


def test_an_unanswered_request_goes_back_to_review_with_its_escalation(sm, app_engine):
    d = escalated(sm, app_engine)
    request(sm, d)
    with app_engine.begin() as conn:
        conn.execute(text("UPDATE cases SET patient_deadline = now() - interval '1 minute' WHERE case_id = :c"),
                     {"c": d.case_id})
    [result] = SlaWorker(sm).tick()
    assert result.committed and result.state_after is State.AWAITING_HUMAN_REVIEW
    case = d.case
    assert case.escalation_kind is EscalationKind.RETRY_EXHAUSTED  # never PatientSlaExpired (design §5.2)
    assert (case.reply_kind, case.patient_deadline, case.human_engaged) == (None, None, True)
```

- [ ] **Step 2: Run them to see them fail**

Run: `$DC run --rm backend pytest tests/test_extension.py tests/test_patient_request_fsm.py -v`
Expected: FAIL — `ImportError: cannot import name 'EXTENSION_TRANSITIONS'`.

- [ ] **Step 3: The guards**

In `backend/hospital_agent/guards.py`:

Add `from datetime import datetime, timedelta` (replacing `from datetime import datetime`) and `from .documents import CATALOG_LABELS` beside the other local imports.

After `WORKFLOW_DECISION_INVALID = "workflow_decision_invalid"` add:

```python
HUMAN_ENGAGED = "human_engaged"  # sub-project 15 (row 84): a person has written to the patient

# Sub-project 15 (design §5.3, §8): what a staff request may ask for, and for how long.
REPLY_KINDS = frozenset({"question", "document"})
MAX_REPLY_WINDOW = timedelta(days=7)
```

In `DECISION_FOR_EVENT`, add `Event.PATIENT_REPLY_REQUESTED: "request",` after the resolve entry.

In `workflow_decision_valid`, directly before `if ctx.event is Event.HUMAN_APPROVED:` add:

```python
    if ctx.event is Event.HUMAN_APPROVED and case.human_engaged:
        # Sub-project 15 (row 84): once a person has written to the patient the case never goes
        # back to the agent - a fail-closed tightening of this guard; no §3 row changed.
        return HUMAN_ENGAGED
```

In `patient_sla_expired`, change `and case.state is State.AWAITING_PATIENT_INPUT` to

```python
        and case.state in (State.AWAITING_PATIENT_INPUT, State.AWAITING_PATIENT_REPLY)  # + sub-project 15
```

Before `GUARDS: dict[str, Guard] = {` add:

```python
def reply_request_valid(ctx: GuardContext) -> str | None:
    """Sub-project 15 (design §5.3, §8): what the staff asked for, and until when. The deadline is
    the one on the reviewer's approval row - never a payload value."""
    case, approval, payload = ctx.case, ctx.approval, ctx.payload
    if case is None or approval is None:
        return GUARD_FAILED
    kind, document, deadline = payload.get("reply_kind"), payload.get("requested_document"), approval.patient_deadline
    return _check(
        kind in REPLY_KINDS
        and (document in CATALOG_LABELS if kind == "document" else document is None)
        and isinstance(deadline, datetime) and deadline.tzinfo is not None
        and ctx.now < deadline <= ctx.now + MAX_REPLY_WINDOW
        and (case.appointment_at is None or deadline <= case.appointment_at)
    )


def patient_reply_valid(ctx: GuardContext) -> str | None:
    """Sub-project 15 (design §5.3, §9): the reply comes through the Session Service - the one
    component that checks the patient and runs the document intake - and answers what was asked."""
    case, payload = ctx.case, ctx.payload
    if case is None or ctx.source is not Component.SESSION_SERVICE or not _nonempty(payload.get("content_hash")):
        return GUARD_FAILED
    if case.reply_kind == "question":
        return _check(payload.get("reply_kind") == "question")
    if case.reply_kind == "document":
        return _check(payload.get("reply_kind") == "document"
                      and payload.get("document_type") == case.requested_document)
    return GUARD_FAILED
```

In `GUARDS`, add (they are snake_case: `tests/test_guards.py` compares only PascalCase names against the spec):

```python
    "reply_request_valid": reply_request_valid,  # sub-project 15
    "patient_reply_valid": patient_reply_valid,  # sub-project 15
```

- [ ] **Step 4: The rows and effects**

In `backend/hospital_agent/fsm.py`:

Replace the module docstring's first paragraph with:

```python
"""Transition table - spec §3, the single source of truth for State changes.

41 rows. tests/test_fsm.py compares (source, event, spec_guard, target) of every
row with docs/spec/03-transitions-guards.md, so the code cannot drift from the spec.
EXTENSION_TRANSITIONS holds sub-project 15's three rows (docs/spec_corrections.md
rows 83-85), outside that table; resolve() searches both.
```

(keep the "A guard name starting with "!" is negated" line after it).

In `class Effect`, after `RECORD_EXECUTION_INTENT = …`:

```python
    RECORD_REPLY_REQUEST = "RecordReplyRequest"  # sub-project 15
    CLEAR_REPLY_REQUEST = "ClearReplyRequest"  # sub-project 15
```

After the closing `)` of `TRANSITIONS`:

```python
# Sub-project 15 (docs/spec_corrections.md rows 83-85; design §5.2): the owner's extension, kept out
# of TRANSITIONS so that table stays §3 row for row. No row here sets an escalation kind - the case
# goes back to review with the escalation it came with (a PatientSlaExpired kind would be resumable,
# and its HUMAN_APPROVED row leads back to the agent).
EXTENSION_TRANSITIONS: tuple[Transition, ...] = (
    Transition(S.AWAITING_HUMAN_REVIEW, E.PATIENT_REPLY_REQUESTED, S.AWAITING_PATIENT_REPLY,
               "WorkflowDecisionValid (decision = request), reply_request_valid",
               ("WorkflowDecisionValid", "reply_request_valid"),
               effects=(Effect.RECORD_REPLY_REQUEST, Effect.CONSUME_APPROVAL)),
    Transition(S.AWAITING_PATIENT_REPLY, E.PATIENT_REPLY_SUBMITTED, S.AWAITING_HUMAN_REVIEW,
               "patient_reply_valid", ("patient_reply_valid",), effects=(Effect.CLEAR_REPLY_REQUEST,)),
    Transition(S.AWAITING_PATIENT_REPLY, E.TIMEOUT_EXPIRED, S.AWAITING_HUMAN_REVIEW,
               "PatientSlaExpired", ("PatientSlaExpired",), effects=(Effect.CLEAR_REPLY_REQUEST,)),
)
```

In `resolve()`, change the candidates line to:

```python
    candidates = [row for row in (*TRANSITIONS, *EXTENSION_TRANSITIONS) if row.source == state and row.event == event]
```

In `apply_effects`, before `case Effect.CONSUME_APPROVAL | Effect.RECORD_EXECUTION_INTENT:` add:

```python
            case Effect.RECORD_REPLY_REQUEST:
                changes.update(human_engaged=True, reply_kind=p["reply_kind"],
                               requested_document=p.get("requested_document"),
                               patient_deadline=ctx.approval.patient_deadline)
            case Effect.CLEAR_REPLY_REQUEST:
                changes.update(reply_kind=None, requested_document=None, patient_deadline=None)
```

- [ ] **Step 5: The SLA Worker's scan**

In `backend/hospital_agent/repository.py`, `expired_patient_deadlines`, change the `.where(...)` to

```python
        .where(cases.c.state.in_((State.AWAITING_PATIENT_INPUT.value, State.AWAITING_PATIENT_REPLY.value)),
               cases.c.patient_deadline <= now)
```

and its docstring's first line to `"""Cases waiting for the patient - a document, or a reply to a staff request (sub-project 15) -
    whose deadline has passed: the SLA Worker's scan (§18.2 index)."""`.

- [ ] **Step 6: Run the tests**

Run: `$DC run --rm backend pytest tests/test_extension.py tests/test_patient_request_fsm.py tests/test_fsm.py tests/test_guards.py tests/test_sla.py -v` (if `tests/test_sla.py` does not exist, use `-k sla` across the suite instead).
Expected: all pass. Then the whole suite once: `$DC run --rm backend pytest -q`.

- [ ] **Step 7: Commit**

```bash
git add backend/hospital_agent/fsm.py backend/hospital_agent/guards.py backend/hospital_agent/repository.py backend/tests/test_extension.py backend/tests/test_patient_request_fsm.py
git commit -m "Add the request, reply and timeout rows, and refuse approve once a person has written" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: T13 — the case never reaches the agent after a request

**Files:**
- Modify: `backend/hospital_agent/policy/temporal.py`
- Modify test: `backend/tests/test_temporal.py` (append)

**Interfaces:**
- Produces: `temporal.AGENT_STATES`, rule `("T13", _t13)` last in `RULES`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_temporal.py` (it already defines `row()` and imports `violations`):

```python
# --- T13, sub-project 15's extension (docs/spec_corrections.md row 84) -----------------------

REQUEST_THEN_REPLY = [
    row("RETRY_EXHAUSTED", "AwaitingHumanReview"),
    row("PATIENT_REPLY_REQUESTED", "AwaitingPatientReply"),
    row("PATIENT_REPLY_SUBMITTED", "AwaitingHumanReview"),
]


@pytest.mark.parametrize("state", ["Classifying", "Classified", "Planning", "RetrievingData", "Delivering",
                                   "AssessingReadiness", "Ready"])
def test_t13_no_agent_state_after_a_request(state):
    found = [(v.rule, v.index) for v in violations([*REQUEST_THEN_REPLY, row("HUMAN_APPROVED", state)])]
    assert ("T13", 3) in found


def test_t13_holds_while_the_case_stays_with_people():
    trace = [*REQUEST_THEN_REPLY, row("HUMAN_RESOLVED_CASE", "Completed")]
    assert "T13" not in {v.rule for v in violations(trace)}


def test_t13_holds_without_a_request():
    trace = [row("RETRY_EXHAUSTED", "AwaitingHumanReview"), row("HUMAN_APPROVED", "Planning")]
    assert "T13" not in {v.rule for v in violations(trace)}
```

(If `pytest` is not already imported in the file, add `import pytest` at the top.)

- [ ] **Step 2: Run them to see them fail**

Run: `$DC run --rm backend pytest tests/test_temporal.py -k t13 -v`
Expected: the parametrized test FAILS (no T13 violation reported); the other two pass vacuously.

- [ ] **Step 3: Add T13**

In `backend/hospital_agent/policy/temporal.py`, append to the module docstring's first paragraph: ` T13 is sub-project 15's extension (docs/spec_corrections.md row 84), not a §6.2 rule.`

Before `RULES: tuple[...] = (` add:

```python
# Sub-project 15 (docs/spec_corrections.md row 84): the states in which an AI component or the
# Tool Executor acts. A case a person has written to (PATIENT_REPLY_REQUESTED) never enters one.
AGENT_STATES = frozenset({"Classifying", "Classified", "Planning", "RetrievingData", "Delivering",
                          "AssessingReadiness", "Ready"})


def _t13(rows: Rows, i: int) -> bool:  # extension: G(AgentState -> ¬ O PATIENT_REPLY_REQUESTED)
    return rows[i].state_after not in AGENT_STATES or not any(
        r.event == "PATIENT_REPLY_REQUESTED" for r in rows[: i + 1])
```

and add `("T13", _t13),` as the last entry of `RULES`.

- [ ] **Step 4: Run the tests**

Run: `$DC run --rm backend pytest tests/test_temporal.py tests/test_patient_request_fsm.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/hospital_agent/policy/temporal.py backend/tests/test_temporal.py
git commit -m "Add T13: a case a person has written to never enters an agent state" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Clinical free text — the message approval

**Files:**
- Modify: `backend/hospital_agent/state_manager.py`
- Create test: `backend/tests/test_message_approval.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: `state_manager.MESSAGE_EVENTS`, `_patient_message_approval(conn, case, approval_id, now)`; the payload key `message_approval_id` on `PATIENT_REPLY_REQUESTED`, `HUMAN_RESOLVED_CASE`, `HUMAN_REJECTED`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_message_approval.py`:

```python
"""Clinical free text to the patient (sub-project 15, design §7.3): bound by a ContentApproval
exactly like the clinical answer, but under its own payload key and verifier."""
import uuid
from datetime import UTC, datetime, timedelta

from hospital_agent import data_log, repository
from hospital_agent.case import ApprovalRecord, ExecutionRecord
from hospital_agent.naming import Action, Component, Event, State
from tests.test_patient_request_fsm import escalated


def clinical_message(d, text="נא לפרט את מועד התור ואת המחלקה", role="clinical_staff") -> tuple[str, str]:
    """What HumanReviewService does for free text (Task 6): the Data Log entry, the execution row
    and the ContentApproval bound to them. Returns (approval_id, content_hash)."""
    case, now = d.case, datetime.now(UTC)
    with d.engine.begin() as conn:
        entry = data_log.record(conn, case.case_id, case.patient_id, data_log.DataKind.STAFF_MESSAGE, text, now)
        execution_id = f"EXEC-{uuid.uuid4().hex[:12]}"
        repository.insert_execution(conn, ExecutionRecord(
            execution_id=execution_id, case_id=case.case_id, patient_id=case.patient_id,
            action=Action.ANSWER_CLINICAL_QUESTION.value, step=case.current_step or 0,
            retry_cycle=case.retry_cycle, attempt_number=0,
            idempotency_key=f"{case.case_id}:message:{execution_id}", status="succeeded",
            state_version=case.state_version, content_hash=entry.content_hash, medical_content_flag=True))
        approval_id = f"APPR-{uuid.uuid4().hex[:12]}"
        repository.insert_approval(conn, ApprovalRecord(
            approval_id=approval_id, approval_type="ContentApproval", case_id=case.case_id,
            patient_id=case.patient_id, reviewer_id="coordinator_nurse", reviewer_role=role, decision="approve",
            reason="clinical message", shown_context_ref="ctx", granted_at=now - timedelta(minutes=1),
            valid_until=now + timedelta(hours=1), execution_id=execution_id,
            action=Action.ANSWER_CLINICAL_QUESTION.value, content_hash=entry.content_hash))
    return approval_id, entry.content_hash


def request_with(sm, d, message_approval_id, content_hash):
    payload = {"approval_id": d.approval("request", patient_deadline=datetime.now(UTC) + timedelta(hours=24)),
               "reply_kind": "question", "requested_document": None, "content_hash": content_hash,
               "message_approval_id": message_approval_id}
    return sm.apply(d.case_id, Event.PATIENT_REPLY_REQUESTED, payload, Component.EXTERNAL)


def _approval(d, approval_id):
    with d.engine.connect() as conn:
        return repository.load_approval(conn, approval_id)


def test_a_clinical_message_rides_on_a_request_and_is_consumed(sm, app_engine):
    d = escalated(sm, app_engine)
    approval_id, content_hash = clinical_message(d)
    result = request_with(sm, d, approval_id, content_hash)
    assert result.committed and result.state_after is State.AWAITING_PATIENT_REPLY
    assert _approval(d, approval_id).consumed_at is not None
    committed = [r for r in d.trace if r.event == "PATIENT_REPLY_REQUESTED" and r.record_type == "Transition"]
    assert committed[-1].content_hash == content_hash


def test_an_approval_granted_by_admin_staff_is_refused(sm, app_engine):
    d = escalated(sm, app_engine)
    approval_id, content_hash = clinical_message(d, role="admin_staff")
    result = request_with(sm, d, approval_id, content_hash)
    assert not result.committed and result.reason == "content_approval_invalid"
    assert d.state is State.AWAITING_HUMAN_REVIEW


def test_a_message_approval_on_approve_is_refused(sm, app_engine):
    d = escalated(sm, app_engine)
    approval_id, _ = clinical_message(d)
    result = sm.apply(d.case_id, Event.HUMAN_APPROVED,
                      {"approval_id": d.approval("approve"), "message_approval_id": approval_id}, Component.EXTERNAL)
    assert not result.committed and result.reason == "content_approval_invalid"


def test_both_approval_keys_at_once_are_refused(sm, app_engine):
    d = escalated(sm, app_engine)
    approval_id, _ = clinical_message(d)
    result = sm.apply(d.case_id, Event.HUMAN_RESOLVED_CASE,
                      {"approval_id": d.approval("resolve"), "message_approval_id": approval_id,
                       "content_approval_id": approval_id}, Component.EXTERNAL)
    assert not result.committed and result.reason == "content_approval_invalid"


def test_a_closing_message_rides_on_reject(sm, app_engine):
    d = escalated(sm, app_engine)
    approval_id, content_hash = clinical_message(d, text="לא נוכל לטפל בפנייה, נא לפנות למרפאה")
    result = sm.apply(d.case_id, Event.HUMAN_REJECTED,
                      {"approval_id": d.approval("reject"), "message_approval_id": approval_id,
                       "content_hash": content_hash}, Component.EXTERNAL)
    assert result.committed and result.state_after is State.FAILED
    assert _approval(d, approval_id).consumed_at is not None


def test_a_used_message_approval_cannot_be_used_twice(sm, app_engine):
    d = escalated(sm, app_engine)
    approval_id, content_hash = clinical_message(d)
    assert request_with(sm, d, approval_id, content_hash).committed
    sm.apply(d.case_id, Event.PATIENT_REPLY_SUBMITTED, {"reply_kind": "question", "content_hash": "HASH-REPLY"},
             Component.SESSION_SERVICE)
    result = sm.apply(d.case_id, Event.HUMAN_RESOLVED_CASE,
                      {"approval_id": d.approval("resolve"), "message_approval_id": approval_id}, Component.EXTERNAL)
    assert not result.committed and result.reason == "content_approval_invalid"
```

- [ ] **Step 2: Run them to see them fail**

Run: `$DC run --rm backend pytest tests/test_message_approval.py -v`
Expected: FAIL — the admin, approve and both-keys cases commit or block with another reason; the approval is never consumed.

- [ ] **Step 3: Verify and consume the message approval**

In `backend/hospital_agent/state_manager.py`, after the `_clinical_answer_approval` function add:

```python
# Sub-project 15 (design §7.3, docs/spec_corrections.md row 86): the events that may carry a staff
# member's clinical free text to the patient, under their own payload key (message_approval_id) -
# never content_approval_id, which is the clinical answer's.
MESSAGE_EVENTS = frozenset({Event.PATIENT_REPLY_REQUESTED, Event.HUMAN_RESOLVED_CASE, Event.HUMAN_REJECTED})


def _patient_message_approval(
    conn: Connection, case: CaseRecord, approval_id: str, now: datetime
) -> ApprovalRecord | None:
    """The ContentApproval that authorises a clinical staff message, or None if it cannot be used.

    Every check of _clinical_answer_approval except the MedicalQuestion one: a clarifying question
    or a closing message may be written on any escalation, but only by clinical_staff, bound to
    one exact text (execution_id + action + content_hash), unused and in time (§12.4).
    """
    approval = repository.load_approval(conn, approval_id)
    if approval is None or approval.approval_type != "ContentApproval" or approval.decision != "approve":
        return None
    if (approval.case_id, approval.patient_id) != (case.case_id, case.patient_id):
        return None
    if approval.reviewer_role != "clinical_staff" or approval.action != Action.ANSWER_CLINICAL_QUESTION.value:
        return None
    if not approval.content_hash or not approval.execution_id:
        return None
    if approval.consumed_at is not None or approval.valid_until <= now:
        return None
    execution = repository.load_execution(conn, approval.execution_id)
    if execution is None or execution.case_id != case.case_id:
        return None
    if execution.content_hash != approval.content_hash or not execution.medical_content_flag:
        return None
    return approval
```

In `apply`, directly after the `content_approval` block (the one ending with `"execution_id": content_approval.execution_id}`), add:

```python
            message_approval = None
            if payload.get("message_approval_id"):
                if case is None or event not in MESSAGE_EVENTS or payload.get("content_approval_id"):
                    return self._block(conn, case, event, CONTENT_APPROVAL_INVALID, now, payload=payload)
                message_approval = _patient_message_approval(conn, case, payload["message_approval_id"], now)
                if message_approval is None:
                    return self._block(conn, case, event, CONTENT_APPROVAL_INVALID, now, payload=payload)
                # The audit row describes what was verified, never what the caller claimed.
                payload = {**payload, "content_hash": message_approval.content_hash}
```

and after the existing `if content_approval is not None:` consumption block:

```python
            if message_approval is not None:
                if repository.consume_approval(conn, message_approval.approval_id, now) == 0:
                    raise _StaleVersion(case.case_id)
```

- [ ] **Step 4: Run the tests**

Run: `$DC run --rm backend pytest tests/test_message_approval.py tests/test_clinical_answer.py tests/test_patient_request_fsm.py -v`
Expected: all pass (the clinical answer's tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/hospital_agent/state_manager.py backend/tests/test_message_approval.py
git commit -m "Bind a clinical staff message to its ContentApproval under its own key" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The message templates

**Files:**
- Create: `backend/hospital_agent/patient_messages.py`
- Create test: `backend/tests/test_patient_messages.py`

**Interfaces:**
- Produces: `Template(template_id, purpose, text, param, options)`, `TEMPLATES`, `DOCUMENT_REQUEST = "document_request"`, `InvalidMessage(code)`, `render(template_id, param=None) -> str`, `purpose_of(template_id) -> str | None`, `is_template_text(text) -> bool`, `as_dicts() -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_patient_messages.py`:

```python
"""The fixed staff messages (sub-project 15, design §7.1): no free text, parameters from closed lists."""
import pytest

from hospital_agent import patient_messages as pm
from hospital_agent.documents import CATALOG_LABELS


def test_a_plain_template_renders_its_text():
    assert pm.render("clarify_general") == "לא הצלחנו להבין את פנייתך. נשמח אם תפרט/י במה נוכל לעזור."


def test_did_you_mean_takes_a_topic_from_its_closed_list():
    assert pm.render("clarify_did_you_mean", "appointment_time") == "האם התכוונת למועד התור? נשמח לאישור או לפירוט."


def test_a_document_request_names_the_catalog_label():
    assert pm.render(pm.DOCUMENT_REQUEST, "URINALYSIS") == "נא להעלות את המסמך: בדיקת שתן."


@pytest.mark.parametrize("template_id, param, code", [
    ("no_such_template", None, "unknown_template"),
    ("clarify_general", "appointment_time", "unexpected_param"),
    ("clarify_did_you_mean", None, "invalid_param"),
    ("clarify_did_you_mean", "anything goes", "invalid_param"),
    (pm.DOCUMENT_REQUEST, "X-RAY", "invalid_param"),
])
def test_anything_outside_the_lists_is_refused(template_id, param, code):
    with pytest.raises(pm.InvalidMessage) as invalid:
        pm.render(template_id, param)
    assert invalid.value.code == code


def test_every_rendered_text_is_recognised_and_nothing_else_is():
    assert pm.is_template_text(pm.render("close_no_reply"))
    for code in CATALOG_LABELS:
        assert pm.is_template_text(pm.render(pm.DOCUMENT_REQUEST, code))
    assert not pm.is_template_text("לא הצלחנו להבין את פנייתך.")  # a fragment is not a template
    assert not pm.is_template_text("קח שני כדורים ביום")


def test_purposes_and_the_api_shape():
    assert {pm.purpose_of(t.template_id) for t in pm.TEMPLATES} == {"question", "document", "closing"}
    assert pm.purpose_of("nope") is None
    shapes = pm.as_dicts()
    assert set(shapes[0]) == {"template_id", "purpose", "text", "param", "options"}
    document = next(s for s in shapes if s["template_id"] == pm.DOCUMENT_REQUEST)
    assert document["param"] == "document" and document["options"] == CATALOG_LABELS
```

- [ ] **Step 2: Run them to see them fail**

Run: `$DC run --rm backend pytest tests/test_patient_messages.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hospital_agent.patient_messages'`.

- [ ] **Step 3: Write the module**

`backend/hospital_agent/patient_messages.py`:

```python
"""The fixed messages a staff member may send the patient without a ContentApproval
(sub-project 15, design §7.1).

Each text is written and approved in advance; a parameter, when there is one, comes from a
closed list - there is no free text here, so no medical content can reach the patient through
a template. The patient screen shows a staff message only if it is one of these texts or a
clinical staff member's text with a consumed ContentApproval (design §7.5): is_template_text()
is that first half. The owner edits the wording here, and only here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache

from .documents import CATALOG_LABELS

DOCUMENT_REQUEST = "document_request"

TOPICS: dict[str, str] = {
    "appointment_time": "מועד התור",
    "required_documents": "המסמכים הנדרשים לתור",
    "preparation": "הוראות ההכנה לתור",
}


@dataclass(frozen=True)
class Template:
    template_id: str
    purpose: str  # "question" | "document" | "closing"
    text: str
    param: str | None = None  # the {placeholder} the text takes, if any
    options: Mapping[str, str] = field(default_factory=dict)  # the closed list: code -> Hebrew


TEMPLATES: tuple[Template, ...] = (
    Template("clarify_general", "question", "לא הצלחנו להבין את פנייתך. נשמח אם תפרט/י במה נוכל לעזור."),
    Template("clarify_did_you_mean", "question", "האם התכוונת ל{topic}? נשמח לאישור או לפירוט.", "topic", TOPICS),
    Template("clarify_appointment", "question",
             "האם הפנייה נוגעת לתור קיים? אם כן, נא לציין את התאריך או את המחלקה."),
    Template(DOCUMENT_REQUEST, "document", "נא להעלות את המסמך: {document}.", "document", CATALOG_LABELS),
    Template("close_handled", "closing", "פנייתך טופלה על ידי הצוות."),
    Template("close_out_of_scope", "closing",
             "פנייתך אינה בתחום שהמערכת מטפלת בו. לשאלות אחרות ניתן לפנות למוקד."),
    Template("close_no_reply", "closing",
             "לא התקבלה תשובה בזמן, ולכן הפנייה נסגרה. אפשר לפתוח פנייה חדשה בכל עת."),
)
_BY_ID = {template.template_id: template for template in TEMPLATES}


class InvalidMessage(ValueError):
    """`code` is the API's detail: unknown_template, unexpected_param or invalid_param."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def purpose_of(template_id: str | None) -> str | None:
    template = _BY_ID.get(template_id or "")
    return template.purpose if template else None


def render(template_id: str, param: str | None = None) -> str:
    template = _BY_ID.get(template_id)
    if template is None:
        raise InvalidMessage("unknown_template")
    if template.param is None:
        if param is not None:
            raise InvalidMessage("unexpected_param")
        return template.text
    if param not in template.options:
        raise InvalidMessage("invalid_param")
    return template.text.format(**{template.param: template.options[param]})


@cache
def _all_texts() -> frozenset[str]:
    return frozenset(render(t.template_id, option) for t in TEMPLATES for option in (t.options or [None]))


def is_template_text(text: str) -> bool:
    return text in _all_texts()


def as_dicts() -> list[dict]:
    return [{"template_id": t.template_id, "purpose": t.purpose, "text": t.text, "param": t.param,
             "options": dict(t.options)} for t in TEMPLATES]
```

- [ ] **Step 4: Run the tests**

Run: `$DC run --rm backend pytest tests/test_patient_messages.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/hospital_agent/patient_messages.py backend/tests/test_patient_messages.py
git commit -m "Add the fixed staff messages, with parameters from closed lists only" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The Human Review Service — request, closing message, the queue

**Files:**
- Modify: `backend/hospital_agent/human_review.py`
- Create test: `backend/tests/test_review_requests.py`

**Interfaces:**
- Consumes: Tasks 1–5; `self.session.document_intake` (sub-project 13) tells whether document requests are possible.
- Produces: `HumanReviewService.request(*, reviewer_id, reviewer_role, case_id, kind, reason, shown_context_ref, template_id=None, param=None, text=None, document_type=None, deadline=None) -> TransitionResult`; `decide(..., message=None)` where `message` is `{"template_id", "param"}` or `{"text"}`; `templates() -> list[dict]`; `ReviewItem.human_engaged: bool`, `ReviewItem.returned_by: str | None`; constants `DEFAULT_REPLY_WINDOW = timedelta(hours=24)`, `MAX_MESSAGE_LENGTH = 2000`. Rejection codes (all `DecisionRejected`): `reason_required`, `invalid_request`, `invalid_template`, `unknown_template`, `unexpected_param`, `invalid_param`, `message_required`, `clinical_staff_only`, `document_service_not_configured`, `invalid_deadline`, `message_not_allowed`, `human_engaged`, plus the State Manager's reasons.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_review_requests.py`:

```python
"""The staff's side of sub-project 15 (design §7, §8, §10): a request to the patient, a closing
message, and the queue - through the real Human Review Service and State Manager."""
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent import data_log, repository
from hospital_agent.human_review import DecisionRejected, HumanReviewService
from hospital_agent.naming import Component, Event, State
from hospital_agent.session import SessionService
from tests.test_patient_request_fsm import escalated

NURSE = dict(reviewer_id="coordinator_nurse", reviewer_role="clinical_staff")
ADMIN = dict(reviewer_id="admin_coordinator", reviewer_role="admin_staff")


class NoIntake:  # a configured document-service; these tests never upload
    def submit(self, patient_id, filename, data):
        raise AssertionError("not called")


def service(sm, *, documents=True) -> HumanReviewService:
    return HumanReviewService(sm, SessionService(sm, document_intake=NoIntake() if documents else None))


def ask(reviews, d, who=ADMIN, **fields):
    fields.setdefault("kind", "question")
    fields.setdefault("reason", "the request is unclear")
    fields.setdefault("shown_context_ref", reviews.context(d.case_id).shown_context_ref)
    return reviews.request(case_id=d.case_id, **who, **fields)


def staff_messages(d):
    with d.engine.connect() as conn:
        return [e for e in data_log.entries(conn, d.case_id, data_log.DataKind.STAFF_MESSAGE)]


def test_any_staff_member_asks_with_a_template(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    result = ask(reviews, d, template_id="clarify_did_you_mean", param="preparation")
    assert result.committed and d.state is State.AWAITING_PATIENT_REPLY
    [message] = staff_messages(d)
    assert message.content == "האם התכוונת להוראות ההכנה לתור? נשמח לאישור או לפירוט."
    assert d.case.patient_deadline - datetime.now(UTC) > timedelta(hours=23)  # the 24 h default


def test_free_text_needs_clinical_staff(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    with pytest.raises(DecisionRejected) as refused:
        ask(reviews, d, text="נא לפרט")
    assert refused.value.reason == "clinical_staff_only"
    assert staff_messages(d) == []
    assert ask(reviews, d, NURSE, text="נא לפרט באיזו מחלקה התור").committed


def test_a_document_request_uses_its_template_and_needs_the_document_service(sm, app_engine):
    d = escalated(sm, app_engine)
    with pytest.raises(DecisionRejected) as refused:
        ask(service(sm, documents=False), d, kind="document", document_type="URINALYSIS")
    assert refused.value.reason == "document_service_not_configured"
    reviews = service(sm)
    assert ask(reviews, d, kind="document", document_type="URINALYSIS").committed
    assert (d.case.reply_kind, d.case.requested_document) == ("document", "URINALYSIS")
    assert staff_messages(d)[-1].content == "נא להעלות את המסמך: בדיקת שתן."


@pytest.mark.parametrize("fields, code", [
    ({}, "invalid_request"),  # neither a template nor text
    ({"template_id": "clarify_general", "text": "x"}, "invalid_request"),
    ({"template_id": "close_handled"}, "invalid_template"),  # a closing template is not a question
    ({"template_id": "clarify_did_you_mean", "param": "gossip"}, "invalid_param"),
    ({"kind": "document", "document_type": "X-RAY"}, "invalid_request"),
    ({"kind": "chat", "template_id": "clarify_general"}, "invalid_request"),
    ({"template_id": "clarify_general", "reason": " "}, "reason_required"),
    ({"template_id": "clarify_general", "deadline": datetime.now(UTC) - timedelta(minutes=1)}, "invalid_deadline"),
    ({"template_id": "clarify_general", "deadline": datetime.now(UTC) + timedelta(days=8)}, "invalid_deadline"),
])
def test_input_that_cannot_be_a_request_is_refused_before_anything_is_written(sm, app_engine, fields, code):
    d = escalated(sm, app_engine)
    with pytest.raises(DecisionRejected) as refused:
        ask(service(sm), d, **fields)
    assert refused.value.reason == code
    assert staff_messages(d) == [] and d.state is State.AWAITING_HUMAN_REVIEW


def test_approve_is_gone_once_a_person_has_written(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    assert "approve" in next(i for i in reviews.queue() if i.case_id == d.case_id).allowed_decisions
    ask(reviews, d, template_id="clarify_general")
    sm.apply(d.case_id, Event.PATIENT_REPLY_SUBMITTED, {"reply_kind": "question", "content_hash": "H"},
             Component.SESSION_SERVICE)
    item = next(i for i in reviews.queue() if i.case_id == d.case_id)
    assert item.allowed_decisions == ["resolve", "reject"] and item.required_fields == []
    assert item.human_engaged and item.returned_by == "patient_reply"
    with pytest.raises(DecisionRejected) as refused:
        reviews.decide(case_id=d.case_id, decision="approve", reason="retry",
                       shown_context_ref=reviews.context(d.case_id).shown_context_ref, **NURSE)
    assert refused.value.reason == "human_engaged"


def test_a_closing_template_rides_on_resolve(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    result = reviews.decide(case_id=d.case_id, decision="resolve", reason="out of scope",
                            shown_context_ref=reviews.context(d.case_id).shown_context_ref,
                            message={"template_id": "close_out_of_scope"}, **ADMIN)
    assert result.committed and d.state is State.COMPLETED
    [message] = staff_messages(d)
    closing = [r for r in d.trace if r.event == "HUMAN_RESOLVED_CASE" and r.record_type == "Transition"]
    assert closing[-1].content_hash == message.content_hash


def test_approve_carries_no_message(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    with pytest.raises(DecisionRejected) as refused:
        reviews.decide(case_id=d.case_id, decision="approve", reason="retry",
                       shown_context_ref=reviews.context(d.case_id).shown_context_ref,
                       message={"template_id": "close_handled"}, **NURSE)
    assert refused.value.reason == "message_not_allowed"


def test_a_clinical_closing_message_is_consumed_with_reject(sm, app_engine):
    d = escalated(sm, app_engine)
    reviews = service(sm)
    result = reviews.decide(case_id=d.case_id, decision="reject", reason="not ours",
                            shown_context_ref=reviews.context(d.case_id).shown_context_ref,
                            message={"text": "נא לפנות ישירות למרפאה"}, **NURSE)
    assert result.committed and d.state is State.FAILED
    with d.engine.connect() as conn:
        [approval] = [a for a in repository.content_approvals_for(conn, d.case_id, "AnswerClinicalQuestion")]
    assert approval.consumed_at is not None


def test_a_request_that_runs_out_of_time_is_marked_in_the_queue(sm, app_engine):
    from sqlalchemy import text

    from hospital_agent.execution.sla import SlaWorker
    d = escalated(sm, app_engine)
    reviews = service(sm)
    ask(reviews, d, template_id="clarify_general")
    with app_engine.begin() as conn:
        conn.execute(text("UPDATE cases SET patient_deadline = now() - interval '1 minute' WHERE case_id = :c"),
                     {"c": d.case_id})
    SlaWorker(sm).tick()
    assert next(i for i in reviews.queue() if i.case_id == d.case_id).returned_by == "reply_timeout"


def test_the_templates_are_served(sm):
    ids = {t["template_id"] for t in service(sm).templates()}
    assert {"clarify_general", "document_request", "close_no_reply"} <= ids
```

- [ ] **Step 2: Run them to see them fail**

Run: `$DC run --rm backend pytest tests/test_review_requests.py -v`
Expected: FAIL — `AttributeError: 'HumanReviewService' object has no attribute 'request'`.

- [ ] **Step 3: Implement**

In `backend/hospital_agent/human_review.py`:

Imports: change `from . import data_log, repository` to `from . import data_log, patient_messages, repository`; add `from collections.abc import Callable, Mapping` (replacing the `Callable` import) and `from .documents import CATALOG_LABELS`.

After `DEFAULT_APPROVAL_TTL = …` add:

```python
# Sub-project 15 (design §8): a request's default deadline, and the longest a staff message may be.
DEFAULT_REPLY_WINDOW = timedelta(hours=24)
MAX_MESSAGE_LENGTH = 2000
```

In `ReviewItem`, after `updated_at: datetime`:

```python
    human_engaged: bool = False  # sub-project 15: a person has written to the patient - no approve
    returned_by: str | None = None  # "patient_reply" | "reply_timeout": how the case last came back
```

In `queue()`, change `allowed_decisions=allowed_decisions(case.escalation_kind),` and `required_fields=list(RESUMABLE.get(case.escalation_kind, ())),` to

```python
                allowed_decisions=_allowed(case),
                required_fields=[] if case.human_engaged else list(RESUMABLE.get(case.escalation_kind, ())),
                updated_at=case.updated_at,
                human_engaged=case.human_engaged,
                returned_by=_returned_by(traces[case.case_id]),
```

(removing the old `updated_at=case.updated_at,` line so it appears once).

Replace `decide()` with (the only changes: the `message` parameter, the `human_engaged` / `message_not_allowed` refusals, and recording the message):

```python
    def decide(self, *, reviewer_id: str, reviewer_role: str, case_id: str, decision: str, reason: str,
               shown_context_ref: str, verified_identity_ref: str | None = None,
               patient_deadline: datetime | None = None, message: Mapping[str, Any] | None = None) -> TransitionResult:
        """Record the reviewer's decision and apply its human event (§3, §12.5).

        Refuses before anything is written: an unknown or not-escalated case, invalid input,
        a context that has changed, or a blocked event (no approval row is left behind in
        the first three). Once the event commits the decision is never reported as an error -
        a case that cannot then be revalidated simply stays in Received for the staff.

        Sub-project 15: `resolve` and `reject` may carry a closing message for the patient
        (a closing template, or clinical free text); `approve` never does, and is refused
        once a person has written to the patient (design §6).
        """
        case = self._load(case_id)
        if case.state is not State.AWAITING_HUMAN_REVIEW:
            raise NotInReview(case_id)

        if decision not in DECISION_EVENT:
            raise DecisionRejected("invalid_decision")
        if not (reason or "").strip():
            raise DecisionRejected("reason_required")
        if decision == "approve" and message is not None:
            raise DecisionRejected("message_not_allowed")
        if decision == "approve" and case.human_engaged:
            raise DecisionRejected("human_engaged")
        given = {"verified_identity_ref": verified_identity_ref, "patient_deadline": patient_deadline}
        if decision == "approve":
            if case.escalation_kind not in RESUMABLE:
                raise DecisionRejected("decision_not_allowed")
            for name in RESUMABLE[case.escalation_kind]:
                value = given[name]
                if value is None or (isinstance(value, str) and not value.strip()):
                    raise DecisionRejected(f"{name}_required")
        text = None if message is None else self._message(reviewer_role, "closing", message)

        if shown_context_ref != self.context(case_id).shown_context_ref:
            raise ContextChanged(case_id)

        payload, entry_id = ({}, None) if text is None else self._record_message(
            case, reviewer_id, reviewer_role, reason, shown_context_ref, text, free=bool(message.get("text")))
        approval_id = self._grant(case, reviewer_id, reviewer_role, decision, reason, shown_context_ref,
                                  verified_identity_ref, patient_deadline)
        result = self._apply(case_id, DECISION_EVENT[decision], {"approval_id": approval_id, **payload}, entry_id)

        if (decision == "approve" and case.escalation_kind is EscalationKind.PATIENT_VERIFICATION_FAILED
                and result.state_after is State.RECEIVED):
            # Design decision 7: identity is now established - the request goes on from the
            # text the patient already submitted. If it cannot (the text was deleted, or the
            # event is blocked), the committed decision still stands: the case stays in
            # Received and the staff see it in the monitor.
            try:
                self.session.revalidate(case_id)
            except EventRejected as rejected:
                logger.info("case not revalidated after an identity approval: %s", rejected.reason)
        self.wake()
        return result
```

After `decide()` add:

```python
    def request(self, *, reviewer_id: str, reviewer_role: str, case_id: str, kind: str, reason: str,
                shown_context_ref: str, template_id: str | None = None, param: str | None = None,
                text: str | None = None, document_type: str | None = None,
                deadline: datetime | None = None) -> TransitionResult:
        """Sub-project 15 (design §5, §7, §8): ask the patient a question or for one catalog document.

        Everything that cannot be a legal request is refused before anything is written; the
        guards (WorkflowDecisionValid with decision = request, reply_request_valid) judge the rest.
        """
        case = self._load(case_id)
        if case.state is not State.AWAITING_HUMAN_REVIEW:
            raise NotInReview(case_id)
        if not (reason or "").strip():
            raise DecisionRejected("reason_required")
        if kind == "document":
            if text is not None or template_id not in (None, patient_messages.DOCUMENT_REQUEST) \
                    or document_type not in CATALOG_LABELS:
                raise DecisionRejected("invalid_request")
            if self.session.document_intake is None:  # configuration, so here and not in a guard (§9)
                raise DecisionRejected("document_service_not_configured")
            body = patient_messages.render(patient_messages.DOCUMENT_REQUEST, document_type)
        elif kind == "question" and document_type is None:
            body = self._message(reviewer_role, "question", {"template_id": template_id, "param": param, "text": text})
        else:
            raise DecisionRejected("invalid_request")
        now = self.sm.clock()
        deadline = deadline or now + DEFAULT_REPLY_WINDOW
        if not now < deadline <= now + timedelta(days=7) or (
                case.appointment_at is not None and deadline > case.appointment_at):
            raise DecisionRejected("invalid_deadline")
        if shown_context_ref != self.context(case_id).shown_context_ref:
            raise ContextChanged(case_id)

        payload, entry_id = self._record_message(case, reviewer_id, reviewer_role, reason, shown_context_ref, body,
                                                 free=text is not None)
        approval_id = self._grant(case, reviewer_id, reviewer_role, "request", reason, shown_context_ref, None, deadline)
        return self._apply(case_id, Event.PATIENT_REPLY_REQUESTED,
                           {"approval_id": approval_id, "reply_kind": kind,
                            "requested_document": document_type if kind == "document" else None, **payload},
                           entry_id)

    def templates(self) -> list[dict]:
        return patient_messages.as_dicts()

    @staticmethod
    def _message(reviewer_role: str, purpose: str, message: Mapping[str, Any]) -> str:
        """The exact text a staff message will carry (design §7.2): a template of `purpose`, or
        clinical staff's free text - exactly one of the two."""
        template_id, text = message.get("template_id"), message.get("text")
        if (template_id is None) == (text is None):
            raise DecisionRejected("invalid_request")
        if text is not None:
            if reviewer_role != CLINICAL_STAFF:
                raise DecisionRejected("clinical_staff_only")  # §12.4
            body = text.strip()
            if not body or len(body) > MAX_MESSAGE_LENGTH:
                raise DecisionRejected("message_required")
            return body
        if patient_messages.purpose_of(template_id) != purpose:
            raise DecisionRejected("invalid_template")
        try:
            return patient_messages.render(template_id, message.get("param"))
        except patient_messages.InvalidMessage as invalid:
            raise DecisionRejected(invalid.code) from None

    def _record_message(self, case: CaseRecord, reviewer_id: str, reviewer_role: str, reason: str,
                        shown_context_ref: str, text: str, *, free: bool) -> tuple[dict[str, Any], str]:
        """The message in the Data Log; free text also gets the clinical answer's binding - an
        execution row and a ContentApproval on its content_hash (design §7.3). Returns the event
        payload's message fields and the entry's id (tombstoned if the event is blocked)."""
        now = self.sm.clock()
        with self.engine.begin() as conn:
            entry = data_log.record(conn, case.case_id, case.patient_id, data_log.DataKind.STAFF_MESSAGE, text, now)
            execution_id = None
            if free:
                execution_id = f"EXEC-{uuid.uuid4().hex[:12]}"
                repository.insert_execution(conn, ExecutionRecord(
                    execution_id=execution_id, case_id=case.case_id, patient_id=case.patient_id,
                    action=Action.ANSWER_CLINICAL_QUESTION.value, step=case.current_step or 0,
                    retry_cycle=case.retry_cycle, attempt_number=0,
                    idempotency_key=f"{case.case_id}:message:{execution_id}", status="succeeded",
                    state_version=case.state_version, content_hash=entry.content_hash, medical_content_flag=True))
        payload: dict[str, Any] = {"content_hash": entry.content_hash}
        if free:
            payload["message_approval_id"] = self._grant_content_approval(
                case, reviewer_id, reviewer_role, reason, shown_context_ref, execution_id, entry.content_hash)
        return payload, entry.entry_id

    def _apply(self, case_id: str, event: Event, payload: dict[str, Any], entry_id: str | None) -> TransitionResult:
        """Apply a human event; a blocked one leaves no readable message behind (§12.3)."""
        result = self.sm.apply(case_id, event, payload, Component.EXTERNAL)
        if not result.committed:
            if entry_id is not None:
                self._tombstone_unauthorised(entry_id)
            raise DecisionRejected(result.reason or "blocked")
        self.wake()
        return result
```

At module level, after `allowed_decisions`, add:

```python
def _allowed(case: CaseRecord) -> list[str]:
    """Sub-project 15 (design §6): once a person has written to the patient, approve is gone."""
    return [d for d in allowed_decisions(case.escalation_kind) if not (d == "approve" and case.human_engaged)]


def _returned_by(trace: list[repository.AuditEntry]) -> str | None:
    """How the case last came back to review - a patient's reply, or a request that ran out of time."""
    back = [r for r in trace if r.record_type == "Transition" and r.state_after == State.AWAITING_HUMAN_REVIEW.value]
    if not back:
        return None
    if back[-1].event == Event.PATIENT_REPLY_SUBMITTED.value:
        return "patient_reply"
    if back[-1].event == Event.TIMEOUT_EXPIRED.value and back[-1].state_before == State.AWAITING_PATIENT_REPLY.value:
        return "reply_timeout"
    return None
```

- [ ] **Step 4: Run the tests**

Run: `$DC run --rm backend pytest tests/test_review_requests.py tests/test_human_review.py tests/test_clinical_answer.py tests/test_api_staff.py -v` (use whichever of these files exist).
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/hospital_agent/human_review.py backend/tests/test_review_requests.py
git commit -m "Let staff ask the patient and close with a message; drop approve once they have" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The Session Service — the patient's reply and what the patient sees

**Files:**
- Modify: `backend/hospital_agent/session.py`
- Create test: `backend/tests/test_patient_reply.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: `SessionService.reply_text(patient_id, case_id, text) -> TransitionResult`; `SessionService.reply_pdf(patient_id, case_id, data, filename) -> UploadOutcome` (codes as sub-project 13 plus `wrong_document_type`); exceptions `NotWaitingForReply`, `ReplyKindMismatch`; `ReplyRequest(kind, message, document_type, deadline)`; `ConversationEntry(sender, text, at)`; `PatientView.reply_request: ReplyRequest | None = None`, `PatientView.conversation: list[ConversationEntry] = []`; `closed` carries the closing message in `message`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_patient_reply.py`:

```python
"""The patient's side of sub-project 15 (design §7.5, §9, §10): replying, and what the patient sees."""
import pytest

from hospital_agent.document_intake import IntakeAnswer
from hospital_agent.human_review import HumanReviewService, NotInReview
from hospital_agent.naming import State
from hospital_agent.session import NotWaitingForReply, ReplyKindMismatch, SessionService
from tests.test_patient_request_fsm import escalated
from tests.test_pdf_upload import PDF, FakeIntake, accepted
from tests.test_review_requests import ADMIN, NURSE, ask


def setup(sm, app_engine, *answers):
    d = escalated(sm, app_engine)
    session = SessionService(sm, document_intake=FakeIntake(*answers))
    return d, session, HumanReviewService(sm, session)


def test_a_question_waits_for_a_text_reply_and_the_patient_sees_the_thread(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, template_id="clarify_appointment")
    view = session.patient_view(d.case_id)
    assert view.status == "needs_reply"
    assert view.reply_request.kind == "question"
    assert view.reply_request.message.startswith("האם הפנייה נוגעת לתור קיים")
    assert view.reply_request.deadline is not None
    session.reply_text(d.patient_id, d.case_id, "כן, התור ב־3 באוקטובר בקרדיולוגיה")
    view = session.patient_view(d.case_id)
    assert view.status == "in_review" and view.reply_request is None
    assert [(e.sender, e.text) for e in view.conversation] == [
        ("staff", "האם הפנייה נוגעת לתור קיים? אם כן, נא לציין את התאריך או את המחלקה."),
        ("patient", "כן, התור ב־3 באוקטובר בקרדיולוגיה"),
    ]


def test_a_reply_is_refused_when_nothing_was_asked_or_something_else_was(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    with pytest.raises(NotWaitingForReply):
        session.reply_text(d.patient_id, d.case_id, "hello")
    ask(reviews, d, kind="document", document_type="URINALYSIS")
    with pytest.raises(ReplyKindMismatch):
        session.reply_text(d.patient_id, d.case_id, "hello")
    with pytest.raises(NotInReview):  # the case waits for the patient, not for a reviewer
        ask(reviews, d, template_id="clarify_general")
```

Continue the file:

```python
def test_a_document_reply_must_be_the_requested_type(sm, app_engine):
    d, session, reviews = setup(sm, app_engine, accepted("CBC"), accepted("URINALYSIS"))
    ask(reviews, d, kind="document", document_type="URINALYSIS")
    wrong = session.reply_pdf(d.patient_id, d.case_id, PDF, "cbc.pdf")
    assert (wrong.code, wrong.document_type) == ("wrong_document_type", "CBC")
    assert d.state is State.AWAITING_PATIENT_REPLY
    right = session.reply_pdf(d.patient_id, d.case_id, PDF, "urine.pdf")
    assert (right.code, right.document_type) == ("accepted", "URINALYSIS")
    assert d.state is State.AWAITING_HUMAN_REVIEW
    view = session.patient_view(d.case_id)
    assert view.conversation[-1].sender == "patient"
    assert view.conversation[-1].text == "הועלה המסמך: בדיקת שתן"


def test_a_rejected_document_changes_nothing(sm, app_engine):
    d, session, reviews = setup(sm, app_engine, IntakeAnswer("DOCUMENT_UNREADABLE", None, None, None))
    ask(reviews, d, kind="document", document_type="URINALYSIS")
    assert session.reply_pdf(d.patient_id, d.case_id, PDF, "x.pdf").code == "unreadable"
    assert d.state is State.AWAITING_PATIENT_REPLY


def test_a_closing_template_is_shown_on_closed(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    reviews.decide(case_id=d.case_id, decision="resolve", reason="done",
                   shown_context_ref=reviews.context(d.case_id).shown_context_ref,
                   message={"template_id": "close_handled"}, **ADMIN)
    view = session.patient_view(d.case_id)
    assert (view.status, view.message) == ("closed", "פנייתך טופלה על ידי הצוות.")


def test_clinical_free_text_is_shown_because_its_approval_was_consumed(sm, app_engine):
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, NURSE, text="נא לציין אם יש לך רגישות ליוד")
    assert session.patient_view(d.case_id).reply_request.message == "נא לציין אם יש לך רגישות ליוד"


def test_a_staff_message_that_is_neither_a_template_nor_approved_is_never_shown(sm, app_engine):
    from datetime import UTC, datetime

    from hospital_agent import data_log
    d, session, reviews = setup(sm, app_engine)
    ask(reviews, d, template_id="clarify_general")
    with d.engine.begin() as conn:  # a forged entry whose hash is not on any committed row
        data_log.record(conn, d.case_id, d.patient_id, data_log.DataKind.STAFF_MESSAGE, "טקסט שלא אושר",
                        datetime.now(UTC))
    texts = [e.text for e in session.patient_view(d.case_id).conversation]
    assert "טקסט שלא אושר" not in texts
```

- [ ] **Step 2: Run them to see them fail**

Run: `$DC run --rm backend pytest tests/test_patient_reply.py -v`
Expected: FAIL — `ImportError: cannot import name 'NotWaitingForReply'`.

- [ ] **Step 3: Implement**

In `backend/hospital_agent/session.py`:

Imports: add `import re`; change `from dataclasses import dataclass` to `from dataclasses import dataclass, field`; add `patient_messages` to the `from . import …` line and `from .documents import CATALOG_LABELS, document_label` (keep existing imports).

After `class EventRejected` add:

```python
class NotWaitingForReply(Exception):
    """Sub-project 15: the case is not waiting for a reply (API 409 "not_waiting_for_reply")."""


class ReplyKindMismatch(Exception):
    """Sub-project 15: a text for a document request, or a file for a question (409 "reply_kind_mismatch")."""


@dataclass(frozen=True)
class ReplyRequest:
    """What the staff asked for, while the case waits for the patient (design §10)."""

    kind: str  # "question" | "document"
    message: str | None
    document_type: str | None
    deadline: datetime | None


@dataclass(frozen=True)
class ConversationEntry:
    sender: str  # "staff" | "patient"
    text: str
    at: datetime


MAX_REPLY_LENGTH = 2000
# The Data Log line of an accepted document reply - the same shape sub-project 13 records.
_DOCUMENT_REPLY = re.compile(r"^(\S+) (\S+) ACCEPTED$")
_STAFF_MESSAGE_EVENTS = frozenset({Event.PATIENT_REPLY_REQUESTED.value, Event.HUMAN_RESOLVED_CASE.value,
                                   Event.HUMAN_REJECTED.value})
```

In `PatientView`, after `document_upload: …`:

```python
    reply_request: ReplyRequest | None = None  # sub-project 15, while status is needs_reply
    conversation: list[ConversationEntry] = field(default_factory=list)  # staff messages and replies
```

In `SessionService`, after `upload_pdf` / `_waiting_case`, add:

```python
    # --- sub-project 15: the patient's reply to a staff request (design §9) ----------------

    def reply_text(self, patient_id: str, case_id: str, text: str) -> TransitionResult:
        case = self._replying_case(patient_id, case_id, "question")
        body = (text or "").strip()
        if not body or len(body) > MAX_REPLY_LENGTH:
            raise EventRejected("reply_required")
        entry = self._record(case, data_log.DataKind.PATIENT_REPLY, body)
        return self._submit_reply(case_id, entry, {"reply_kind": "question"})

    def reply_pdf(self, patient_id: str, case_id: str, data: bytes, filename: str) -> UploadOutcome:
        """The requested document, through the sub-project 13 intake; it counts only when its type
        is the one asked for. The PDF stays in the document-service; the log gets only the code."""
        self._replying_case(patient_id, case_id, "document")
        if self.document_intake is None:
            raise IntakeUnavailable("not_configured")
        try:
            answer = self.document_intake.submit(patient_id, filename, data)
        except IntakeUnavailable as unavailable:
            logger.info("pdf reply: document_service_unavailable (%s)", unavailable)
            raise
        document_type, document_ref = _effective(answer)
        if document_type is None or document_ref is None:
            outcome = UploadOutcome(_REJECTION_CODES.get(answer.result, _UNREADABLE), None)
        else:
            case = self._replying_case(patient_id, case_id, "document")  # it may have moved meanwhile
            if document_type != case.requested_document:
                outcome = UploadOutcome("wrong_document_type", document_type)
            else:
                entry = self._record(case, data_log.DataKind.PATIENT_REPLY, f"{document_ref} {document_type} ACCEPTED")
                try:
                    self._submit_reply(case_id, entry, {"reply_kind": "document", "document_type": document_type})
                except EventRejected:
                    logger.info("pdf reply: not_waiting_for_reply")
                    raise NotWaitingForReply(case_id) from None
                outcome = UploadOutcome("accepted", document_type)
        logger.info("pdf reply: %s", outcome.code)
        return outcome

    def _replying_case(self, patient_id: str, case_id: str, kind: str) -> CaseRecord:
        case = self.case_for_patient(patient_id, case_id)
        if case.state is not State.AWAITING_PATIENT_REPLY:
            raise NotWaitingForReply(case_id)
        if case.reply_kind != kind:
            raise ReplyKindMismatch(case_id)
        return case

    def _submit_reply(self, case_id: str, entry: data_log.DataEntry, payload: dict) -> TransitionResult:
        result = self.sm.apply(case_id, Event.PATIENT_REPLY_SUBMITTED, {**payload, "content_hash": entry.content_hash},
                               Component.SESSION_SERVICE)
        if not result.committed:
            with self.engine.begin() as conn:  # §12.3: a refused reply must not stay readable
                data_log.tombstone(conn, entry.entry_id, self.sm.clock())
            raise EventRejected(result.reason or "blocked")
        return result
```

In `_view`, inside the `with self.engine.connect() as conn:` block, after the `message = …` statement add:

```python
            staff = self._staff_messages(conn, case.case_id, trace)
            replies = self._patient_replies(conn, case.case_id, trace)
```

After the block (before `needs_document = …`) add:

```python
        committed = [r for r in trace if r.record_type == "Transition"]
        reply_request = None
        if case.state is State.AWAITING_PATIENT_REPLY:
            asked = [r.content_hash for r in committed if r.event == Event.PATIENT_REPLY_REQUESTED.value]
            text = next((m.text for m in reversed(staff) if asked and m.content_hash == asked[-1]), None)
            reply_request = ReplyRequest(case.reply_kind, text, case.requested_document, case.patient_deadline)
        if status == "closed" and message is None:
            closing = [r.content_hash for r in committed if r.content_hash and r.event in
                       (Event.HUMAN_RESOLVED_CASE.value, Event.HUMAN_REJECTED.value)]
            message = next((m.text for m in reversed(staff) if closing and m.content_hash == closing[-1]), None)
        conversation = sorted([ConversationEntry("staff", m.text, m.at) for m in staff]
                              + [ConversationEntry("patient", r.text, r.at) for r in replies], key=lambda e: e.at)
```

and pass `reply_request=reply_request, conversation=conversation,` to the `PatientView(...)` call.

Add the two helpers next to `_clinical_answer`:

```python
    @dataclass(frozen=True)
    class _Message:
        content_hash: str
        text: str
        at: datetime

    def _staff_messages(self, conn, case_id: str, trace) -> list[_Message]:
        """Staff messages the patient may read (design §7.5): the hash is on a committed request /
        resolve / reject row, and the text is a template's or has a consumed clinical approval."""
        committed = {r.content_hash for r in trace
                     if r.record_type == "Transition" and r.content_hash and r.event in _STAFF_MESSAGE_EVENTS}
        approved = {a.content_hash for a in repository.content_approvals_for(
                        conn, case_id, naming.Action.ANSWER_CLINICAL_QUESTION.value)
                    if a.approval_type == "ContentApproval" and a.reviewer_role == auth.CLINICAL_STAFF
                    and a.consumed_at is not None and a.content_hash}
        return [self._Message(e.content_hash, e.content, e.created_at)
                for e in data_log.entries(conn, case_id, data_log.DataKind.STAFF_MESSAGE)
                if e.content is not None and e.content_hash in committed
                and (patient_messages.is_template_text(e.content) or e.content_hash in approved)]

    def _patient_replies(self, conn, case_id: str, trace) -> list[_Message]:
        """The patient's own replies whose PATIENT_REPLY_SUBMITTED committed; a document reply is
        shown as the document's label, never its reference line."""
        committed = {r.content_hash for r in trace if r.record_type == "Transition"
                     and r.event == Event.PATIENT_REPLY_SUBMITTED.value and r.content_hash}
        replies = []
        for e in data_log.entries(conn, case_id, data_log.DataKind.PATIENT_REPLY):
            if e.content is None or e.content_hash not in committed:
                continue
            match = _DOCUMENT_REPLY.match(e.content)
            text = (f"הועלה המסמך: {document_label(match.group(2))}"
                    if match and match.group(2) in CATALOG_LABELS else e.content)
            replies.append(self._Message(e.content_hash, text, e.created_at))
        return replies
```

(If `naming`, `auth` or `repository` are not already module imports in `session.py`, check how `_clinical_answer` references them and use the same names.)

- [ ] **Step 4: Run the tests**

Run: `$DC run --rm backend pytest tests/test_patient_reply.py tests/test_session.py tests/test_pdf_upload.py tests/test_api_patient.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/hospital_agent/session.py backend/tests/test_patient_reply.py
git commit -m "Let the patient reply by text or the requested PDF, and show the thread" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The API

**Files:**
- Modify: `backend/hospital_agent/api/schemas.py`, `backend/hospital_agent/api/routes_staff.py`, `backend/hospital_agent/api/routes_patient.py`, `backend/hospital_agent/api/app.py`
- Modify: `docs/api.md`, `docs/superpowers/specs/2026-09-25-patient-requests-design.md` (§10 status codes)
- Create test: `backend/tests/test_api_patient_requests.py`

**Interfaces:**
- Produces: `GET /api/staff/message-templates`; `POST /api/staff/cases/{id}/request`; `DecisionRequest.message`; `ReviewItem.human_engaged`, `returned_by`; `PatientCaseView.reply_request`, `conversation`; `POST /api/patient/requests/{id}/reply`; `POST /api/patient/requests/{id}/reply/file`; `UploadResult.code` gains `wrong_document_type`.
- Status codes follow the existing decision route: refusals from the service are `409 {detail}`, `clinical_staff_only` is `403`, schema failures are the app's `422 invalid_body`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_api_patient_requests.py`:

```python
"""Sub-project 15's routes (design §10)."""
import pytest
from fastapi.testclient import TestClient

from hospital_agent.api.app import create_app
from hospital_agent.auth import demo_password
from tests.test_patient_request_fsm import escalated
from tests.test_pdf_upload import PDF, FakeIntake, accepted

NURSE, ADMIN, PATIENT = "coordinator_nurse", "admin_coordinator", "P-10041"


@pytest.fixture
def intake():
    return FakeIntake(accepted("URINALYSIS"))


@pytest.fixture
def client(app_engine, intake):
    with TestClient(create_app(app_engine, document_intake=intake)) as test_client:
        yield test_client


def auth(client, user_id):
    token = client.post("/api/auth/login", json={"user_id": user_id, "password": demo_password()}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def context_ref(client, case_id, headers):
    return client.get(f"/api/staff/cases/{case_id}/context", headers=headers).json()["shown_context_ref"]


def test_templates_are_served_to_staff_only(client):
    assert client.get("/api/staff/message-templates", headers=auth(client, PATIENT)).status_code == 403
    body = client.get("/api/staff/message-templates", headers=auth(client, ADMIN)).json()
    assert {"template_id", "purpose", "text", "param", "options"} == set(body[0])


def test_a_question_round_trip(client, sm, app_engine):
    d = escalated(sm, app_engine)
    staff = auth(client, ADMIN)
    response = client.post(f"/api/staff/cases/{d.case_id}/request", headers=staff, json={
        "kind": "question", "template_id": "clarify_general", "reason": "unclear",
        "shown_context_ref": context_ref(client, d.case_id, staff)})
    assert response.status_code == 200 and response.json()["state"] == "AwaitingPatientReply"
    patient = auth(client, PATIENT)
    view = client.get(f"/api/patient/requests/{d.case_id}", headers=patient).json()
    assert view["status"] == "needs_reply" and view["reply_request"]["kind"] == "question"
    replied = client.post(f"/api/patient/requests/{d.case_id}/reply", headers=patient, json={"text": "תור לאורתופדיה"})
    assert replied.status_code == 200 and replied.json()["status"] == "in_review"
    item = next(i for i in client.get("/api/staff/reviews", headers=staff).json() if i["case_id"] == d.case_id)
    assert item["returned_by"] == "patient_reply" and "approve" not in item["allowed_decisions"]


def test_free_text_from_admin_staff_is_403(client, sm, app_engine):
    d = escalated(sm, app_engine)
    staff = auth(client, ADMIN)
    response = client.post(f"/api/staff/cases/{d.case_id}/request", headers=staff, json={
        "kind": "question", "text": "נא לפרט", "reason": "unclear",
        "shown_context_ref": context_ref(client, d.case_id, staff)})
    assert (response.status_code, response.json()["detail"]) == (403, "clinical_staff_only")


def test_a_document_round_trip_through_the_file_route(client, sm, app_engine):
    d = escalated(sm, app_engine)
    staff = auth(client, NURSE)
    client.post(f"/api/staff/cases/{d.case_id}/request", headers=staff, json={
        "kind": "document", "document_type": "URINALYSIS", "reason": "need urine test",
        "shown_context_ref": context_ref(client, d.case_id, staff)})
    patient = auth(client, PATIENT)
    assert client.post(f"/api/patient/requests/{d.case_id}/reply", headers=patient,
                       json={"text": "hi"}).json()["detail"] == "reply_kind_mismatch"
    uploaded = client.post(f"/api/patient/requests/{d.case_id}/reply/file", headers=patient,
                           files={"file": ("urine.pdf", PDF, "application/pdf")})
    assert uploaded.status_code == 200
    assert uploaded.json()["upload"] == {"code": "accepted", "document_type": "URINALYSIS"}
    assert uploaded.json()["request"]["status"] == "in_review"


def test_a_closing_message_through_the_decision_route(client, sm, app_engine):
    d = escalated(sm, app_engine)
    staff = auth(client, ADMIN)
    response = client.post(f"/api/staff/cases/{d.case_id}/decision", headers=staff, json={
        "decision": "reject", "reason": "out of scope", "shown_context_ref": context_ref(client, d.case_id, staff),
        "message": {"template_id": "close_out_of_scope"}})
    assert response.status_code == 200
    view = client.get(f"/api/patient/requests/{d.case_id}", headers=auth(client, PATIENT)).json()
    assert view["status"] == "closed" and view["message"].startswith("פנייתך אינה בתחום")


def test_replying_when_nothing_was_asked_is_409(client, sm, app_engine):
    d = escalated(sm, app_engine)
    response = client.post(f"/api/patient/requests/{d.case_id}/reply", headers=auth(client, PATIENT),
                           json={"text": "hi"})
    assert (response.status_code, response.json()["detail"]) == (409, "not_waiting_for_reply")
```

- [ ] **Step 2: Run them to see them fail**

Run: `$DC run --rm backend pytest tests/test_api_patient_requests.py -v`
Expected: FAIL — 404 on the new routes.

- [ ] **Step 3: Schemas**

In `backend/hospital_agent/api/schemas.py`:

After `PatientStatusChange` add:

```python
class ReplyRequestView(BaseModel):
    """Sub-project 15: what the staff asked for (design §10). Never a reason or an escalation kind."""

    model_config = ConfigDict(from_attributes=True)

    kind: Literal["question", "document"]
    message: str | None
    document_type: str | None
    deadline: datetime | None


class ConversationEntryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sender: Literal["staff", "patient"]
    text: str
    at: datetime
```

In `PatientCaseView`, after `document_upload: …`:

```python
    reply_request: ReplyRequestView | None
    conversation: list[ConversationEntryView]
```

In `UploadResult.code`, add `"wrong_document_type"` to the `Literal[...]`.

In `ReviewItem`, after `updated_at: datetime`:

```python
    human_engaged: bool
    returned_by: Literal["patient_reply", "reply_timeout"] | None
```

Before `class DecisionRequest` add:

```python
class MessageBody(BaseModel):
    """Sub-project 15 (design §7.2): a template (with its parameter) or clinical free text -
    the Human Review Service judges which, so the reason code reaches the reviewer."""

    template_id: str | None = Field(default=None, max_length=64)
    param: str | None = Field(default=None, max_length=64)
    text: str | None = Field(default=None, max_length=2000)
```

In `DecisionRequest`, after `patient_deadline: …`:

```python
    message: MessageBody | None = None  # sub-project 15: a closing message on resolve / reject
```

After `DecisionResponse` add:

```python
class PatientRequestBody(MessageBody):
    """Sub-project 15 (design §10): POST /api/staff/cases/{id}/request."""

    kind: str = Field(max_length=16)
    reason: str = Field(max_length=2000)
    shown_context_ref: str = Field(max_length=200)
    document_type: str | None = Field(default=None, max_length=64)
    deadline: AwareDatetime | None = None


class MessageTemplateView(BaseModel):
    template_id: str
    purpose: Literal["question", "document", "closing"]
    text: str
    param: str | None
    options: dict[str, str]


class ReplyBody(BaseModel):
    text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
```

- [ ] **Step 4: Staff routes**

In `backend/hospital_agent/api/routes_staff.py`, add `MessageTemplateView` and `PatientRequestBody` to the `.schemas` import. In `decide`, pass `message=body.message.model_dump() if body.message else None,` to `reviews.decide(...)`, and change its `except DecisionRejected` to

```python
    except DecisionRejected as rejected:
        status = 403 if rejected.reason == "clinical_staff_only" else 409
        raise HTTPException(status_code=status, detail=rejected.reason) from None
```

After `decide` add:

```python
@router.get("/message-templates", response_model=list[MessageTemplateView])
def message_templates(reviews: HumanReviewService = Depends(get_reviews)) -> list[MessageTemplateView]:
    """Sub-project 15: the fixed messages (design §7.1) - the UI keeps no copy of them."""
    return [MessageTemplateView.model_validate(template) for template in reviews.templates()]


@router.post("/cases/{case_id}/request", response_model=DecisionResponse)
def request_from_patient(case_id: str, body: PatientRequestBody, principal: Principal = Depends(require_staff),
                         reviews: HumanReviewService = Depends(get_reviews)) -> DecisionResponse:
    """Sub-project 15 (design §5, §7): ask the patient a question or for one catalog document."""
    try:
        reviews.request(
            reviewer_id=principal.user_id, reviewer_role=principal.role, case_id=case_id, kind=body.kind,
            reason=body.reason, shown_context_ref=body.shown_context_ref, template_id=body.template_id,
            param=body.param, text=body.text, document_type=body.document_type, deadline=body.deadline)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    except NotInReview:
        raise HTTPException(status_code=409, detail="not_in_review") from None
    except ContextChanged:
        raise HTTPException(status_code=409, detail="context_changed") from None
    except DecisionRejected as rejected:
        status = 403 if rejected.reason == "clinical_staff_only" else 409
        raise HTTPException(status_code=status, detail=rejected.reason) from None
    return DecisionResponse(case_id=case_id, state=reviews.sm.load(case_id).state.value)
```

- [ ] **Step 5: Patient routes and the upload size limit**

In `backend/hospital_agent/api/routes_patient.py`: import `NotWaitingForReply`, `ReplyKindMismatch` from `..session` and `ReplyBody` from `.schemas`. After `upload_pdf` add:

```python
@router.post("/requests/{case_id}/reply", response_model=PatientCaseView)
def reply(case_id: str, body: ReplyBody, principal: Principal = Depends(require_patient),
          session: SessionService = Depends(get_session)) -> PatientCaseView:
    """Sub-project 15 (design §9): the patient's text answer to a staff question."""
    try:
        session.reply_text(principal.patient_id, case_id, body.text)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    except NotWaitingForReply:
        raise HTTPException(status_code=409, detail="not_waiting_for_reply") from None
    except ReplyKindMismatch:
        raise HTTPException(status_code=409, detail="reply_kind_mismatch") from None
    except EventRejected as rejected:
        raise HTTPException(status_code=409, detail=rejected.reason) from None
    return PatientCaseView.model_validate(session.patient_view(case_id))


@router.post("/requests/{case_id}/reply/file", response_model=PdfUploadResponse)
async def reply_pdf(case_id: str, request: Request, principal: Principal = Depends(require_patient),
                    session: SessionService = Depends(get_session)) -> PdfUploadResponse:
    """Sub-project 15 (design §9): the requested document, as the sub-project 13 upload does it."""
    if session.document_intake is None:
        raise HTTPException(status_code=404, detail="file_upload_not_enabled")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > UPLOAD_BODY_LIMIT:
            raise HTTPException(status_code=413, detail="too_large")
    part = await run_in_threadpool(_file_part, request.headers.get("content-type", ""), bytes(body))
    if part is None:
        raise HTTPException(status_code=422, detail="invalid_body")
    filename, data = part
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="too_large")
    try:
        outcome = await run_in_threadpool(session.reply_pdf, principal.patient_id, case_id, data, filename)
        view = await run_in_threadpool(session.patient_view, case_id)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    except NotWaitingForReply:
        raise HTTPException(status_code=409, detail="not_waiting_for_reply") from None
    except ReplyKindMismatch:
        raise HTTPException(status_code=409, detail="reply_kind_mismatch") from None
    except IntakeUnavailable:
        raise HTTPException(status_code=503, detail="document_service_unavailable") from None
    return PdfUploadResponse(upload=UploadResult.model_validate(outcome),
                             request=PatientCaseView.model_validate(view))
```

In `backend/hospital_agent/api/app.py`, `UploadSizeLimit`: change `scope["path"].endswith("/documents/file")` to `scope["path"].endswith(("/documents/file", "/reply/file"))`, and its docstring's first line to mention `.../reply/file` too.

- [ ] **Step 6: Documentation**

In `docs/api.md`: add to the route table (section 2) after the patient `documents/file` row:

```markdown
| POST | `/api/patient/requests/{case_id}/reply` | patient | Answer a staff question (sub-project 15) |
| POST | `/api/patient/requests/{case_id}/reply/file` | patient | Upload the PDF a staff member asked for (sub-project 15) |
```

and after the staff `answer` row:

```markdown
| GET | `/api/staff/message-templates` | staff | The fixed staff messages (sub-project 15) |
| POST | `/api/staff/cases/{case_id}/request` | staff | Ask the patient a question or for a document (sub-project 15) |
```

Append a section `## 8. Staff requests to the patient (sub-project 15)` documenting: the patient view's new status `needs_reply` and the fields `reply_request` (`kind`, `message`, `document_type`, `deadline`) and `conversation` (`sender`, `text`, `at`), and `message` on `closed`; the two patient routes and their codes (`409 not_waiting_for_reply`, `409 reply_kind_mismatch`, the upload codes of §4 plus `wrong_document_type`); the templates route; the request route's body (`kind`, `template_id`, `param`, `text`, `document_type`, `deadline`, `reason`, `shown_context_ref`) and codes (`403 clinical_staff_only`; `409` with `invalid_request`, `invalid_template`, `unknown_template`, `unexpected_param`, `invalid_param`, `message_required`, `document_service_not_configured`, `invalid_deadline`, `context_changed`, `not_in_review`); the decision route's optional `message` (`message_not_allowed` on approve, `human_engaged` when approve is gone); and `ReviewItem`'s `human_engaged` / `returned_by`. Take every field name and code from the code you wrote, not from this summary.

In the design doc §10, replace "`422 invalid_request` / `invalid_deadline`" with "`409 invalid_request` / `409 invalid_deadline`, like the decision route's service refusals".

- [ ] **Step 7: Run the tests**

Run: `$DC run --rm backend pytest tests/test_api_patient_requests.py tests/test_api_staff.py tests/test_api_patient.py tests/test_api.py -v`
Expected: all pass. Then the whole suite once: `$DC run --rm backend pytest -q`.

- [ ] **Step 8: Commit**

```bash
git add backend/hospital_agent/api backend/tests/test_api_patient_requests.py docs/api.md docs/superpowers/specs/2026-09-25-patient-requests-design.md
git commit -m "Expose staff requests, closing messages and patient replies in the API" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Metrics (sub-project 14)

**Files:**
- Modify: `backend/hospital_agent/metrics.py`, `backend/tests/test_metrics_human.py`, `docs/superpowers/specs/2026-09-24-admin-metrics-design.md`

**Interfaces:**
- Produces: `HumanLoad.decisions` gains the key `PATIENT_REPLY_REQUESTED`; `by_state` already carries `AwaitingPatientReply` (raw states).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_metrics_human.py`:

```python
def test_a_request_to_the_patient_is_counted_with_the_staff_decisions(app_engine):
    with app_engine.begin() as conn:
        add_case(conn, "C-1", created_at=at(-5), state="AwaitingPatientReply")
        add_row(conn, "C-1", "MEDICAL_QUESTION_DETECTED", at=at(0), before="Classifying", after=REVIEW)
        add_row(conn, "C-1", "PATIENT_REPLY_REQUESTED", at=at(15), before=REVIEW, after="AwaitingPatientReply")
    with app_engine.connect() as conn:
        load = metrics.human_load(conn, WINDOW)
    assert load.decisions["PATIENT_REPLY_REQUESTED"] == 1
    assert load.time_to_decision.count == 1  # the first human action ends the wait (design §13)
```

Also update `test_an_empty_database_has_no_load`'s expected `decisions` to include `"PATIENT_REPLY_REQUESTED": 0`.

- [ ] **Step 2: Run it to see it fail**

Run: `$DC run --rm backend pytest tests/test_metrics_human.py -v`
Expected: FAIL — `KeyError: 'PATIENT_REPLY_REQUESTED'`.

- [ ] **Step 3: Count it**

In `backend/hospital_agent/metrics.py`, `human_load`, after the `decisions.update(...)` statement:

```python
    # Sub-project 15: a request to the patient is a staff action too. It is not a decision on the
    # escalation, so decided_by_kind (the approval join) leaves it out.
    decisions["PATIENT_REPLY_REQUESTED"] = int(conn.execute(text(f"""
        SELECT count(*) FROM audit_log
        WHERE record_type = 'Transition' AND event = 'PATIENT_REPLY_REQUESTED' AND {_EVENTS}"""),
        params).scalar_one())
```

In `docs/superpowers/specs/2026-09-24-admin-metrics-design.md` §4.2, B3's row: append "מאז תת־פרויקט 15 היציאה הראשונה יכולה להיות בקשה מהמטופל, ולכן המדד הוא הזמן עד הפעולה האנושית הראשונה." and B4's row: append "וגם `PATIENT_REPLY_REQUESTED` (תת־פרויקט 15)."

- [ ] **Step 4: Run the tests**

Run: `$DC run --rm backend pytest tests/test_metrics_human.py tests/test_metrics_compute.py tests/test_api_admin.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/hospital_agent/metrics.py backend/tests/test_metrics_human.py docs/superpowers/specs/2026-09-24-admin-metrics-design.md
git commit -m "Count a request to the patient with the staff decisions in the metrics" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Frontend data layer

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/api/client.test.ts`, `frontend/src/components/StatusPill.tsx`, `frontend/src/pages/patient/helpers.ts`, `frontend/src/pages/staff/labels.ts`, `frontend/src/pages/staff/metricsLabels.ts`

**Interfaces:**
- Produces (types): `State` gains `'AwaitingPatientReply'`; `PatientStatus` gains `'needs_reply'`; `PatientView.reply_request: ReplyRequest | null`, `PatientView.conversation: ConversationEntry[]`; `UploadCode` gains `'wrong_document_type'`; `ReviewItem.human_engaged: boolean`, `ReviewItem.returned_by: 'patient_reply' | 'reply_timeout' | null`; `DecisionBody.message?: MessageBody`; new `MessageBody`, `MessageTemplate`, `PatientRequestBody`, `ReplyRequest`, `ConversationEntry`.
- Produces (client): `getMessageTemplates(): Promise<MessageTemplate[]>`, `requestFromPatient(caseId, body: PatientRequestBody): Promise<DecisionResult>`, `replyToRequest(caseId, text): Promise<PatientView>`, `replyWithFile(caseId, file: File): Promise<PdfUploadResponse>`.
- Produces (labels): `STATE_LABELS.AwaitingPatientReply = 'ממתינה לתשובת המטופל'`; `STATUS_LABELS.needs_reply = 'ממתינה לתשובתך'`; `STATUS_TEXT.needs_reply = { title: 'ממתינה לתשובתך', note: 'איש צוות ביקש ממך פרט נוסף או מסמך.' }`; `DATA_KIND_LABELS.staff_message = 'הודעת צוות למטופל'`, `DATA_KIND_LABELS.patient_reply = 'תשובת המטופל'`; `RETURNED_BY_LABELS = { patient_reply: 'התקבלה תשובת מטופל', reply_timeout: 'לא נענתה בזמן' }`; `EVENT_LABELS.PATIENT_REPLY_REQUESTED = 'נשלחה בקשה למטופל'` (metrics).

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/api/client.test.ts`:

```ts
describe('patient requests (sub-project 15)', () => {
  it('posts a staff request to the case', async () => {
    mockOnce(200, { case_id: 'C-1', state: 'AwaitingPatientReply' })
    await api.requestFromPatient('C-1', {
      kind: 'question',
      template_id: 'clarify_general',
      reason: 'unclear',
      shown_context_ref: 'ref-1',
    })
    const [url, init] = lastCall()
    expect(url).toBe('/api/staff/cases/C-1/request')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toMatchObject({ kind: 'question', template_id: 'clarify_general' })
  })

  it('posts the patient text reply', async () => {
    mockOnce(200, {})
    await api.replyToRequest('C-1', 'כן')
    const [url, init] = lastCall()
    expect(url).toBe('/api/patient/requests/C-1/reply')
    expect(JSON.parse(init.body as string)).toEqual({ text: 'כן' })
  })

  it('posts the requested PDF as multipart', async () => {
    mockOnce(200, {})
    await api.replyWithFile('C-1', new File(['%PDF'], 'a.pdf', { type: 'application/pdf' }))
    const [url, init] = lastCall()
    expect(url).toBe('/api/patient/requests/C-1/reply/file')
    expect(init.body).toBeInstanceOf(FormData)
  })

  it('reads the message templates', async () => {
    mockOnce(200, [])
    await api.getMessageTemplates()
    expect(lastCall()[0]).toBe('/api/staff/message-templates')
  })
})
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && npm test -- src/api/client.test.ts`
Expected: FAIL — `api.requestFromPatient is not a function`.

- [ ] **Step 3: Types**

In `frontend/src/api/types.ts`:
- In `STATES`, add `'AwaitingPatientReply',` after `'Failed',` with the comment `// sub-project 15's extension (docs/api.md §8)`.
- In `PatientStatus`, add `| 'needs_reply'` after `| 'needs_document'`.
- In `PatientView`, change the `message` doc to `/** The delivered message iff \`status === 'completed'\`; the closing message, if any, when \`closed\`. */` and after `document_upload` add:

```ts
  /** Sub-project 15: what the staff asked for, iff `status === 'needs_reply'`. */
  reply_request: ReplyRequest | null
  /** Staff messages the patient may see and the patient's replies, oldest first. */
  conversation: ConversationEntry[]
```

- In `UploadCode`, add `| 'wrong_document_type'`.
- In `ReviewItem`, add:

```ts
  /** Sub-project 15: a person has written to the patient - `approve` is never offered again. */
  human_engaged: boolean
  /** How the case last came back to review. */
  returned_by: 'patient_reply' | 'reply_timeout' | null
```

- In `DecisionBody`, add `/** Sub-project 15: a closing message, on resolve / reject only. */ message?: MessageBody`.
- Append:

```ts
// ---- Staff requests to the patient (sub-project 15, docs/api.md §8) --------

/** A fixed template (with its parameter) or clinical staff's free text - exactly one. */
export interface MessageBody {
  template_id?: string
  param?: string
  text?: string
}

export interface MessageTemplate {
  template_id: string
  purpose: 'question' | 'document' | 'closing'
  text: string
  /** The `{placeholder}` the text takes, if any. */
  param: string | null
  /** The closed list for `param`: code -> Hebrew. */
  options: Record<string, string>
}

export interface PatientRequestBody extends MessageBody {
  kind: 'question' | 'document'
  reason: string
  shown_context_ref: string
  document_type?: string
  /** ISO datetime with a timezone offset; the server defaults to 24 hours. */
  deadline?: IsoDateTime
}

export interface ReplyRequest {
  kind: 'question' | 'document'
  message: string | null
  document_type: string | null
  deadline: IsoDateTime | null
}

export interface ConversationEntry {
  sender: 'staff' | 'patient'
  text: string
  at: IsoDateTime
}
```

- [ ] **Step 4: Client**

In `frontend/src/api/client.ts`, add `ConversationEntry`-free imports only as needed: `MessageTemplate`, `PatientRequestBody` to the `import type` list. Append:

```ts
// ---- Staff requests to the patient (sub-project 15) ------------------------

export function getMessageTemplates(): Promise<MessageTemplate[]> {
  return request<MessageTemplate[]>('GET', '/staff/message-templates')
}

export function requestFromPatient(caseId: string, body: PatientRequestBody): Promise<DecisionResult> {
  return request<DecisionResult>('POST', `/staff/cases/${id(caseId)}/request`, { body })
}

export function replyToRequest(caseId: string, text: string): Promise<PatientView> {
  return request<PatientView>('POST', `/patient/requests/${id(caseId)}/reply`, { body: { text } })
}

export function replyWithFile(caseId: string, file: File): Promise<PdfUploadResponse> {
  const form = new FormData()
  form.append('file', file)
  return request<PdfUploadResponse>('POST', `/patient/requests/${id(caseId)}/reply/file`, { body: form })
}
```

(`id` is the file's existing path-segment encoder used by `getCase` — check its name there and use the same.)

- [ ] **Step 5: Labels**

- `frontend/src/components/StatusPill.tsx` `STATUS_LABELS`: add `needs_reply: 'ממתינה לתשובתך',`. Then find how `pill-needs_document` is styled in `frontend/src/styles/app.css` and give `.pill-needs_reply` the same look — adding a new selector, never redeclaring an existing one (if the existing rule is a selector list you may not extend it; add a separate `.pill-needs_reply` rule with the same declarations).
- `frontend/src/pages/patient/helpers.ts` `STATUS_TEXT`: add `needs_reply: { title: 'ממתינה לתשובתך', note: 'איש צוות ביקש ממך פרט נוסף או מסמך.' },`.
- `frontend/src/pages/staff/labels.ts`: `STATE_LABELS` add `AwaitingPatientReply: 'ממתינה לתשובת המטופל',`; `DATA_KIND_LABELS` add `staff_message: 'הודעת צוות למטופל',` and `patient_reply: 'תשובת המטופל',`; append

```ts
/** Sub-project 15: how a case last came back to the review queue. */
export const RETURNED_BY_LABELS: Record<'patient_reply' | 'reply_timeout', string> = {
  patient_reply: 'התקבלה תשובת מטופל',
  reply_timeout: 'לא נענתה בזמן',
}
```

- `frontend/src/pages/staff/metricsLabels.ts` `EVENT_LABELS`: add `PATIENT_REPLY_REQUESTED: 'נשלחה בקשה למטופל',`.

- [ ] **Step 6: Run the tests and the build**

Run: `cd frontend && npm test && npm run build`
Expected: all pass; the build finds every `Record<PatientStatus|State, …>` complete. Fix any fixture that now lacks `reply_request` / `conversation` / `human_engaged` / `returned_by` by adding `reply_request: null, conversation: []` / `human_engaged: false, returned_by: null` to it.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "Add the request and reply types, client calls and labels" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The staff screen — request panel, closing message, queue mark

**Files:**
- Create: `frontend/src/pages/staff/PatientRequest.tsx`, `frontend/src/pages/staff/PatientRequest.test.tsx`
- Modify: `frontend/src/pages/staff/ReviewCase.tsx`, `frontend/src/pages/staff/ReviewCase.test.tsx`, `frontend/src/pages/staff/ReviewQueue.tsx`, `frontend/src/pages/staff/ReviewQueue.test.tsx`

**Interfaces:**
- Consumes: Task 10.
- Produces: `PatientRequest({ caseId, shownContextRef, role, templates, onSent, onContextChanged })`; `MessagePicker({ purpose, role, templates, value, onChange })` exported from the same file; `MessageChoice = { mode: 'none' } | { mode: 'template'; template_id: string; param?: string } | { mode: 'text'; text: string }` and `toMessageBody(choice): MessageBody | undefined`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/pages/staff/PatientRequest.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import type { MessageTemplate } from '../../api/types'
import { PatientRequest } from './PatientRequest'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  requestFromPatient: vi.fn(),
}))

const TEMPLATES: MessageTemplate[] = [
  { template_id: 'clarify_general', purpose: 'question', text: 'לא הצלחנו להבין', param: null, options: {} },
  {
    template_id: 'clarify_did_you_mean',
    purpose: 'question',
    text: 'האם התכוונת ל{topic}?',
    param: 'topic',
    options: { preparation: 'הוראות ההכנה לתור' },
  },
  {
    template_id: 'document_request',
    purpose: 'document',
    text: 'נא להעלות את המסמך: {document}.',
    param: 'document',
    options: { URINALYSIS: 'בדיקת שתן' },
  },
  { template_id: 'close_handled', purpose: 'closing', text: 'טופלה', param: null, options: {} },
]

function renderPanel(role: 'clinical_staff' | 'admin_staff' = 'admin_staff') {
  const onSent = vi.fn()
  render(
    <PatientRequest
      caseId="C-1"
      shownContextRef="ref-1"
      role={role}
      templates={TEMPLATES}
      onSent={onSent}
      onContextChanged={vi.fn()}
    />,
  )
  return { onSent }
}

describe('PatientRequest', () => {
  it('sends a question template with its parameter', async () => {
    vi.mocked(api.requestFromPatient).mockResolvedValue({ case_id: 'C-1', state: 'AwaitingPatientReply' })
    const { onSent } = renderPanel()
    await userEvent.selectOptions(screen.getByLabelText('הודעה'), 'clarify_did_you_mean')
    await userEvent.selectOptions(screen.getByLabelText('נושא'), 'preparation')
    await userEvent.type(screen.getByLabelText('סיבה (פנימית)'), 'unclear')
    await userEvent.click(screen.getByRole('button', { name: 'שליחה למטופל' }))
    expect(api.requestFromPatient).toHaveBeenCalledWith(
      'C-1',
      expect.objectContaining({
        kind: 'question',
        template_id: 'clarify_did_you_mean',
        param: 'preparation',
        reason: 'unclear',
        shown_context_ref: 'ref-1',
      }),
    )
    expect(onSent).toHaveBeenCalled()
  })

  it('offers free text to clinical staff only', () => {
    renderPanel('admin_staff')
    expect(screen.queryByRole('option', { name: 'טקסט חופשי (צוות קליני)' })).not.toBeInTheDocument()
  })

  it('lets clinical staff write free text', async () => {
    vi.mocked(api.requestFromPatient).mockResolvedValue({ case_id: 'C-1', state: 'AwaitingPatientReply' })
    renderPanel('clinical_staff')
    await userEvent.selectOptions(screen.getByLabelText('הודעה'), 'text')
    await userEvent.type(screen.getByLabelText('הטקסט למטופל'), 'נא לפרט')
    await userEvent.type(screen.getByLabelText('סיבה (פנימית)'), 'r')
    await userEvent.click(screen.getByRole('button', { name: 'שליחה למטופל' }))
    expect(api.requestFromPatient).toHaveBeenCalledWith('C-1', expect.objectContaining({ text: 'נא לפרט' }))
  })

  it('asks for a catalog document', async () => {
    vi.mocked(api.requestFromPatient).mockResolvedValue({ case_id: 'C-1', state: 'AwaitingPatientReply' })
    renderPanel()
    await userEvent.click(screen.getByRole('radio', { name: 'בקשת מסמך' }))
    await userEvent.selectOptions(screen.getByLabelText('סוג המסמך'), 'URINALYSIS')
    await userEvent.type(screen.getByLabelText('סיבה (פנימית)'), 'r')
    await userEvent.click(screen.getByRole('button', { name: 'שליחה למטופל' }))
    expect(api.requestFromPatient).toHaveBeenCalledWith(
      'C-1',
      expect.objectContaining({ kind: 'document', document_type: 'URINALYSIS' }),
    )
  })

  it('shows the refusal code', async () => {
    vi.mocked(api.requestFromPatient).mockRejectedValue(new api.ApiError(409, 'invalid_deadline'))
    renderPanel()
    await userEvent.selectOptions(screen.getByLabelText('הודעה'), 'clarify_general')
    await userEvent.type(screen.getByLabelText('סיבה (פנימית)'), 'r')
    await userEvent.click(screen.getByRole('button', { name: 'שליחה למטופל' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid_deadline')
  })
})
```

In `ReviewQueue.test.tsx` add a case: an item with `returned_by: 'patient_reply'` shows `התקבלה תשובת מטופל` beside the code `patient_reply` (in `.mono`). In `ReviewCase.test.tsx` add: with `human_engaged: true` and `allowed_decisions: ['resolve', 'reject']`, no `approve` button is rendered and the "בקשה מהמטופל" panel is; choosing a closing template and clicking resolve calls `api.decide` with `message: { template_id: 'close_handled' }` (mock `getMessageTemplates` to return the four templates above). Follow the existing tests' mocking and rendering patterns in those two files.

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && npm test -- src/pages/staff/PatientRequest.test.tsx src/pages/staff/ReviewQueue.test.tsx src/pages/staff/ReviewCase.test.tsx`
Expected: FAIL — the new module does not exist; the new cases fail.

- [ ] **Step 3: `PatientRequest.tsx`**

```tsx
import { useState } from 'react'
import * as api from '../../api/client'
import type { MessageBody, MessageTemplate, Role } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { TextField } from '../../components/TextField'
import { detailOf, toIsoWithOffset } from './labels'

/**
 * Sub-project 15 (design §7, §12): ask the patient a question or for one catalog document.
 * The texts come from the server's templates; free text is offered to clinical_staff only,
 * because only they may grant the ContentApproval it needs (§12.4). The reason stays internal.
 */
export type MessageChoice =
  | { mode: 'none' }
  | { mode: 'template'; template_id: string; param?: string }
  | { mode: 'text'; text: string }

export function toMessageBody(choice: MessageChoice): MessageBody | undefined {
  if (choice.mode === 'template') return { template_id: choice.template_id, param: choice.param }
  if (choice.mode === 'text') return { text: choice.text }
  return undefined
}

const FREE_TEXT = 'text'

/** One select for a template of `purpose` (or free text, for clinical staff), plus its parameter. */
export function MessagePicker({
  purpose,
  role,
  templates,
  value,
  onChange,
  allowNone = false,
}: {
  purpose: 'question' | 'closing'
  role: Role
  templates: MessageTemplate[]
  value: MessageChoice
  onChange: (next: MessageChoice) => void
  allowNone?: boolean
}) {
  const own = templates.filter((t) => t.purpose === purpose)
  const selected = value.mode === 'template' ? own.find((t) => t.template_id === value.template_id) : undefined
  const current = value.mode === 'template' ? value.template_id : value.mode === 'text' ? FREE_TEXT : ''
  return (
    <>
      <label className="field">
        <span className="field-label">הודעה</span>
        <select
          className="control"
          value={current}
          onChange={(event) => {
            const next = event.target.value
            if (next === '') onChange({ mode: 'none' })
            else if (next === FREE_TEXT) onChange({ mode: 'text', text: '' })
            else onChange({ mode: 'template', template_id: next })
          }}
        >
          <option value="">{allowNone ? 'בלי הודעה' : 'בחירת הודעה…'}</option>
          {own.map((t) => (
            <option key={t.template_id} value={t.template_id}>
              {t.text}
            </option>
          ))}
          {role === 'clinical_staff' && <option value={FREE_TEXT}>טקסט חופשי (צוות קליני)</option>}
        </select>
      </label>
      {selected?.param && value.mode === 'template' && (
        <label className="field">
          <span className="field-label">{selected.param === 'topic' ? 'נושא' : 'פרמטר'}</span>
          <select
            className="control"
            value={value.param ?? ''}
            onChange={(event) => onChange({ ...value, param: event.target.value || undefined })}
          >
            <option value="">בחירה…</option>
            {Object.entries(selected.options).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </label>
      )}
      {value.mode === 'text' && (
        <TextField
          multiline
          label="הטקסט למטופל"
          value={value.text}
          maxLength={2000}
          counter
          hint="נשלח למטופל כפי שנכתב, עם אישור תוכן (ContentApproval) שלך."
          onChange={(event) => onChange({ mode: 'text', text: event.target.value })}
        />
      )}
    </>
  )
}

const pad = (n: number) => String(n).padStart(2, '0')
function inTwentyFourHours(): string {
  const d = new Date(Date.now() + 24 * 3600_000)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function PatientRequest({
  caseId,
  shownContextRef,
  role,
  templates,
  onSent,
  onContextChanged,
}: {
  caseId: string
  shownContextRef: string
  role: Role
  templates: MessageTemplate[]
  onSent: () => void
  onContextChanged: () => void
}) {
  const [kind, setKind] = useState<'question' | 'document'>('question')
  const [choice, setChoice] = useState<MessageChoice>({ mode: 'none' })
  const [documentType, setDocumentType] = useState('')
  const [deadline, setDeadline] = useState(inTwentyFourHours)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const documents = templates.find((t) => t.purpose === 'document')?.options ?? {}

  async function send() {
    setBusy(true)
    setError(null)
    try {
      await api.requestFromPatient(caseId, {
        kind,
        reason,
        shown_context_ref: shownContextRef,
        deadline: toIsoWithOffset(deadline) ?? undefined,
        ...(kind === 'document' ? { document_type: documentType } : toMessageBody(choice)),
      })
      onSent()
    } catch (caught) {
      const detail = detailOf(caught)
      if (detail === 'context_changed') onContextChanged()
      setError(detail)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="patient-request" aria-labelledby="patient-request-h">
      <h3 className="section-h" id="patient-request-h">
        בקשה מהמטופל
      </h3>
      <p className="col-note">הפנייה תמתין לתשובת המטופל ותחזור לתור. היא לא תחזור לטיפול אוטומטי.</p>
      <div className="patient-request-kind" role="radiogroup" aria-label="סוג הבקשה">
        <label>
          <input type="radio" name="request-kind" checked={kind === 'question'} onChange={() => setKind('question')} />
          שאלת הבהרה
        </label>
        <label>
          <input type="radio" name="request-kind" checked={kind === 'document'} onChange={() => setKind('document')} />
          בקשת מסמך
        </label>
      </div>
      {kind === 'question' ? (
        <MessagePicker purpose="question" role={role} templates={templates} value={choice} onChange={setChoice} />
      ) : (
        <label className="field">
          <span className="field-label">סוג המסמך</span>
          <select className="control" value={documentType} onChange={(event) => setDocumentType(event.target.value)}>
            <option value="">בחירה…</option>
            {Object.entries(documents).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </label>
      )}
      <TextField
        label="עד מתי (ברירת מחדל: 24 שעות)"
        type="datetime-local"
        dir="ltr"
        value={deadline}
        onChange={(event) => setDeadline(event.target.value)}
      />
      <TextField
        multiline
        label="סיבה (פנימית)"
        value={reason}
        maxLength={2000}
        onChange={(event) => setReason(event.target.value)}
      />
      {error && (
        <Alert variant="error" title="הבקשה לא נשלחה">
          <span className="mono">{error}</span>
        </Alert>
      )}
      <Button variant="secondary" busy={busy} onClick={() => void send()}>
        שליחה למטופל
      </Button>
    </section>
  )
}
```

(If `TextField`'s `label` is not wired as the input's accessible name, or `.field` / `.field-label` / `.control` are not this project's names for a labelled select, follow the pattern `ReviewCase.tsx` / `CaseMonitor.tsx` use for their selects and keep the visible label texts above, which the tests query by.)

Add to `frontend/src/styles/app.css` (new selectors only):

```css
/* Sub-project 15: the staff request panel. */
.patient-request {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-block-start: var(--space-4);
  border-block-start: 1px solid var(--line-200);
}

.patient-request-kind {
  display: flex;
  gap: var(--space-4);
}
```

- [ ] **Step 4: Wire it into the review screen and the queue**

`ReviewCase.tsx`:
- Load the templates once: `const [templates, setTemplates] = useState<MessageTemplate[]>([])` and a `useEffect` calling `api.getMessageTemplates().then(setTemplates).catch(() => setTemplates([]))`.
- In the decision column, after the `ClinicalAnswer` block and before the `allowed.length === 0` branch, render `<PatientRequest …>` when `allowed.length > 0`, with `onSent` navigating to `/staff` with the notice `נשלחה בקשה למטופל בפנייה ${caseId}.` (the same shape `ClinicalAnswer`'s `onAnswered` uses) and `onContextChanged={() => void refreshContext()}`.
- In the decision form, above `decision-actions`, add a closing-message picker: `const [closing, setClosing] = useState<MessageChoice>({ mode: 'none' })` and `<MessagePicker purpose="closing" role={user?.role ?? 'admin_staff'} templates={templates} value={closing} onChange={setClosing} allowNone />`, under a small heading `הודעת סיום למטופל (אופציונלי)`.
- In `submit(decision)`, for `resolve` / `reject` add `message: toMessageBody(closing)` to the body (omit the key when it is `undefined`); never for `approve`.

`ReviewQueue.tsx`: in each row, when `item.returned_by` is set, show `RETURNED_BY_LABELS[item.returned_by]` followed by `<span className="mono">{item.returned_by}</span>` in the row's secondary line.

- [ ] **Step 5: Run the tests and the build**

Run: `cd frontend && npm test && npm run build`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "Add the staff request panel and the optional closing message" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The patient screen — reply and conversation

**Files:**
- Create: `frontend/src/pages/patient/ReplyToRequest.tsx`, `frontend/src/pages/patient/ReplyToRequest.test.tsx`
- Modify: `frontend/src/pages/patient/RequestDetail.tsx`, `frontend/src/pages/patient/RequestDetail.test.tsx`, `frontend/src/pages/patient/fixtures.ts`

**Interfaces:**
- Consumes: Task 10.
- Produces: `ReplyToRequest({ view, onChanged })`, `Conversation({ entries })`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/pages/patient/ReplyToRequest.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import type { PatientView } from '../../api/types'
import { Conversation, ReplyToRequest } from './ReplyToRequest'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  replyToRequest: vi.fn(),
  replyWithFile: vi.fn(),
}))

const BASE = {
  case_id: 'C-1',
  status: 'needs_reply',
  created_at: '2026-09-25T08:00:00Z',
  updated_at: '2026-09-25T08:05:00Z',
  request_text: 'שאלה',
  missing_document_ids: [],
  missing_document_request_template_id: null,
  message: null,
  history: [],
  document_upload: 'file',
  conversation: [],
} satisfies Partial<PatientView>

const QUESTION: PatientView = {
  ...BASE,
  reply_request: { kind: 'question', message: 'האם התכוונת למועד התור?', document_type: null, deadline: '2026-09-26T08:05:00Z' },
} as PatientView

const DOCUMENT: PatientView = {
  ...BASE,
  reply_request: { kind: 'document', message: 'נא להעלות את המסמך: בדיקת שתן.', document_type: 'URINALYSIS', deadline: null },
} as PatientView

describe('ReplyToRequest', () => {
  it('shows the question and sends a text reply', async () => {
    vi.mocked(api.replyToRequest).mockResolvedValue({ ...QUESTION, status: 'in_review', reply_request: null })
    const onChanged = vi.fn()
    render(<ReplyToRequest view={QUESTION} onChanged={onChanged} />)
    expect(screen.getByText('האם התכוונת למועד התור?')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('התשובה שלך'), 'כן')
    await userEvent.click(screen.getByRole('button', { name: 'שליחת התשובה' }))
    expect(api.replyToRequest).toHaveBeenCalledWith('C-1', 'כן')
    expect(onChanged).toHaveBeenCalled()
  })

  it('offers a PDF picker for a document request and explains a wrong type', async () => {
    vi.mocked(api.replyWithFile).mockResolvedValue({
      upload: { code: 'wrong_document_type', document_type: 'CBC' },
      request: DOCUMENT,
    })
    render(<ReplyToRequest view={DOCUMENT} onChanged={vi.fn()} />)
    await userEvent.upload(
      screen.getByLabelText('בחירת קובץ PDF'),
      new File(['%PDF'], 'cbc.pdf', { type: 'application/pdf' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'העלאת המסמך' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('המסמך שהועלה אינו המסמך שהתבקש')
  })
})

describe('Conversation', () => {
  it('lists staff messages and replies in order', () => {
    render(
      <Conversation
        entries={[
          { sender: 'staff', text: 'שאלה מהצוות', at: '2026-09-25T08:00:00Z' },
          { sender: 'patient', text: 'תשובה שלי', at: '2026-09-25T09:00:00Z' },
        ]}
      />,
    )
    const items = screen.getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('צוות')
    expect(items[0]).toHaveTextContent('שאלה מהצוות')
    expect(items[1]).toHaveTextContent('את/ה')
  })
})
```

In `RequestDetail.test.tsx` add: a `needs_reply` view renders the reply form; a `closed` view with `message` shows that message; add `reply_request: null, conversation: []` to every existing fixture in `fixtures.ts`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && npm test -- src/pages/patient`
Expected: FAIL — the module does not exist.

- [ ] **Step 3: `ReplyToRequest.tsx`**

```tsx
import { useState } from 'react'
import * as api from '../../api/client'
import type { ConversationEntry, PatientView } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { TextField } from '../../components/TextField'
import { documentLabel, errorMessage, formatDateTime } from './helpers'

/**
 * Sub-project 15 (design §9, §12): the patient's answer to a staff request - text for a
 * question, the requested PDF for a document. The patient sees the message and the deadline,
 * never why it was asked (§12.3).
 */
export function ReplyToRequest({ view, onChanged }: { view: PatientView; onChanged: (next: PatientView) => void }) {
  const request = view.reply_request
  if (!request) return null
  return (
    <section className="reply-request" aria-labelledby="reply-request-h">
      <h2 className="section-h" id="reply-request-h">
        בקשה מהצוות
      </h2>
      {request.message && <p className="message-text">{request.message}</p>}
      {request.deadline && <p className="muted">נא להשיב עד {formatDateTime(request.deadline)}.</p>}
      {request.kind === 'question' ? (
        <TextReply caseId={view.case_id} onChanged={onChanged} />
      ) : (
        <FileReply caseId={view.case_id} documentType={request.document_type} onChanged={onChanged} />
      )}
    </section>
  )
}

function TextReply({ caseId, onChanged }: { caseId: string; onChanged: (next: PatientView) => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function send() {
    setBusy(true)
    setError(null)
    try {
      onChanged(await api.replyToRequest(caseId, text))
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <TextField multiline label="התשובה שלך" value={text} maxLength={2000} counter onChange={(e) => setText(e.target.value)} />
      {error && <Alert variant="error">{error}</Alert>}
      <Button variant="primary" busy={busy} disabled={!text.trim()} onClick={() => void send()}>
        שליחת התשובה
      </Button>
    </>
  )
}

const MAX_BYTES = 10 * 1024 * 1024

function FileReply({
  caseId,
  documentType,
  onChanged,
}: {
  caseId: string
  documentType: string | null
  onChanged: (next: PatientView) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function send() {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.pdf') || file.size > MAX_BYTES) {
      setError('יש לבחור קובץ PDF עד 10MB.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const { upload, request } = await api.replyWithFile(caseId, file)
      if (upload.code === 'accepted') onChanged(request)
      else setError(uploadText(upload.code))
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      {documentType && <p>המסמך המבוקש: {documentLabel(documentType)}</p>}
      <label className="field">
        <span className="field-label">בחירת קובץ PDF</span>
        <input
          className="file-input"
          type="file"
          accept="application/pdf,.pdf"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      {error && <Alert variant="error">{error}</Alert>}
      <Button variant="primary" busy={busy} disabled={!file} onClick={() => void send()}>
        העלאת המסמך
      </Button>
    </>
  )
}

function uploadText(code: string): string {
  switch (code) {
    case 'wrong_document_type':
      return 'המסמך שהועלה אינו המסמך שהתבקש. נא להעלות את המסמך הנכון.'
    case 'not_medical':
      return 'הקובץ אינו מסמך רפואי, ולכן לא נקלט.'
    case 'expired':
      return 'המסמך ישן מדי לפי כללי התוקף. יש להעלות מסמך עדכני.'
    case 'not_yours':
      return 'המסמך אינו שייך לך, ולכן לא נקלט.'
    default:
      return 'לא הצלחנו לקרוא את המסמך. ודאו שזה קובץ PDF ברור ונסו שוב.'
  }
}

/** The staff messages the patient may see and their own replies, oldest first. */
export function Conversation({ entries }: { entries: ConversationEntry[] }) {
  if (entries.length === 0) return null
  return (
    <section className="conversation" aria-label="ההתכתבות עם הצוות">
      <ol className="conversation-list">
        {entries.map((entry, index) => (
          <li key={index} className={`conversation-item from-${entry.sender}`}>
            <span className="conversation-who">{entry.sender === 'staff' ? 'צוות' : 'את/ה'}</span>
            <span className="message-text">{entry.text}</span>
            <time className="muted" dateTime={entry.at}>
              {formatDateTime(entry.at)}
            </time>
          </li>
        ))}
      </ol>
    </section>
  )
}
```

(If `documentLabel`, `errorMessage` or `formatDateTime` live under other names in `pages/patient/helpers.ts`, import the existing ones — `RequestDetail.tsx` uses them.)

Add to `app.css` (new selectors only; logical properties):

```css
/* Sub-project 15: the patient's reply and the conversation with the staff. */
.reply-request,
.conversation-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.conversation-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.conversation-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-3);
  border-radius: 10px;
  background: var(--surface-100);
  max-inline-size: 42em;
}

.conversation-item.from-patient {
  margin-inline-start: auto;
}

.conversation-who {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-500);
}
```

- [ ] **Step 4: Wire it into `RequestDetail.tsx`**

In `StatusContent`: add `case 'needs_reply': return <ReplyToRequest view={view} onChanged={onChanged} />`; in `case 'closed'`, when `view.message` is set, render `<Alert variant="info" title="הודעה מהצוות"><span className="message-text">{view.message}</span></Alert>` instead of the generic text (keep the generic text when it is null). Render `<Conversation entries={view.conversation} />` under the status content for every status.

- [ ] **Step 5: Run the tests and the build**

Run: `cd frontend && npm test && npm run build`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "Let the patient answer a staff request and read the conversation" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: The metrics tile

**Files:**
- Modify: `frontend/src/pages/staff/Metrics.tsx`, `frontend/src/pages/staff/Metrics.test.tsx`

- [ ] **Step 1: Write the failing test**

In `Metrics.test.tsx`'s first test, give the fixture `by_state` an `AwaitingPatientReply: 1` entry (and raise `opened` by 1 so `בטיפול` stays `1`), and assert `tile('ממתינות לתשובת מטופל')` has text `1`.

- [ ] **Step 2: Run it to see it fail**

Run: `cd frontend && npm test -- src/pages/staff/Metrics.test.tsx`
Expected: FAIL — no such tile.

- [ ] **Step 3: Add the tile**

In `FlowGroup`, add `'AwaitingPatientReply'` to `named`, and after the `ממתינות למטופל` tile add `<Tile label="ממתינות לתשובת מטופל" value={formatCount(state('AwaitingPatientReply'))} />`.

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npm test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/staff/Metrics.tsx frontend/src/pages/staff/Metrics.test.tsx
git commit -m "Show cases waiting for a patient's reply in the metrics" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Whole-system verification and documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: The full backend suite** — `$DC run --rm backend pytest -q`. Report the count.
- [ ] **Step 2: The formal core did not move** —
  ```bash
  $DC run --rm backend python -m obs.golden
  $DC run --rm backend python -m hospital_agent.policy.consistency
  $DC run --rm backend pytest tests/test_fsm.py::test_table_matches_spec_3_row_by_row -v
  ```
  Expected: `35` / `4` / `54`; `7 abstract properties passed (9 UNSAT queries)`; 1 passed.
- [ ] **Step 3: Frontend** — `cd frontend && npm test && npm run build`.
- [ ] **Step 4: CLAUDE.md** — in *Project status*, after the sub-project 14 sentence, add:

```markdown
Sub-project 15 (`docs/superpowers/specs/2026-09-25-patient-requests-design.md`, `docs/spec_corrections.md` rows 83-88) lets a staff member ask the patient a clarifying question or for one catalog document, and close or reject with a closing message: templates for any staff member, free text for `clinical_staff` only under a `ContentApproval` (`message_approval_id`). It adds `AwaitingPatientReply` and `PATIENT_REPLY_REQUESTED` / `PATIENT_REPLY_SUBMITTED` as a marked extension (`naming.EXTENSION_*`, `fsm.EXTENSION_TRANSITIONS`) - the spec's own lists and their tests are unchanged - and a case a person has written to never goes back to the agent (`WorkflowDecisionValid` refuses `HUMAN_APPROVED` with `human_engaged`; T13 in the Temporal Monitor). Migration 0006 adds three `cases` columns and widens `ck_approvals_decision` and `ck_data_log_kind`.
```

and in *Working in the backend*, the `fsm.py` bullet: append `Sub-project 15's three rows live in fsm.EXTENSION_TRANSITIONS, outside the 41; resolve() searches both.`

- [ ] **Step 5: Commit** — `git add CLAUDE.md && git commit -m "Record sub-project 15 in the project status" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`
- [ ] **Step 6: Clean up** — `$DC down -v` (the `requests-proto` project only).
