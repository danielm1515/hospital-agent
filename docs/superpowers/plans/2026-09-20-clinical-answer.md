# Sub-project 8 (Clinical Answer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a `clinical_staff` reviewer answer a `MedicalQuestion` escalation with a text the patient then sees, authorised by a `ContentApproval` (§12.4) bound to that exact text.

**Architecture:** The Human Review Service records the answer in the Data Log, writes a final `executions` row and a `ContentApproval` bound to it, then closes the case with the existing `HUMAN_RESOLVED_CASE` transition. The State Manager verifies and consumes that ContentApproval inside the same transaction, exactly as it already does for a PolicyReview override. The patient screen shows the answer only when a consumed, matching ContentApproval exists.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy Core, pytest (backend); Vite + React 18 + TypeScript, Vitest + Testing Library (frontend). No new dependencies, no new migration.

**Spec:** `docs/superpowers/specs/2026-09-20-clinical-answer-design.md`; binding spec `docs/spec/05-action-registry.md`, `docs/spec/12-context-audit-approval.md` §12.4-§12.5, `docs/spec/06-temporal-rules.md` §6.3, `docs/spec/03-transitions-guards.md`.

## Global Constraints

- **Nothing in the binding lists changes.** 12 States, 26 Events, the 41 rows of the §3 table, the 12 temporal rules and the closed Action list stay exactly as they are. No new migration: `approvals`, `executions` and `data_log` already have every column this needs.
- **Only `clinical_staff` may grant a ContentApproval** (§12.4), and only on an escalation whose kind is `MedicalQuestion`.
- **Fail closed (§14):** any doubt about the approval blocks the transition and consumes nothing.
- **Identity comes from the verified token only** (§18.3). No request body ever carries `reviewer_id` or `reviewer_role`.
- **The patient never sees an escalation kind, a policy reason or an Audit row** (§12.3).
- Everything runs in Docker: `docker compose run --rm backend pytest`. Frontend: `cd frontend && npm test`, `npm run build`. Never `docker compose down -v`.
- Never read, print or commit `.env` or any key. LF line endings. Every commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `backend/hospital_agent/state_manager.py` | The one new TCB seam: verify + consume a ContentApproval in the transition's transaction. |
| `backend/hospital_agent/human_review.py` | `answer()`: the checks, the Data Log row, the execution row, the two approvals, the transition. |
| `backend/hospital_agent/repository.py` | `content_approvals_for(conn, case_id, action)` - the read the Session Service needs. |
| `backend/hospital_agent/session.py` | Second delivery source for `patient_status` / `message`. |
| `backend/hospital_agent/api/{schemas,routes_staff}.py` | `POST /api/staff/cases/{id}/answer`. |
| `docs/api.md` | The new route and the widened `completed` meaning. The UI's only contract source. |
| `frontend/src/api/{types,client}.ts` | `AnswerBody` and `answer()`. |
| `frontend/src/pages/staff/ClinicalAnswer.tsx` | The answer form, its own component, co-located test. |
| `frontend/src/pages/staff/ReviewCase.tsx` | Mounts `ClinicalAnswer` for the right kind and role. |
| `frontend/src/styles/app.css` | Styles for the answer block. |

---

### Task 1: The State Manager seam

**Files:**
- Modify: `backend/hospital_agent/state_manager.py`
- Test: `backend/tests/test_clinical_answer.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `StateManager.apply(case_id, Event.HUMAN_RESOLVED_CASE, {"approval_id": <WorkflowDecision id>, "content_approval_id": <ContentApproval id>}, Component.EXTERNAL)` commits only when the ContentApproval verifies; on success the audit row carries `action="AnswerClinicalQuestion"`, `content_hash` and `execution_id` taken **from the approval row**, and the ContentApproval's `consumed_at` is set in the same transaction. On failure the result is `Blocked` with reason `content_approval_invalid`.

**Context the implementer needs:**

`_apply_once` already has the precedent to copy - after `insert_audit` it consumes a PolicyReview override in the same transaction:

```python
            override_id = payload.get("policy_review_override_id")
            if event in POLICY_DECISION_EVENTS and override_id:
                if repository.consume_approval(conn, override_id, now) == 0:
                    raise _StaleVersion(case.case_id)
```

`_audit_entry` already reads `action`, `content_hash` and `execution_id` out of the payload, so the seam does not touch it: it replaces those three payload values with the verified ones before the entry is built. `repository.load_approval`, `repository.load_execution` and `repository.consume_approval` all exist.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_clinical_answer.py`:

