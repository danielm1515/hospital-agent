# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Sub-projects 1–7 are implemented and merged into `main`: 1 (Core), 2 (Policy), 3 (Execution), 4 (LLM), 5 (Human Review + the authenticated API) and 7 (D33: the Response Evaluator's recall measurement, `backend/eval/`) in `backend/`; 6 (React UI, `frontend/`) served by the compose `frontend` service on `http://localhost:5173` (design and plan under `docs/superpowers/`). The authoritative input is the **binding demo spec** `Hospital_Agent_Clean.docx` (Hebrew, final-project scope). A Markdown copy lives in `docs/spec/`, one file per spec section: **spec §N → `docs/spec/NN-*.md`** (index: `docs/spec/README.md`). The docx is the source of truth. `docs/spec/` is generated, so don't hand-edit it. After the docx changes, regenerate it:

```bash
python scripts/spec_to_md.py
```

(Requires `python-docx`.)

- **Scope is exactly the three scenarios in §0**: normal flow with a missing document, medical escalation, and technical failure with bounded retry. The spec calls the "full characterization" (אפיון מלא) a vision document, so do not build beyond the demo.
- The spec keeps pointing to a **companion document (המסמך הנלווה)** that uses the same section numbers, and its implementation conditions are binding. It is **not in the repo**. When a detail is deferred to it, take the most conservative (fail-closed) reading and record it in `docs/spec_corrections.md` (see *Autonomous mode* below).
- Spec inconsistencies found while implementing, and decisions the spec leaves open, are in `docs/spec_corrections.md`.

## Autonomous mode (the user's standing instruction, 2026-09-20)

The user wants the project finished without being asked questions. Until they say otherwise:

- **Do not stop to ask.** At every decision point choose the option you would recommend, prefer the one that stays closest to the spec and fails closed, and record it: a row in `docs/spec_corrections.md` for spec-level decisions, a line in the sub-project's design doc for the rest.
- **Keep the process, drop the approval waits.** Each sub-project still gets a design doc, a prototype-validated plan, subagent-driven execution with task reviews and a final whole-branch review. The user's approval of each step is given in advance; merge a sub-project to `main` once its final review is clean and the full suite passes.
- **Parallel agents are welcome** where tasks are independent (isolated worktrees, each with its own compose project and database: `docker compose -p <name> -f docker-compose.yml -f <override without host ports>`). Never disturb the user's running stack on 54322 / 8000 except to restart it after a merge.
- **Never** read, print or commit the OpenAI key; never push to a remote; never delete user data.
- Remaining work: none within the demo's own scope (sub-projects 1-7 are all implemented and merged - see *Verification targets* and *What is left*). What is left is only what the spec itself defers: the companion document's (המסמך הנלווה) implementation conditions, not in the repo, and anything beyond the §0 demo scope, which the spec calls the vision document (אפיון מלא).

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

