# Spec corrections and open decisions

Found while implementing `Hospital_Agent_Clean.docx`. Every item names the section,
what the spec says, and what the code does. The spec itself is not edited here:
`docs/spec/` is generated from the docx.

## Inconsistencies in the spec

### 1. Number of Postgres tables (§1 vs §18.2)

§1 lists the Postgres Database as "טבלאות cases, executions ו־audit_log" (3 tables).
§18.2 defines 4 tables: `cases`, `executions`, `audit_log` and `approvals`.

**Code:** follows §18.2. `approvals` is required by `WorkflowDecisionValid`
(§3.1) and by the single-use rule (`consumed_at`, §18.2).

### 2. audit_log columns (§12.2 vs §18.2)

§12.2 lists the Audit record fields, including `action` and `outcome`. The
`audit_log` table in §18.2 has neither column.

**Code:** `audit_log` carries both (`alembic/versions/0001_initial_schema.py`).
`outcome` stays NULL for Transition and Blocked rows, as §12.2 requires.

## Decisions the spec leaves open

| # | Question | Decision | Where |
|---|---|---|---|
| 1 | When is a PolicyReview approval consumed? | Not at `HUMAN_APPROVED`. It stays open as `PolicyReviewOverrideValid` and is consumed by the next Policy decision (§3.1 "נצרך בהחלטת Policy הבאה"; OPA's `approval_envelope` requires `consumed_at == null`). Because it stays open, `WorkflowDecisionValid` also checks that the same `approval_id` has not already committed a `Transition` row in `audit_log` (`repository.approval_used`) - otherwise the same approval could resume a later PolicyReview escalation at the same plan_hash/step. | `fsm.py` row 38, `guards.py` `workflow_decision_valid`, `test_policy_review_approval_stays_open_for_the_next_policy_decision`, `test_policy_review_approval_cannot_approve_a_later_escalation_at_the_same_step` |
| 2 | How often is an event re-processed after a stale `state_version`? | At most 3 times, then `ReprocessLimitExceeded` and nothing is committed. | `state_manager.py` `MAX_REPROCESS` |
| 3 | What happens to `escalation_kind` after a human resumes the case? | `HUMAN_APPROVED` clears `escalation_kind` / `escalated_from_state`; the Audit keeps the history. Resolve/reject keep them on the closed case. | `fsm.py` `Effect.CLEAR_ESCALATION` |
| 4 | Reason code for a WorkflowDecision that fails a check the spec does not name | `workflow_decision_invalid`. The spec-named codes are kept: `approval_decision_mismatch`, `identity_not_established`, `patient_deadline_missing`. Three more prose conditions guard against malformed payloads that would otherwise crash `apply()` instead of failing closed (§14): `valid_classification` on `INTENT_CLASSIFIED` returns `invalid_safety_level` for a `safety_level` outside the four `SafetyLevel` values; `valid_tool_result` on `DATA_RETRIEVED` returns `invalid_tool_result` when `required_documents`/`held_documents` are present but are not lists of non-empty strings; `deadline_registered` on `MISSING_INFORMATION_DETECTED` reuses `patient_deadline_missing` when `patient_deadline` is missing, naive, or not in the future. All three are ordinary Blocked reasons - the guard never raises. | `guards.py` `workflow_decision_valid`, `valid_classification`, `valid_tool_result`, `deadline_registered` |
| 5 | Which document formats are "supported" (`RequestValid`, `DocumentValid`)? | `pdf`, `jpg`, `png`. | `guards.py` `SUPPORTED_DOCUMENT_FORMATS` |
| 6 | How is `Initial` stored in the Audit? | `state_before` is NULL on the `REQUEST_SUBMITTED` row. | `state_manager.py` `_audit_entry` |
| 7 | Can the Core record `TOOL_EXECUTION_STARTED` / `AUDIT_RECORDED`? | Not yet: `apply()` refuses them with `NonTransitionEvent`. Sub-project 3 adds a State Manager entry point that writes the ExecutionStarted/AUDIT_RECORDED pair and increments `attempt_count` in one transaction. Until then nothing in the Core increments `attempt_count`, so `AttemptsAvailable` always holds and scenario 3's retries (`test_scenario_3_technical_failure`) do not exercise the retry budget - they drive `RETRY_EXHAUSTED` directly rather than by exhausting `max_attempts`. | `state_manager.py`, `tests/test_scenarios.py` |
| 8 | What happens on a temporal violation raised by a `HUMAN_REVIEW_REQUIRED` event itself, or while the case is already in `AwaitingHumanReview`? | It is blocked and audited (`Blocked: temporal_violation:<rule>`), but `apply()` raises no further escalation signal in either case: `TemporalViolation` follow-up escalation is skipped outright when the violating event is `HUMAN_REVIEW_REQUIRED` itself (would be a signal about a signal); and when the case is already in `AwaitingHumanReview`, `EscalationCoordinator.signal()` is still called, but there is no `(AwaitingHumanReview, HUMAN_REVIEW_REQUIRED)` row in `fsm.py` for it to land on, so the signal itself is Blocked (`guard_failed`) rather than producing a second `HUMAN_REVIEW_REQUIRED` Transition. Both cases leave exactly one Blocked audit row and no escalation Transition row. | `state_manager.py` `apply()`, `fsm.py` (no `AwaitingHumanReview` outgoing row for `HUMAN_REVIEW_REQUIRED`) |