```python
"""Sub-project 8: a clinical answer authorised by a ContentApproval (§5, §12.4, §6.3)."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent import repository
from hospital_agent.case import ApprovalRecord, ExecutionRecord
from hospital_agent.naming import Action, Component, EscalationKind, Event, State
from tests.driver import Driver

ANSWER = "אין להפסיק מדלל דם ללא הנחיית הרופא המטפל."
ANSWER_HASH = "0f2a" * 16


QUESTION = "Should I stop taking my blood thinner?"


def escalated(sm, app_engine) -> Driver:
    """A case sitting in AwaitingHumanReview with escalation_kind=MedicalQuestion.

    The same three driver calls `tests/test_human_review.py` and `tests/test_api_staff.py`
    already use for this shape.
    """
    d = Driver(sm, app_engine)
    d.submit()
    d.validate(QUESTION)
    d.medical_question()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.MEDICAL_QUESTION)
    return d


def write_execution(app_engine, d: Driver, *, content_hash: str = ANSWER_HASH) -> str:
    execution_id = f"EXEC-{d.case_id[-8:]}"
    with app_engine.begin() as conn:
        repository.insert_execution(conn, ExecutionRecord(
            execution_id=execution_id, case_id=d.case_id, patient_id=d.patient_id,
            action=Action.ANSWER_CLINICAL_QUESTION.value, step=0, retry_cycle=0, attempt_number=0,
            idempotency_key=f"{d.case_id}:answer", status="succeeded",
            content_hash=content_hash, medical_content_flag=True))
    return execution_id


def write_approvals(app_engine, d: Driver, execution_id: str, **changes) -> tuple[str, str]:
    """A WorkflowDecision for the transition and a ContentApproval for the text."""
    now = datetime.now(UTC)
    workflow = ApprovalRecord(
        approval_id=f"APPR-W-{d.case_id[-6:]}", approval_type="WorkflowDecision", case_id=d.case_id,
        patient_id=d.patient_id, reviewer_id="coordinator_nurse", reviewer_role="clinical_staff",
        decision="resolve", reason="נענתה על ידי אחות מתאמת", shown_context_ref="ctx-1",
        granted_at=now, valid_until=now + timedelta(hours=1),
        escalation_kind=EscalationKind.MEDICAL_QUESTION.value)
    content = replace(
        workflow, approval_id=f"APPR-C-{d.case_id[-6:]}", approval_type="ContentApproval",
        execution_id=execution_id, action=Action.ANSWER_CLINICAL_QUESTION.value,
        content_hash=ANSWER_HASH, escalation_kind=None)
    content = replace(content, **changes)
    with app_engine.begin() as conn:
        repository.insert_approval(conn, workflow)
        repository.insert_approval(conn, content)
    return workflow.approval_id, content.approval_id


def resolve_with(sm, d: Driver, workflow_id: str, content_id: str | None):
    payload = {"approval_id": workflow_id}
    if content_id is not None:
        payload["content_approval_id"] = content_id
    return sm.apply(d.case_id, Event.HUMAN_RESOLVED_CASE, payload, Component.EXTERNAL)


# --- the seam ------------------------------------------------------------------------------

def test_a_valid_content_approval_is_recorded_on_the_row_and_consumed(sm, app_engine):
    d = escalated(sm, app_engine)
    execution_id = write_execution(app_engine, d)
    workflow_id, content_id = write_approvals(app_engine, d, execution_id)

    result = resolve_with(sm, d, workflow_id, content_id)

    assert result.committed and result.state_after is State.COMPLETED
    row = d.trace()[-1]
    # §5's postcondition: the answer is documented, with the approval and the exact text.
    assert row.event == Event.HUMAN_RESOLVED_CASE.value
    assert row.action == Action.ANSWER_CLINICAL_QUESTION.value
    assert row.content_hash == ANSWER_HASH
    assert row.execution_id == execution_id
    assert row.approval_id == workflow_id  # the transition's own approval is the WorkflowDecision
    with app_engine.connect() as conn:
        assert repository.load_approval(conn, content_id).consumed_at is not None
        assert repository.load_approval(conn, workflow_id).consumed_at is not None


def test_a_resolve_without_an_answer_is_unchanged(sm, app_engine):
    """The plain close keeps working, and carries no action or content hash."""
    d = escalated(sm, app_engine)
    workflow_id, _ = write_approvals(app_engine, d, write_execution(app_engine, d))

    result = resolve_with(sm, d, workflow_id, None)

    assert result.committed and result.state_after is State.COMPLETED
    row = d.trace()[-1]
    assert row.action is None and row.content_hash is None


@pytest.mark.parametrize("changes", [
    {"approval_type": "WorkflowDecision"},                        # not a content approval
    {"reviewer_role": "admin_staff"},                             # §12.4: clinical_staff only
    {"action": Action.SEND_STATUS_UPDATE.value},                  # another action's approval
    {"content_hash": None},                                       # §12.4 requires it
    {"execution_id": None},                                       # §12.4 requires it
    {"consumed_at": datetime(2026, 1, 1, tzinfo=UTC)},            # single use
    {"valid_until": datetime(2020, 1, 1, tzinfo=UTC)},            # expired
    {"case_id": "CASE-SOMEONE-ELSE"},                             # another case's approval
])
def test_an_unusable_content_approval_blocks_the_transition(sm, app_engine, changes):
    d = escalated(sm, app_engine)
    execution_id = write_execution(app_engine, d)
    workflow_id, content_id = write_approvals(app_engine, d, execution_id, **changes)

    result = resolve_with(sm, d, workflow_id, content_id)

    assert not result.committed and result.reason == "content_approval_invalid"
    assert d.state is State.AWAITING_HUMAN_REVIEW
    with app_engine.connect() as conn:
        # Fail closed: nothing was consumed, so the reviewer can try again.
        assert repository.load_approval(conn, workflow_id).consumed_at is None
        if changes.get("case_id") is None:
            assert repository.load_approval(conn, content_id).consumed_at is None


def test_an_approval_whose_execution_belongs_to_another_case_is_refused(sm, app_engine):
    d = escalated(sm, app_engine)
    other = escalated(sm, app_engine)
    workflow_id, content_id = write_approvals(app_engine, d, write_execution(app_engine, other))

    result = resolve_with(sm, d, workflow_id, content_id)

    assert not result.committed and result.reason == "content_approval_invalid"


def test_an_execution_whose_hash_differs_from_the_approval_is_refused(sm, app_engine):
    d = escalated(sm, app_engine)
    execution_id = write_execution(app_engine, d, content_hash="a different hash")
    workflow_id, content_id = write_approvals(app_engine, d, execution_id)

    result = resolve_with(sm, d, workflow_id, content_id)

    assert not result.committed and result.reason == "content_approval_invalid"


def test_content_approval_id_is_ignored_on_any_other_event(sm, app_engine):
    """The seam is one event wide: an unrelated event never consumes an approval."""
    d = escalated(sm, app_engine)
    execution_id = write_execution(app_engine, d)
    workflow_id, content_id = write_approvals(app_engine, d, execution_id)

    result = sm.apply(d.case_id, Event.HUMAN_REJECTED,
                      {"approval_id": workflow_id, "content_approval_id": content_id}, Component.EXTERNAL)

    assert result.committed and result.state_after is State.FAILED
    assert d.trace()[-1].content_hash is None
    with app_engine.connect() as conn:
        assert repository.load_approval(conn, content_id).consumed_at is None
```

The `sm` and `app_engine` fixtures come from `tests/conftest.py`. `Driver.submit()`, `Driver.validate(text)` and `Driver.medical_question()` are the driver's own helpers - do not add new ones for this plan.

- [ ] **Step 2: Run the tests and watch them fail**

Run: `docker compose run --rm backend pytest tests/test_clinical_answer.py -v`
Expected: every test fails - `content_approval_invalid` is not a reason anything produces yet, and the audit row carries no action.

- [ ] **Step 3: Add the verification helper**

In `state_manager.py`, next to the other module-level helpers (near `_policy_evidence`):

```python
CONTENT_APPROVAL_INVALID = "content_approval_invalid"


def _clinical_answer_approval(
    conn: Connection, case: CaseRecord, approval_id: str, now: datetime
) -> ApprovalRecord | None:
    """The ContentApproval that authorises a clinical answer, or None if it cannot be used.

    §12.4: a ContentApproval is granted by clinical_staff only and is bound to one exact
    message by execution_id + action + content_hash. Everything is re-checked here, inside
    the transaction that consumes it (§6.3), because this is the only place that can hold
    the check and the consumption together. Anything unexpected returns None and the caller
    blocks the transition (§14).
    """
    approval = repository.load_approval(conn, approval_id)
    if approval is None or approval.approval_type != "ContentApproval":
        return None
    if (approval.case_id, approval.patient_id) != (case.case_id, case.patient_id):
        return None
    if approval.reviewer_role != "clinical_staff":
        return None
    if approval.action != Action.ANSWER_CLINICAL_QUESTION.value:
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

Import `Action` from `.naming` and `ApprovalRecord` from `.case` if they are not already imported in this module.

- [ ] **Step 4: Wire the seam into `_apply_once`**

Immediately after `ctx = GuardContext(...)` is built and **before** `resolution = resolve(state, event, ctx)`, add:

```python
            content_approval = None
            if event is Event.HUMAN_RESOLVED_CASE and payload.get("content_approval_id"):
                if case is None:
                    return self._block(conn, None, event, CONTENT_APPROVAL_INVALID, now, payload=payload)
                content_approval = _clinical_answer_approval(
                    conn, case, payload["content_approval_id"], now)
                if content_approval is None:
                    return self._block(conn, case, event, CONTENT_APPROVAL_INVALID, now, payload=payload)
                # The audit row describes what was verified, never what the caller claimed.
                payload = {**payload, "action": content_approval.action,
                           "content_hash": content_approval.content_hash,
                           "execution_id": content_approval.execution_id}