`AUTH_SECRET` (the key that signs the API's tokens) and `DEMO_PASSWORD` (the one password of the §18.3 demo users, default `demo`) can also be set in `.env`; both have a development default, so the demo runs without them. `CORS_ORIGINS` adds allowed origins beyond the UI dev server.

The LLM needs `OPENAI_API_KEY` (and optionally `OPENAI_MODEL`, default `gpt-5.6-luna`) in `.env` at the repo root; `.env` is git-ignored, and docker compose passes both to the backend. Without a key the server runs but the Agent Orchestrator does not start (`/health` says so). The regular tests never call the model; one smoke test does, only when asked:

```bash
docker compose run --rm -e RUN_LIVE_LLM=1 backend pytest tests/test_live_llm.py -v
```

D33 (§16, §6.5): the Response Evaluator's recall over the labelled set `backend/eval/messages.jsonl` (48 messages, 24 medical / 24 operational, mostly Hebrew). The default provider is `FakeProvider` (no network); `--live` needs `RUN_LIVE_LLM=1` and `OPENAI_API_KEY`, same gate as above. Both write `docs/d33-report.md` by default - the container's `WORKDIR` is `backend/`, so a relative `--out` resolves under `backend/`, not the repo root; leave `--out` unset unless passing an absolute path.

```bash
docker compose run --rm backend python -m eval.d33
```

```bash
docker compose run --rm -e RUN_LIVE_LLM=1 backend python -m eval.d33 --live
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
- `hospital_agent/llm/` holds the four LLM calls of §18.5 and the components around them: `provider.py` (`OpenAIProvider`; `FakeProvider`, deterministic, for tests and `obs.golden` only), `schemas.py`, `prompts/`, `classifier.py`, `planner.py`, `evaluator.py` (a separate process, §6.5), `message.py` (the fixed status template) and `orchestrator.py` (the Agent Orchestrator, which keeps no state of its own and steps each case from its stored State). Every answer is schema-checked; three unusable answers in a row escalate (`ClassificationFailed` / `PlanningFailed`). No schema lets the model assert a fact or a flag.
- `hospital_agent/auth.py` (the §18.3 demo IdP: the fixed user list and HMAC tokens), `session.py` (the patient's side: submit, upload, `patient_view`) and `human_review.py` (the staff's side: the queue, the shown context and the decision) are the components behind the `/api` routes (`api/routes_auth.py`, `routes_patient.py`, `routes_staff.py`, with `api/deps.py` for the identity). Identity always comes from the verified token - `patient_id`, `reviewer_id` and `reviewer_role` are never read from a request body (§18.3). A decision is bound to what the reviewer was shown by `shown_context_ref`; a context that changed meanwhile is refused (409 `context_changed`). The API's contract is `docs/api.md`.
- `hospital_agent/data_log.py` is the §12.3 Data Log (table `data_log`, migration 0003): request text, uploaded documents, retrieved instructions and outgoing messages. Audit keeps only `content_hash`; deletion is a tombstone.
- `hospital_agent/scripted.py` drives the patient through the real Session Service and still inserts the reviewers' approvals directly (so a test can build an invalid one); `tests/driver.py` adds test-only shortcuts that emit a Classifier / Planner / Orchestrator event directly. The Tool Executor is the only code that calls an external system (`execution/gateway.py`); `POLICY_ALLOWED` writes the `executions` intent row, and `StateManager.start_execution()` writes the STARTED / AUDIT_RECORDED pair.

## What is left

Sub-project 7 (D33) closes the last item this repo's design docs tracked. Sub-projects 1-7, including the React UI, are implemented and merged into `main`; nothing here is pending integration:

- **Sub-project 6 (React UI)** (`frontend/`: patient screen, staff screen, §1, D24) is merged into `main` and served by the compose `frontend` service on `http://localhost:5173`.
- **The golden traces (§15)** are produced by the running system, not hand-derived: `python -m obs.golden` prints `35`, `4`, `54` audit rows for the three §0 scenarios, matching the spec's expected counts.
- **The demo runs end to end**, on the same code and the same model, through exactly the three scenarios of §0 (normal flow with a missing document, medical escalation, technical failure with bounded retry) - only the patient's input and the mocked external responses change between them.
- **Anything beyond §0 is the "full characterization" (אפיון מלא) vision document** (see *Project status* above), not this demo - it is out of scope, not a gap.

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
- **D33 (§16, §6.5):** the Response Evaluator's recall over `backend/eval/messages.jsonl` (48 messages, 24 medical / 24 operational, mostly Hebrew), run with `python -m eval.d33` (`--live` for the real model). Declared threshold: `D33_RECALL_THRESHOLD = 0.95`. This is measured, never proven (§6.5): a false negative here is medical content that would have reached the patient without a `ContentApproval`. Measured: `FakeProvider` (offline, not compared to the threshold - its keyword rule is English-only against a mostly-Hebrew set) **0.1667 (4/24)**; live `gpt-5.6-luna` (`docs/d33-report.md`) **1.0000 (24/24), meets the threshold**, with 2 false positives among the 24 operational messages (a delay only, §6.5) and 0 unusable answers.

## Tech stack (decided by the user)

- **Backend:** Python + FastAPI.
- **Frontend:** React, with the patient screen and the staff screen described in §1.
- **Database:** PostgreSQL, using the four-table model in §18.2, plus the §12.3 Data Log table (`docs/spec_corrections.md` row 29).
- **LLM:** OpenAI `gpt-5.6-luna`, through the Model Selector (`llm/model_selector.py`; `OPENAI_MODEL` overrides it). It accepts no `temperature` but the default, so the calls use `reasoning_effort="none"` and strict JSON Schemas instead of §18.5's temperature 0 (`docs/spec_corrections.md` row 28). The model and a hash of the four prompts are recorded in `rule_version`.
- **Policy engines:** OPA (the `opa` 1.9.0 binary, plus a Python reference evaluator) and the Python Prolog/Datalog engines run **inside the backend service**. There is no OPA server and no sidecar, and SWI-Prolog is not installed - it is only the spec's reference engine, used to derive the expected results the tests compare against. Z3 is `z3-solver==4.15.4`, through its Python bindings.
- **Backend layout:** a **single FastAPI app** with one module per spec component (§1). The components are internal boundaries, not separate services.
