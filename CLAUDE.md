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

## Working in the backend (`backend/hospital_agent/`)

- `fsm.py` is the §3 table as data. Each row keeps its Guard cell verbatim in `spec_guard`, and `tests/test_fsm.py` compares all 41 rows with `docs/spec/03-transitions-guards.md`. Change the spec first, then the row.
- A guard (`guards.py`) returns `None` when it holds, or a reason code. A fact that another component determines is trusted only from its owning component: for a system-owned event, because `StateManager` has already checked that the event came from its owner (`naming.EVENT_OWNER`); for an external event (e.g. `DOCUMENT_UPLOADED`, which has no owner), the guard itself checks `GuardContext.source` (e.g. `DocumentValid` requires `Component.SESSION_SERVICE`).
- Only `StateManager.apply()` writes State. `HUMAN_REVIEW_REQUIRED` enters only through `EscalationCoordinator.signal()` - routing every internal escalation through it, instead of letting a component emit `HUMAN_REVIEW_REQUIRED` directly, is a convention inside the trusted computing base (§6.5): the code enforces it, but §6.4's safety proofs assume every component that could call `signal()` keeps to it.
- Guards evaluated by components that don't exist yet are ports (`GuardPorts`: `ExecutorReverified` until sub-project 3). Test doubles - including the permissive monitors some State Manager unit tests use - live only in `tests/fakes.py`; application code has no permissive defaults.
- Application code builds a State Manager only through `wiring.build_state_manager()`: the real Temporal Monitor, and the policy files' hash in `rule_version`. Policy decisions go through `PolicyService.apply()`, readiness through `ReadinessCheck.run()`.
- `policy/policy.rego` is spec §8 verbatim; `policy/rules.pl` is spec §10 without its CASE-482 example facts (those live in `tests/fixtures/case_482.pl`); `policy/flows.dl` is spec §11. Tests compare all three with `docs/spec/`. `tests/opa_reference.py` must agree with the real OPA on every input in `tests/policy_inputs.py` - change them together.
- The Temporal Monitor (`policy/temporal.py`) reads guard results and evidence from the audit rows' `guards` JSON. A new rule needs its evidence recorded there by whoever emits the event; only Policy decision events may carry `evidence`.
- `db.py` mirrors the Alembic migrations, and `tests/test_schema.py` fails if they drift. A schema change is a new migration, never an edit to `0001`. Every new migration must `GRANT` the new tables to `hospital_app` (§18.2) - `SELECT, INSERT, UPDATE` for an ordinary table, but `SELECT, INSERT` only for an audit-style append-only table (as `0001` does for `audit_log`), so the DB itself, not just the app, enforces that Audit can't be changed or deleted.
- `tests/driver.py` plays the components that don't exist yet (Classifier, Planner, Policy Service, Tool Executor, reviewers). The scenario and D-tests drive cases through it.

## Hand-off to sub-project 3 (Tool Executor)

- The `TOOL_EXECUTION_STARTED` row must carry, in its `guards` JSON, the §6.1 evidence verified against stored State by `ExecutorReverified`: `InPlan`, `IdentityVerified`, `PatientContextPresent`, `AttemptsAvailable` (evaluated on the attempt count **before** the increment that happens in the same transaction), `medical_content_flag` and, for medical output, `ContentApprovalValid`; plus `execution_id` and `content_hash`. T1–T4, T6, T9 and T12 have so far run only on hand-built traces. T6 relies on `medical_content_flag` being present: a missing flag makes T6 pass trivially (fails open), so it is a hard requirement.
- `TOOL_EXECUTION_STARTED` and `AUDIT_RECORDED` must be committed together, in one transaction; a committed STARTED without AUDIT_RECORDED makes T9 block every later event of the case, including the TemporalViolation escalation.
- `decision_token` from the Policy decision is not persisted yet: write it to `executions.decision_token` with the `state_version`, `plan_hash` and step it was issued for, and have `ExecutorReverified` compare them.
- `ReadinessCheck.run(case_id, hours_until)` takes `hours_until` from its caller: derive it from the appointment time that `CheckAppointment` retrieved and stored, never from request input.
- A ContentApproval is consumed in the same transaction as the execution row that uses it (§12.4).

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