```

Then, beside the `policy_review_override_id` block after `insert_audit`:

```python
            if content_approval is not None:
                if repository.consume_approval(conn, content_approval.approval_id, now) == 0:
                    raise _StaleVersion(case.case_id)
```

Note that `payload` is rebound, so the `_audit_entry(...)` call below picks the verified values up without any change to that function.

- [ ] **Step 5: Run the tests**

Run: `docker compose run --rm backend pytest tests/test_clinical_answer.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole suite and the golden traces**

Run: `docker compose run --rm backend pytest -q`
Expected: 0 failures.

Run: `docker compose run --rm backend python -m obs.golden`
Expected: `35`, `4`, `54` - unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/hospital_agent/state_manager.py backend/tests/test_clinical_answer.py
git commit -m "Verify and consume a clinical answer's ContentApproval in the transition"
```

---

### Task 2: `HumanReviewService.answer()`

**Files:**
- Modify: `backend/hospital_agent/human_review.py`, `backend/hospital_agent/repository.py`
- Test: `backend/tests/test_clinical_answer.py` (append)

**Interfaces:**
- Consumes: the Task 1 seam - `apply(..., HUMAN_RESOLVED_CASE, {"approval_id": ..., "content_approval_id": ...}, Component.EXTERNAL)`.
- Produces:
  ```python
  class AnswerRejected(DecisionRejected): ...   # subclass, so the API's existing 409 handling covers it

  def answer(self, *, reviewer_id: str, reviewer_role: str, case_id: str, answer: str,
             reason: str, shown_context_ref: str) -> TransitionResult
  ```
  and `repository.content_approvals_for(conn, case_id, action) -> list[ApprovalRecord]`.

**Rules, in this order** (each refuses before anything is written):

| Check | Raised |
|---|---|
| case unknown | `CaseNotFound` |
| state is not `AwaitingHumanReview` | `NotInReview` |
| `reviewer_role != "clinical_staff"` | `AnswerRejected("clinical_staff_only")` |
| `escalation_kind` is not `MedicalQuestion` | `AnswerRejected("decision_not_allowed")` |
| `answer.strip()` empty | `AnswerRejected("answer_required")` |
| `reason.strip()` empty | `AnswerRejected("reason_required")` |
| `shown_context_ref` differs from `self.context(case_id).shown_context_ref` | `ContextChanged` |

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_clinical_answer.py`:

```python
# --- the service ---------------------------------------------------------------------------

from hospital_agent import data_log                                    # noqa: E402
from hospital_agent.human_review import AnswerRejected, ContextChanged, HumanReviewService, NotInReview  # noqa: E402
from hospital_agent.session import SessionService                      # noqa: E402

NURSE = ("coordinator_nurse", "clinical_staff")
ADMIN = ("admin_coordinator", "admin_staff")


@pytest.fixture
def session(sm):
    return SessionService(sm)


@pytest.fixture
def review(sm, session):
    """The same two-line fixture `tests/test_human_review.py` uses."""
    return HumanReviewService(sm, session)


def answer_it(review, d, *, who=NURSE, answer=ANSWER, reason="נענתה בטלפון על ידי האחות"):
    return review.answer(reviewer_id=who[0], reviewer_role=who[1], case_id=d.case_id,
                         answer=answer, reason=reason,
                         shown_context_ref=review.context(d.case_id).shown_context_ref)


def approvals_of(app_engine, case_id):
    with app_engine.connect() as conn:
        return {a.approval_type: a for a in repository.content_approvals_for(conn, case_id, None)}


def test_a_nurse_answers_and_the_case_completes(review, sm, app_engine):
    d = escalated(sm, app_engine)

    result = answer_it(review, d)

    assert result.committed and result.state_after is State.COMPLETED
    row = d.trace()[-1]
    assert row.action == Action.ANSWER_CLINICAL_QUESTION.value
    with app_engine.connect() as conn:
        [message] = [e for e in data_log.entries(conn, d.case_id, data_log.DataKind.OUTGOING_MESSAGE)]
    assert message.content == ANSWER and row.content_hash == message.content_hash
    both = approvals_of(app_engine, d.case_id)
    assert both["ContentApproval"].consumed_at is not None
    assert both["WorkflowDecision"].consumed_at is not None
    assert both["ContentApproval"].reviewer_role == "clinical_staff"


def test_the_execution_row_is_final_so_restart_recovery_ignores_it(review, sm, app_engine):
    """Decision 4: a row left in 'started' would escalate as ExecutionUnknown after a restart."""
    d = escalated(sm, app_engine)
    answer_it(review, d)
    with app_engine.connect() as conn:
        executions = repository.executions_of_case(conn, d.case_id)
    assert [e.status for e in executions] == ["succeeded"]
    assert executions[0].medical_content_flag is True


def test_admin_staff_may_not_answer_and_nothing_is_written(review, sm, app_engine):
    d = escalated(sm, app_engine)
    with pytest.raises(AnswerRejected) as rejected:
        answer_it(review, d, who=ADMIN)
    assert rejected.value.reason == "clinical_staff_only"
    assert d.state is State.AWAITING_HUMAN_REVIEW
    with app_engine.connect() as conn:
        assert data_log.entries(conn, d.case_id, data_log.DataKind.OUTGOING_MESSAGE) == []
    assert approvals_of(app_engine, d.case_id) == {}


def test_an_escalation_that_is_not_a_medical_question_is_refused(review, sm, app_engine):
    """A RetryExhausted case is in review too - but it is not a question to answer."""
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    d.retry_exhausted()
    assert d.case.escalation_kind is EscalationKind.RETRY_EXHAUSTED
    with pytest.raises(AnswerRejected) as rejected:
        answer_it(review, d)
    assert rejected.value.reason == "decision_not_allowed"


@pytest.mark.parametrize("field, value, expected", [
    ("answer", "   ", "answer_required"),
    ("reason", "   ", "reason_required"),
])
def test_empty_input_is_refused(review, sm, app_engine, field, value, expected):
    d = escalated(sm, app_engine)
    with pytest.raises(AnswerRejected) as rejected:
        answer_it(review, d, **{field: value})
    assert rejected.value.reason == expected


def test_a_stale_shown_context_ref_is_refused(review, sm, app_engine):
    d = escalated(sm, app_engine)
    with pytest.raises(ContextChanged):
        review.answer(reviewer_id=NURSE[0], reviewer_role=NURSE[1], case_id=d.case_id,
                       answer=ANSWER, reason="סיבה", shown_context_ref="ctx-stale")
    assert d.state is State.AWAITING_HUMAN_REVIEW


def test_a_case_that_is_not_in_review_is_refused(review, sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    with pytest.raises(NotInReview):
        answer_it(review, d)
```

`tests/conftest.py` provides `sm` and `app_engine` only; `session` and `review` are per-file fixtures, which is why they are declared above (`tests/test_human_review.py` declares the same two).

- [ ] **Step 2: Run them and watch them fail**

Run: `docker compose run --rm backend pytest tests/test_clinical_answer.py -v`
Expected: `ImportError: cannot import name 'AnswerRejected'`.

- [ ] **Step 3: Add the two repository reads**

In `backend/hospital_agent/repository.py`, beside the other approval functions:

