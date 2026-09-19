# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Greenfield: no application code yet. The only authoritative input is the **binding demo spec** `Hospital_Agent_Clean.docx` (Hebrew, final-project scope). A Markdown copy lives in `docs/spec/`, one file per spec section: **spec §N → `docs/spec/NN-*.md`** (index: `docs/spec/README.md`). The docx is the source of truth. `docs/spec/` is generated, so don't hand-edit it. After the docx changes, regenerate it:

```bash
python scripts/spec_to_md.py
```

(Requires `python-docx`.)

- **Scope is exactly the three scenarios in §0**: normal flow with a missing document, medical escalation, and technical failure with bounded retry. The spec calls the "full characterization" (אפיון מלא) a vision document, so do not build beyond the demo.
- The spec keeps pointing to a **companion document (המסמך הנלווה)** that uses the same section numbers, and its implementation conditions are binding. It is **not in the repo**. When a detail is deferred to it, ask the user instead of inventing it.
- No build/test/lint commands exist yet. Add them here once the project is scaffolded.

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
- **OPA 1.9.0 / Rego v1** (`package hospital_agent.policy`). Precedence is Deny > RequireHumanReview > Allow > default Deny. The bundle data is `data.hospital_agent.minimized_fields` and `data.hospital_agent.approved_instruction_sources`.
- **Prolog (SWI-Prolog 9.2.9).** Handles role and action authorization and gives `explain/4` reasons. Dynamic facts are cleared and reloaded for each request, in isolation.
- **Datalog** (a subset run in SWI-Prolog with tabling). Models sensitive-field flows. `build_minimized.py` exports `flows.dl` to `minimized_fields.json`, which goes into the OPA bundle.
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
- **Policy engines:** OPA and SWI-Prolog run **inside the backend service**. There is no OPA server and no sidecar. Z3 runs through its Python bindings.
- **Backend layout:** a **single FastAPI app** with one module per spec component (§1). The components are internal boundaries, not separate services.

## Still open (confirm with the user)

- The exact model ID string for the OpenAI API.
- How OPA runs in-process from Python. OPA is written in Go, so the choices are a Wasm-compiled policy or calling the `opa` binary. With Wasm, check that the builtins the policy uses (`time.*`, `crypto.sha256`, `json.marshal`) are supported.