```python
def content_approvals_for(conn: Connection, case_id: str, action: str | None) -> list[ApprovalRecord]:
    """This case's approvals, newest last; `action` narrows to one Action Registry entry."""
    query = select(approvals).where(approvals.c.case_id == case_id)
    if action is not None:
        query = query.where(approvals.c.action == action)
    rows = conn.execute(query.order_by(approvals.c.granted_at, approvals.c.approval_id)).mappings()
    return [ApprovalRecord(**dict(row)) for row in rows]


def executions_of_case(conn: Connection, case_id: str) -> list[ExecutionRecord]:
    """This case's executions, oldest first."""
    rows = conn.execute(
        select(executions).where(executions.c.case_id == case_id).order_by(executions.c.execution_id)
    ).mappings()
    return [ExecutionRecord(**dict(row)) for row in rows]
```

If `executions_of_case` (or an equivalent) already exists, reuse it instead of adding a second one.

- [ ] **Step 4: Write `answer()`**

In `human_review.py`, add the exception beside `DecisionRejected`:

```python
class AnswerRejected(DecisionRejected):
    """A clinical answer that cannot be given (API 409/403 {"detail": reason})."""
```

and the method, after `decide()`:

```python
    def answer(self, *, reviewer_id: str, reviewer_role: str, case_id: str, answer: str,
               reason: str, shown_context_ref: str) -> TransitionResult:
        """§5 AnswerClinicalQuestion: record a clinical answer, authorise it and close the case.

        The Human Review Service is this action's actor (§5), not the Tool Executor: there is
        no external call, so there is no TOOL_EXECUTION_STARTED row and T6 does not apply. What
        makes the answer deliverable is the ContentApproval, which the State Manager re-checks
        and consumes inside the transition's own transaction (§6.3).
        """
        case = self._load(case_id)
        if case.state is not State.AWAITING_HUMAN_REVIEW:
            raise NotInReview(case_id)
        if reviewer_role != CLINICAL_STAFF:
            raise AnswerRejected("clinical_staff_only")          # §12.4
        if case.escalation_kind is not EscalationKind.MEDICAL_QUESTION:
            raise AnswerRejected("decision_not_allowed")
        text = (answer or "").strip()
        if not text:
            raise AnswerRejected("answer_required")
        if not (reason or "").strip():
            raise AnswerRejected("reason_required")
        if shown_context_ref != self.context(case_id).shown_context_ref:
            raise ContextChanged(case_id)

        now = self.sm.clock()
        with self.engine.begin() as conn:
            entry = data_log.record(conn, case.case_id, case.patient_id,
                                    data_log.DataKind.OUTGOING_MESSAGE, text, now)
            execution_id = f"EXEC-{uuid.uuid4().hex[:12]}"
            repository.insert_execution(conn, ExecutionRecord(
                execution_id=execution_id,
                case_id=case.case_id,
                patient_id=case.patient_id,
                action=Action.ANSWER_CLINICAL_QUESTION.value,
                step=case.current_step or 0,
                retry_cycle=case.retry_cycle,
                attempt_number=0,
                idempotency_key=f"{case.case_id}:answer:{execution_id}",
                # Decision 4: final at once. There is no external call to wait for, and restart
                # recovery escalates any execution left in 'started' as ExecutionUnknown.
                status="succeeded",
                state_version=case.state_version,
                content_hash=entry.content_hash,
                medical_content_flag=True,
            ))
        content_id = self._grant_content_approval(case, reviewer_id, reviewer_role, reason,
                                                  shown_context_ref, execution_id, entry.content_hash)
        workflow_id = self._grant(case, reviewer_id, reviewer_role, "resolve", reason,
                                  shown_context_ref, None, None)
        result = self.sm.apply(case_id, Event.HUMAN_RESOLVED_CASE,
                               {"approval_id": workflow_id, "content_approval_id": content_id},
                               Component.EXTERNAL)
        if not result.committed:
            # Fail closed: the text is medical content that was never authorised to be shown.
            with self.engine.begin() as conn:
                data_log.tombstone(conn, entry.entry_id, self.sm.clock())
            raise AnswerRejected(result.reason or "blocked")
        self.wake()
        return result

    def _grant_content_approval(self, case: CaseRecord, reviewer_id: str, reviewer_role: str,
                                reason: str, shown_context_ref: str, execution_id: str,
                                content_hash: str) -> str:
        """The §12.4 ContentApproval: bound to execution_id + action + content_hash."""
        granted_at = self.sm.clock()
        approval = ApprovalRecord(
            approval_id=f"APPR-{uuid.uuid4().hex[:12]}",
            approval_type="ContentApproval",
            case_id=case.case_id,
            patient_id=case.patient_id,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            decision="approve",
            reason=reason,
            shown_context_ref=shown_context_ref,
            granted_at=granted_at,
            valid_until=granted_at + self.approval_ttl,
            execution_id=execution_id,
            action=Action.ANSWER_CLINICAL_QUESTION.value,
            content_hash=content_hash,
        )
        with self.engine.begin() as conn:
            repository.insert_approval(conn, approval)
        return approval.approval_id
```

Add the imports this needs: `Action` from `.naming`, `ExecutionRecord` from `.case`, and `CLINICAL_STAFF = "clinical_staff"` as a module constant (or import the name `auth.py` already defines - check `hospital_agent/auth.py`, which has `PATIENT, CLINICAL_STAFF, ADMIN_STAFF`, and import from there rather than re-declaring the string).

- [ ] **Step 5: Run the tests**

Run: `docker compose run --rm backend pytest tests/test_clinical_answer.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `docker compose run --rm backend pytest -q`
Expected: 0 failures.

- [ ] **Step 7: Commit**

```bash
git add backend/hospital_agent/human_review.py backend/hospital_agent/repository.py backend/tests/test_clinical_answer.py
git commit -m "Let clinical staff answer a medical question with an approved message"
```

---

### Task 3: What the patient sees

**Files:**
- Modify: `backend/hospital_agent/session.py`
- Test: `backend/tests/test_clinical_answer.py` (append)

**Interfaces:**
- Consumes: `repository.content_approvals_for(conn, case_id, action)` (Task 2), `HumanReviewService.answer()` (Task 2).
- Produces: `patient_view(case_id)` returns `status="completed"` and `message=<the answer>` for a case closed with a clinical answer; `history`'s last step is `completed`.

**The rule** (design §5): a `Completed` case is `completed` when the Data Log holds an outgoing message **whose content is still there** and whose `content_hash` matches a **consumed** ContentApproval of this case, for `AnswerClinicalQuestion`, granted by `clinical_staff`. Everything else stays `closed`.

`session.py` today decides this from `CASE_RESOLVED` rows:

```python
            resolved = [row for row in trace if row.record_type == "Transition"
                        and row.event == Event.CASE_RESOLVED.value]
            status = patient_status(case, delivered=bool(resolved))
```

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_clinical_answer.py`:

```python
# --- what the patient sees -----------------------------------------------------------------

def test_the_patient_sees_the_answer_as_a_delivered_message(review, session, sm, app_engine):
    d = escalated(sm, app_engine)
    answer_it(review, d)

    view = session.patient_view(d.case_id)

    assert (view.status, view.message) == ("completed", ANSWER)
    assert [step.status for step in view.history][-1] == "completed"
    # §12.3: still no kind, no reason, no audit.
    assert not hasattr(view, "escalation_kind")


def test_a_case_closed_without_an_answer_stays_closed(review, session, sm, app_engine):
    d = escalated(sm, app_engine)
    review.decide(reviewer_id=NURSE[0], reviewer_role=NURSE[1], case_id=d.case_id,
                   decision="resolve", reason="הופנתה למרפאה",
                   shown_context_ref=review.context(d.case_id).shown_context_ref)

    view = session.patient_view(d.case_id)

    assert (view.status, view.message) == ("closed", None)


def test_a_deleted_answer_is_not_shown_again(review, session, sm, app_engine):
    """§18.4: a tombstone leaves the hash and the approval, but no content to show."""
    d = escalated(sm, app_engine)
    answer_it(review, d)
    with app_engine.connect() as conn:
        [entry] = data_log.entries(conn, d.case_id, data_log.DataKind.OUTGOING_MESSAGE)
    assert review.tombstone(d.case_id, entry.entry_id)

    view = session.patient_view(d.case_id)

    assert (view.status, view.message) == ("closed", None)


def test_a_message_without_a_matching_approval_is_never_shown(session, sm, app_engine):
    """The read-side stand-in for T6: no approval, no medical content on the screen."""
    d = escalated(sm, app_engine)
    with app_engine.begin() as conn:
        data_log.record(conn, d.case_id, d.patient_id, data_log.DataKind.OUTGOING_MESSAGE,
                        "טקסט שאיש לא אישר", sm.clock())
    workflow_id, _ = write_approvals(app_engine, d, write_execution(app_engine, d))
    assert resolve_with(sm, d, workflow_id, None).committed

    view = session.patient_view(d.case_id)

    assert (view.status, view.message) == ("closed", None)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `docker compose run --rm backend pytest tests/test_clinical_answer.py -k patient -v`
Expected: the first test fails with `('closed', None) != ('completed', ANSWER)`.

- [ ] **Step 3: Add the second delivery source**

In `session.py`, add the helper beside `_delivered_message`:

```python
    def _clinical_answer(self, conn, case_id: str) -> str | None:
        """The answer a clinical_staff reviewer gave and approved (§5, §12.4), or None.

        The approval must be consumed - an approval that was never used authorised nothing -
        and its content_hash must match an outgoing message whose content is still there.
        This is what T6 does for the Tool Executor's path, applied where the patient reads.
        """
        approved = {
            approval.content_hash
            for approval in repository.content_approvals_for(
                conn, case_id, naming.Action.ANSWER_CLINICAL_QUESTION.value)
            if approval.approval_type == "ContentApproval"
            and approval.reviewer_role == auth.CLINICAL_STAFF
            and approval.consumed_at is not None
            and approval.content_hash
        }
        if not approved:
            return None
        answers = [entry.content for entry
                   in data_log.entries(conn, case_id, data_log.DataKind.OUTGOING_MESSAGE)
                   if entry.content is not None and entry.content_hash in approved]
        return answers[-1] if answers else None
```

and use it in `_view`, replacing the two lines that compute `status` and `message`:

```python
            resolved = [row for row in trace if row.record_type == "Transition"
                        and row.event == Event.CASE_RESOLVED.value]
            answer = self._clinical_answer(conn, case.case_id) if case.state is State.COMPLETED else None
            status = patient_status(case, delivered=bool(resolved) or answer is not None)
            history = status_history(trace, delivered=bool(resolved) or answer is not None)
            message = (self._delivered_message(conn, case.case_id, resolved[-1]) if resolved
                       else answer) if status == "completed" else None
```

Import what this needs: `from . import auth, naming` (or the specific names), keeping the module's existing import style. Watch for an import cycle: `auth.py` must not import `session.py` - if it does, use the literal `"clinical_staff"` with a comment naming §12.4 instead.

- [ ] **Step 4: Run the tests**

Run: `docker compose run --rm backend pytest tests/test_clinical_answer.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite and the golden traces**

Run: `docker compose run --rm backend pytest -q`
Expected: 0 failures.

Run: `docker compose run --rm backend python -m obs.golden`
Expected: `35`, `4`, `54`.

- [ ] **Step 6: Commit**

```bash
git add backend/hospital_agent/session.py backend/tests/test_clinical_answer.py
git commit -m "Show the patient a clinical answer that carries a consumed ContentApproval"
```

---

### Task 4: The API route and the contract

**Files:**
- Modify: `backend/hospital_agent/api/schemas.py`, `backend/hospital_agent/api/routes_staff.py`, `docs/api.md`
- Test: `backend/tests/test_api_staff.py` (append)

**Interfaces:**
- Consumes: `HumanReviewService.answer(...)` and `AnswerRejected` (Task 2).
- Produces: `POST /api/staff/cases/{case_id}/answer` with body `{answer, reason, shown_context_ref}` → `200 {"case_id", "state"}` (the existing `DecisionResponse`).

Status codes: `403 clinical_staff_only`; `409 not_in_review` / `decision_not_allowed` / `context_changed` / `answer_required` / `reason_required`; `404 case_not_found`; `422 invalid_body`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_api_staff.py`. That file already has the `client` and `staff` fixtures, the `NURSE, ADMIN, PATIENT` constants, `demo_password()` and the `medical_question(sm, app_engine)` helper - use them, and add nothing new:

```python
def token_for(client, user_id):
    token = client.post("/api/auth/login", json={"user_id": user_id, "password": demo_password()}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def answer(client, headers, case_id, **overrides):
    ref = client.get(f"/api/staff/cases/{case_id}/context", headers=headers).json()["shown_context_ref"]
    body = {"answer": "אין להפסיק את הטיפול ללא הנחיית הרופא.", "reason": "נענתה טלפונית",
            "shown_context_ref": ref, **overrides}
    return client.post(f"/api/staff/cases/{case_id}/answer", json=body, headers=headers)


def test_a_nurse_answers_a_medical_question_through_the_api(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)

    answered = answer(client, staff, d.case_id)

    assert answered.status_code == 200
    assert answered.json() == {"case_id": d.case_id, "state": "Completed"}


def test_admin_staff_gets_403_from_the_answer_route(client, sm, app_engine):
    """§12.4: a ContentApproval is clinical_staff only. admin_staff may still resolve or reject."""
    d = medical_question(sm, app_engine)

    refused = answer(client, token_for(client, ADMIN), d.case_id)

    assert refused.status_code == 403 and refused.json()["detail"] == "clinical_staff_only"
    assert sm.load(d.case_id).state is State.AWAITING_HUMAN_REVIEW


def test_an_escalation_that_is_not_a_medical_question_is_409(client, staff, sm, app_engine):
    d = retry_exhausted(sm, app_engine)

    refused = answer(client, staff, d.case_id)

    assert refused.status_code == 409 and refused.json()["detail"] == "decision_not_allowed"


def test_the_answer_route_refuses_an_empty_body_without_echoing_it(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)

    refused = client.post(f"/api/staff/cases/{d.case_id}/answer",
                          json={"answer": "", "reason": "", "shown_context_ref": ""}, headers=staff)

    assert refused.status_code == 422 and refused.json() == {"detail": "invalid_body"}


def test_a_stale_context_ref_is_409_context_changed(client, staff, sm, app_engine):
    d = medical_question(sm, app_engine)

    refused = answer(client, staff, d.case_id, shown_context_ref="ctx-stale")

    assert refused.status_code == 409 and refused.json()["detail"] == "context_changed"


def test_a_patient_token_cannot_reach_the_answer_route(client, sm, app_engine):
    d = medical_question(sm, app_engine)

    refused = client.post(f"/api/staff/cases/{d.case_id}/answer",
                          json={"answer": "תשובה", "reason": "סיבה", "shown_context_ref": "ctx"},
                          headers=token_for(client, PATIENT))

    assert refused.status_code == 403
```

If `token_for` duplicates a helper the file already has, use the file's.

- [ ] **Step 2: Run them and watch them fail**

Run: `docker compose run --rm backend pytest tests/test_api_staff.py -k answer -v`
Expected: `404` - the route does not exist.

- [ ] **Step 3: Add the request schema**

In `api/schemas.py`, beside `DecisionRequest`:

```python
class AnswerRequest(BaseModel):
    """§5 AnswerClinicalQuestion. `answer` is the exact text the ContentApproval covers."""

    answer: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    reason: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    shown_context_ref: Annotated[str, StringConstraints(min_length=1, max_length=200)]
```

- [ ] **Step 4: Add the route**

In `api/routes_staff.py`, after `decide`:

```python
@router.post("/cases/{case_id}/answer", response_model=DecisionResponse)
def answer(case_id: str, body: AnswerRequest, principal: Principal = Depends(require_staff),
           reviews: HumanReviewService = Depends(get_reviews)) -> DecisionResponse:
    """§5, §12.4: a clinical answer, authorised by a ContentApproval bound to its exact text."""
    try:
        reviews.answer(
            reviewer_id=principal.user_id,
            reviewer_role=principal.role,
            case_id=case_id,
            answer=body.answer,
            reason=body.reason,
            shown_context_ref=body.shown_context_ref,
        )
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    except NotInReview:
        raise HTTPException(status_code=409, detail="not_in_review") from None
    except ContextChanged:
        raise HTTPException(status_code=409, detail="context_changed") from None
    except AnswerRejected as rejected:
        status = 403 if rejected.reason == "clinical_staff_only" else 409
        raise HTTPException(status_code=status, detail=rejected.reason) from None
    return DecisionResponse(case_id=case_id, state=reviews.sm.load(case_id).state.value)
```

Add `AnswerRejected` to the `..human_review` import and `AnswerRequest` to the `.schemas` import. `AnswerRejected` subclasses `DecisionRejected`, so it must be caught **before** any `except DecisionRejected` in the same function - this route has none, but keep the order as written.

- [ ] **Step 5: Run the tests**

Run: `docker compose run --rm backend pytest tests/test_api_staff.py -q`
Expected: PASS.

- [ ] **Step 6: Document the route in `docs/api.md`**

In §5, after `POST /api/staff/cases/{case_id}/decision`, add:

````markdown
### POST /api/staff/cases/{case_id}/answer

A clinical answer to a `MedicalQuestion` escalation (§5 `AnswerClinicalQuestion`). The text is
recorded in the Data Log, a `ContentApproval` (§12.4) is bound to its exact `content_hash`, and
the case closes. Request:

```json
{"answer": "...", "reason": "...", "shown_context_ref": "ctx-ba3e0652b0ea"}
```

- `answer` is 1-2000 characters: the exact text the approval covers and the patient then reads.
- `reason` is 1-2000 characters, internal, exactly like a decision's reason. It never reaches
  the patient.
- `shown_context_ref` binds the answer to the context the reviewer was shown, like a decision.

`200`: `{"case_id": "...", "state": "Completed"}`.

- `403 clinical_staff_only` - only `clinical_staff` may grant a ContentApproval (§12.4). This is
  a role rule, not an authentication one: an `admin_staff` token reaches the route and is refused.
- `409 decision_not_allowed` - the escalation is not a `MedicalQuestion`.
- `409 not_in_review`, `409 context_changed`, `404 case_not_found`, `422 invalid_body`.
````

In §4, extend the `completed` row of the status table so the UI knows where a message can come from:

```markdown
| `completed` | An answer was delivered | `message` - either the status update the agent sent, or a clinical answer a `clinical_staff` reviewer wrote and approved |
```

- [ ] **Step 7: Run the whole suite**

Run: `docker compose run --rm backend pytest -q`
Expected: 0 failures.

- [ ] **Step 8: Commit**

```bash
git add backend/hospital_agent/api/ backend/tests/test_api_staff.py docs/api.md
git commit -m "Add POST /api/staff/cases/{id}/answer and document it"
```

---

### Task 5: The staff screen

**Files:**
- Create: `frontend/src/pages/staff/ClinicalAnswer.tsx`, `frontend/src/pages/staff/ClinicalAnswer.test.tsx`
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/pages/staff/ReviewCase.tsx`, `frontend/src/styles/app.css`

**Interfaces:**
- Consumes: `POST /api/staff/cases/{id}/answer` (Task 4).
- Produces: `<ClinicalAnswer caseId shownContextRef role onAnswered />`, and in the client:
  ```ts
  export interface AnswerBody { answer: string; reason: string; shown_context_ref: string }
  export function answer(caseId: string, body: AnswerBody): Promise<DecisionResult>
  ```

**Behaviour:**
- `ReviewCase` renders `<ClinicalAnswer>` only when the queue item's `escalation_kind === 'MedicalQuestion'`.
- `role === 'clinical_staff'` gets the form. Any other role gets the block in a locked state with one sentence: `אישור תוכן רפואי הוא של צוות קליני בלבד (§12.4). אפשר לסגור או לדחות את הפנייה.` - shown, not hidden, so the rule is visible (design decision 7).
- The form holds its own answer text and reason; it is not the decision form's state.
- A hint above the field says the text itself is what gets approved: `הטקסט הזה בדיוק הוא מה שיאושר ויוצג למטופל. עריכה אחריו מחייבת אישור חדש.`
- Submitting with an empty answer or reason shows the field error and calls nothing.
- On success it calls `onAnswered()` (ReviewCase navigates back to the queue with a notice, exactly as a decision does).
- `409 context_changed` shows the same "refresh the context" alert the decision form shows; `403 clinical_staff_only` shows `רק צוות קליני רשאי לאשר תוכן רפואי.`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/staff/ClinicalAnswer.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import { ClinicalAnswer } from './ClinicalAnswer'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  answer: vi.fn(),
}))

const onAnswered = vi.fn()

function renderAnswer(role: 'clinical_staff' | 'admin_staff' = 'clinical_staff') {
  return render(
    <ClinicalAnswer caseId="CASE-1" shownContextRef="ctx-1" role={role} onAnswered={onAnswered} />,
  )
}

beforeEach(() => {
  vi.mocked(api.answer).mockReset()
  vi.mocked(api.answer).mockResolvedValue({ case_id: 'CASE-1', state: 'Completed' })
  onAnswered.mockReset()
})

describe('ClinicalAnswer', () => {
  it('sends the exact text with the shown context ref', async () => {
    renderAnswer()

    await userEvent.type(screen.getByLabelText(/התשובה למטופל/), 'אין להפסיק את הטיפול.')
    await userEvent.type(screen.getByLabelText(/סיבה/), 'נענתה טלפונית')
    await userEvent.click(screen.getByRole('button', { name: 'אישור ושליחת התשובה' }))

    expect(api.answer).toHaveBeenCalledWith('CASE-1', {
      answer: 'אין להפסיק את הטיפול.',
      reason: 'נענתה טלפונית',
      shown_context_ref: 'ctx-1',
    })
    expect(onAnswered).toHaveBeenCalled()
  })

  it('says the text itself is what gets approved', () => {
    renderAnswer()
    expect(screen.getByText(/הטקסט הזה בדיוק הוא מה שיאושר/)).toBeInTheDocument()
  })

  it('locks the block for a role that may not approve content, and explains why', () => {
    renderAnswer('admin_staff')
    expect(screen.getByText(/צוות קליני בלבד/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'אישור ושליחת התשובה' })).not.toBeInTheDocument()
  })

  it('sends nothing when the answer or the reason is empty', async () => {
    renderAnswer()
    await userEvent.click(screen.getByRole('button', { name: 'אישור ושליחת התשובה' }))
    expect(api.answer).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('יש לכתוב תשובה וסיבה.')
  })

  it('shows a Hebrew message for a context that changed, and keeps the text', async () => {
    vi.mocked(api.answer).mockRejectedValue(new ApiError(409, 'context_changed'))
    renderAnswer()

    await userEvent.type(screen.getByLabelText(/התשובה למטופל/), 'תשובה')
    await userEvent.type(screen.getByLabelText(/סיבה/), 'סיבה')
    await userEvent.click(screen.getByRole('button', { name: 'אישור ושליחת התשובה' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/ההקשר השתנה/)
    expect(screen.getByLabelText(/התשובה למטופל/)).toHaveValue('תשובה')
    expect(onAnswered).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npx vitest run src/pages/staff/ClinicalAnswer.test.tsx`
Expected: FAIL - `Failed to resolve import "./ClinicalAnswer"`.

- [ ] **Step 3: Add the client call**

In `frontend/src/api/types.ts`, beside `DecisionBody`:

```ts
/** `POST /api/staff/cases/{case_id}/answer` body (`docs/api.md` §5). */
export interface AnswerBody {
  /** The exact text the ContentApproval covers, 1-2000 characters. */
  answer: string
  /** Internal, like a decision's reason: recorded, never sent to the patient. */
  reason: string
  shown_context_ref: string
}
```

In `frontend/src/api/client.ts`, beside `decide`:

```ts
export function answer(caseId: string, body: AnswerBody): Promise<DecisionResult> {
  return request<DecisionResult>(`/staff/cases/${encodeURIComponent(caseId)}/answer`, {
    method: 'POST',
    body,
  })
}
```

Match `decide`'s exact shape in that file - if it calls a helper with different argument names, copy that call and change only the path.

- [ ] **Step 4: Write the component**

Create `frontend/src/pages/staff/ClinicalAnswer.tsx`:

```tsx
import { useState } from 'react'
import * as api from '../../api/client'
import type { Role } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { TextField } from '../../components/TextField'
import { detailOf } from './labels'

/**
 * §5 AnswerClinicalQuestion: a clinical answer to a medical question.
 *
 * What is approved is the text itself - the server binds a ContentApproval (§12.4) to its
 * content_hash - so this form keeps the answer separate from the decision's reason, which
 * stays internal. Only `clinical_staff` may grant that approval; every other role sees the
 * block locked with the reason, rather than not seeing it at all.
 */
const MAX = 2000

export function ClinicalAnswer({
  caseId,
  shownContextRef,
  role,
  onAnswered,
}: {
  caseId: string
  shownContextRef: string
  role: Role
  onAnswered: () => void
}) {
  const [text, setText] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (role !== 'clinical_staff') {
    return (
      <Alert variant="info" title="תשובה למטופל">
        אישור תוכן רפואי הוא של צוות קליני בלבד (§12.4). אפשר לסגור או לדחות את הפנייה.
      </Alert>
    )
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!text.trim() || !reason.trim()) {
      setError('יש לכתוב תשובה וסיבה.')
      return
    }
    setError(null)
    setBusy(true)
    try {
      await api.answer(caseId, { answer: text.trim(), reason: reason.trim(), shown_context_ref: shownContextRef })
      onAnswered()
    } catch (caught) {
      setError(answerError(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="form answer-form" onSubmit={submit}>
      <h3 className="col-sub">תשובה למטופל</h3>
      <p className="col-note">
        הטקסט הזה בדיוק הוא מה שיאושר ויוצג למטופל. עריכה אחריו מחייבת אישור חדש.
      </p>
      <TextField
        multiline
        label="התשובה למטופל"
        value={text}
        maxLength={MAX}
        counter
        rows={5}
        onChange={(event) => setText(event.target.value)}
      />
      <TextField
        multiline
        label="סיבה (פנימית)"
        value={reason}
        maxLength={MAX}
        counter
        hint="נשמרת על שתי רשומות האישור וביומן הביקורת. אינה נשלחת למטופל."
        onChange={(event) => setReason(event.target.value)}
      />
      {error && <Alert variant="error">{error}</Alert>}
      <div className="actions">
        <Button type="submit" variant="primary" busy={busy}>
          אישור ושליחת התשובה
        </Button>
      </div>
    </form>
  )
}

/** One Hebrew sentence per `docs/api.md` §5 code; an unknown code stays generic. */
function answerError(caught: unknown): string {
  const code = detailOf(caught)
  if (code === 'context_changed') return 'ההקשר השתנה מאז שנטען. רעננו את ההקשר וכתבו את התשובה שוב.'
  if (code === 'clinical_staff_only') return 'רק צוות קליני רשאי לאשר תוכן רפואי.'
  if (code === 'decision_not_allowed') return 'לפנייה הזו אי אפשר לשלוח תשובה קלינית.'
  if (code === 'not_in_review') return 'הפנייה כבר אינה ממתינה להכרעה.'
  return 'שליחת התשובה נכשלה. נסו שוב.'
}
```

`detailOf` (in `./labels`) returns the raw `detail` code from an `ApiError`, or `'network_error'` - which is why `answerError` can switch on it directly.

- [ ] **Step 5: Run the component test**

Run: `cd frontend && npx vitest run src/pages/staff/ClinicalAnswer.test.tsx`
Expected: PASS.

- [ ] **Step 6: Mount it in `ReviewCase` and style it**

In `ReviewCase.tsx`, inside the decision column, above the decision form, render it for a medical question only:

```tsx
              {item?.escalation_kind === 'MedicalQuestion' && (
                <ClinicalAnswer
                  caseId={caseId}
                  shownContextRef={context.shown_context_ref}
                  role={user?.role ?? 'admin_staff'}
                  onAnswered={() =>
                    navigate(STAFF_QUEUE, {
                      state: { notice: `נשלחה תשובה למטופל בפנייה ${caseId}, והפנייה נסגרה.` },
                    })
                  }
                />
              )}
```

Take `user` from `useAuth()` (`frontend/src/auth/AuthContext.tsx` exports `useAuth(): AuthValue` with `user: Me | null`, and `Me` has `role`). Use the same navigation target and notice shape the decision path already uses in this file - read it and copy it rather than inventing a second one.

In `frontend/src/styles/app.css`, beside the other staff rules:

```css
.answer-form {
  margin-block-end: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--line-200);
  border-radius: var(--radius-md);
  background: var(--surface-100);
}

.col-sub {
  margin: 0 0 var(--space-2);
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--ink-900);
}
```

Do not add a selector that `app.css` already declares at the top level - `src/styles/app.css.test.ts` fails on a duplicate.

- [ ] **Step 7: Run the whole frontend suite and the build**

Run: `cd frontend && npm test`
Expected: 0 failures.

Run: `cd frontend && npm run build`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "Add the clinical answer form to the review screen"
```

---

### Task 6: The live demo and the documentation

**Files:**
- Modify: `CLAUDE.md`, `docs/spec_corrections.md`
- Test: none new; this task verifies the built system end to end.

- [ ] **Step 1: Restart the stack and walk the scenario**

Run: `docker compose restart backend` (the repo is mounted, so no rebuild).

In the browser at `http://localhost:5273`:
1. As `P-10041`, submit a medical question (for example `האם להפסיק את מדלל הדם לפני הבדיקה?`) and watch it reach "הועברה לצוות".
2. Sign out, sign in at `/staff/login` as `coordinator_nurse`, open the case from the queue, write an answer and submit it.
3. Sign back in as `P-10041` and confirm the answer is on the request screen as a delivered message, with the timeline ending in "הפנייה הושלמה".
4. Repeat step 2 as `admin_coordinator` on another medical question and confirm the block is locked with its explanation.

Record what you saw in the commit message. If any step behaves differently from the plan, stop and report it rather than adjusting the product to match the plan.

- [ ] **Step 2: Update `CLAUDE.md`**

- Under *Project status*, say sub-projects 1-8 are implemented and merged.
- In the *Escalation* section, after the table of resumable kinds, add: "A `MedicalQuestion` never resumes, but a `clinical_staff` reviewer can answer it: `HumanReviewService.answer()` records the text in the Data Log, binds a `ContentApproval` (§12.4) to its `content_hash`, and closes the case with `HUMAN_RESOLVED_CASE`. The State Manager verifies and consumes that approval in the same transaction, and the patient screen shows the text only when such a consumed approval exists - the read-side counterpart of T6."
- In *Working in the backend*, note that `session.py` has two delivery sources for `completed`: a `CASE_RESOLVED` row, or a clinical answer with a consumed ContentApproval.

- [ ] **Step 3: Append the decisions to `docs/spec_corrections.md`**

Append these six rows, continuing the numbering (the file ends at row 58). Keep the file's three-column shape: the open question, the decision with its reasoning, and where it lives.

```markdown
| 59 | §5 puts `AnswerClinicalQuestion` in the Action Registry with HumanReviewService as its actor, but §3 has no transition out of `AwaitingHumanReview` for a `MedicalQuestion` other than resolve or reject. How does an approved clinical answer reach the patient? | It is recorded and shown, not executed. The answer goes into the Data Log, a ContentApproval is bound to its `content_hash`, and the case closes with the existing `HUMAN_RESOLVED_CASE`; the patient screen shows the text only when that approval is present and consumed. Delivering it through the Tool Executor would have needed a new §3 row and a loosening of T1 (`Execute -> Y PolicyAllowed`) and T2 (`Execute -> InPlan`) - a clinical answer is never in the plan, because §5 lets the Planner propose only the four automatic actions. §5's own postcondition for this action is "תשובה רפואית תועדה עם approval_id": documented, not called. | `hospital_agent/human_review.py` `answer()`, `hospital_agent/session.py` `_clinical_answer()` |
| 60 | Who may answer, and on which escalations? | `clinical_staff` only (§12.4 gives ContentApproval to that role alone) and `MedicalQuestion` only (that is scenario 2 of §0). `admin_staff` can still resolve or reject. Any other escalation kind is refused with `decision_not_allowed` rather than quietly accepted. | `hospital_agent/human_review.py` `answer()`, `hospital_agent/api/routes_staff.py` |
| 61 | One approval row or two? | Two. §12.4 defines them as different things - "WorkflowDecision מאשר החלטה בתהליך; ContentApproval מאשר תוכן מסוים" - and states that a WorkflowDecision is never a medical content approval. The ContentApproval covers the text; the WorkflowDecision covers closing the case and is what `WorkflowDecisionValid` consumes. Merging them would have blurred exactly the separation the spec built. | `hospital_agent/human_review.py` `_grant_content_approval()` |
| 62 | §12.4 makes `execution_id` mandatory on a ContentApproval, but a clinical answer makes no external call. What execution does it point at? | A row written straight to `status="succeeded"`, for action `AnswerClinicalQuestion`, carrying `medical_content_flag=true` and the `content_hash`. It exists to satisfy §12.4 and to tie the approval to one exact message. It must be final at once: restart recovery escalates every execution left in `started` as `ExecutionUnknown` regardless of the case's state, so a non-final row would turn each clinical answer into a fresh escalation after every restart. | `hospital_agent/human_review.py` `answer()`, `hospital_agent/execution/recovery.py` |
| 63 | Where is the ContentApproval verified and consumed? | In the State Manager, inside the transition's own transaction, not in the Human Review Service. §6.3 requires the approval and the record to be saved together, and only the State Manager holds that transaction. The seam is one payload field (`content_approval_id`) on one event (`HUMAN_RESOLVED_CASE`), with a closed list of checks: type, case, patient, role, action, content hash against the execution row, validity and single use. Anything else blocks the transition and consumes nothing (§14). | `hospital_agent/state_manager.py` `_clinical_answer_approval()` |
| 64 | T6 protects medical content at the moment the Tool Executor sends it. What protects a clinical answer, which is never sent that way? | The same evidence, checked where the patient reads instead of where the system writes: `patient_view` returns the text only when a **consumed** ContentApproval of this case, for `AnswerClinicalQuestion`, granted by `clinical_staff`, matches the `content_hash` of an outgoing Data Log message whose content is still present. Text edited after the approval no longer matches, and a §18.4 tombstone leaves the case reading `closed` with no message. | `hospital_agent/session.py` `_clinical_answer()`, `_view()` |
```

- [ ] **Step 4: Run everything one last time**

Run: `docker compose run --rm backend pytest -q` → 0 failures.
Run: `docker compose run --rm backend python -m obs.golden` → `35`, `4`, `54`.
Run: `cd frontend && npm test` → 0 failures.
Run: `cd frontend && npm run build` → clean.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/spec_corrections.md
git commit -m "Document sub-project 8 and record its spec decisions"
```

## Done when

- A `clinical_staff` reviewer can answer a `MedicalQuestion` from the staff screen, and the patient sees that text on their request screen.
- `admin_staff` cannot, at the API (403) and on the screen (locked block with the reason).
- An answer whose ContentApproval does not verify never commits and never consumes anything.
- The full backend suite passes, the golden traces still print `35` / `4` / `54`, `npm test` passes and `npm run build` is clean.
- `docs/api.md`, `CLAUDE.md` and `docs/spec_corrections.md` describe what was built.
