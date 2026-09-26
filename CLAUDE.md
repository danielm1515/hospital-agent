# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Sub-projects 1–8 are implemented and merged into `main`: 1 (Core), 2 (Policy), 3 (Execution), 4 (LLM), 5 (Human Review + the authenticated API), 7 (D33: the Response Evaluator's recall measurement, `backend/eval/`) and 8 (the clinical answer: a `clinical_staff` reviewer answering a `MedicalQuestion` with a `ContentApproval`-bound text) in `backend/`; 6 (React UI, `frontend/`) served by the compose `frontend` service on `http://localhost:5273` (design and plan under `docs/superpowers/`). Sub-project 9 is the patients registry: a `patients` table other systems read through the read-only `hospital_reader` role, and the appointment-service checking every patient against it. It is separate from the numbered spec work above - added at the owner's explicit request rather than from the spec (`docs/spec_corrections.md` rows 65-68) - and only the `patients` table and `hospital_reader` role live in this repo (`backend/`, `docs/patients-registry.md`); the appointment-service side is built in the owner's separate `appointment-service` project (outside this repo, design §4 of `docs/superpowers/specs/2026-09-22-patients-registry-design.md`): with `PATIENT_REGISTRY_URL` set (its `compose.yaml` default, pointing at `hospital` as `hospital_reader`), it answers `404 patient_not_found` for a patient the registry doesn't know and `503 patient_registry_unavailable` when the registry can't be asked, and a known patient without an appointment is still `200 found=false`; with the variable unset it behaves as before. Sub-project 10 connects `CheckAppointment` to that appointment-service on the owner's stack only (`APPOINTMENT_SERVICE_URL` + `APPOINTMENT_API_KEY` in `.env`); everything else stays on the mocks (`docs/spec_corrections.md` rows 72-75). Sub-projects 11-13 (`docs/superpowers/specs/2026-09-22-document-requirements-design.md`) bring required documents per appointment and a real documents system, and all three are now done: 11 - the appointment-service stores each appointment's required document types (`CBC`, `COAGULATION_TESTS`, `ECG`, `URINALYSIS`, `PREOP_SUMMARY`) and `CheckAppointment` returns them as `required_documents`; 12 - the owner's `document-service` project (`http://127.0.0.1:8090`, its own local git, 123 tests) takes a patient's PDF (and, since sub-project 17, a JPEG or PNG photo or a scanned PDF, read by a vision call), runs the intake (readable, not an accepted duplicate, LLM-classified as a medical catalog type, the patient's own, within its validity), stores only accepted files in a private S3 bucket, and lists a patient's documents with their result as of today and `valid_until`; the S3 bucket is the owner's to create (its README). 13 - the agent now reads both: `required_documents` moved from `CheckDocuments` to `CheckAppointment` (the appointment system's own fact, `docs/spec_corrections.md` row 77), `CheckDocuments` reads the document-service's listing for `held_documents` (row 78), and the patient's PDF upload is forwarded to the document-service by the Session Service itself - not the Tool Executor, a recorded exception (row 79) - with the accepted document's catalog type becoming `DOCUMENT_UPLOADED`'s `document_id` and the document-service's own id riding along as `document_ref` (row 80); a rejected, not-required or already-held upload emits no event (row 81). `DOCUMENT_SERVICE_URL` and `DOCUMENT_API_KEY` in `.env` (both default empty) turn this on; unset, `CheckDocuments` stays on the mock and the patient keeps the text upload. Sub-project 14 (`docs/superpowers/specs/2026-09-24-admin-metrics-design.md`, `docs/spec_corrections.md` row 82) is the owner's admin metrics screen: `GET /api/admin/metrics?from=&to=` (`admin_staff` only, `require_admin`; at most 90 days) aggregates the existing `audit_log`, `executions`, `cases` and `approvals` tables read-only in one `REPEATABLE READ` snapshot (`hospital_agent/metrics.py`), and `/staff/metrics` shows it; migration 0005 adds three indexes and is its only write. Sub-project 15 (`docs/superpowers/specs/2026-09-25-patient-requests-design.md`, `docs/spec_corrections.md` rows 83-88) lets a staff member ask the patient a clarifying question or for one catalog document, and close or reject with a closing message: templates for any staff member, free text for `clinical_staff` only under a `ContentApproval` (`message_approval_id`). It adds `AwaitingPatientReply` and `PATIENT_REPLY_REQUESTED` / `PATIENT_REPLY_SUBMITTED` as a marked extension (`naming.EXTENSION_*`, `fsm.EXTENSION_TRANSITIONS`) - the spec's own lists and their tests are unchanged - and a case a person has written to never goes back to the agent (`WorkflowDecisionValid` refuses `HUMAN_APPROVED` with `human_engaged`; T13 in the Temporal Monitor). Migration 0006 adds three `cases` columns and widens `ck_approvals_decision` and `ck_data_log_kind`. The patient sees a staff message only when its hash is on a committed Transition row and it is a fixed template (`patient_messages.py`) or its clinical `ContentApproval` was consumed (design §7.5); the API is `docs/api.md` §8. Sub-project 16 (`docs/superpowers/specs/2026-09-26-appointment-list-design.md`, `docs/spec_corrections.md` row 89) shows the patient's appointments: the appointment-service's `GET /api/v1/patients/{id}/appointments?from=&to=` (both statuses, at most 100 with `truncated`, compared in Israel time), read by `hospital_agent/appointment_list.py` - a second recorded exception beside row 79, read-only and outside the FSM, validating every row (the asked patient, an aware time, normalised to UTC) - through `GET /api/patient/appointments` and `GET /api/staff/cases/{id}/appointments` (`docs/api.md` §9; default now..+30 days, at most 366, `422 invalid_range`, `503 appointments_unavailable`), and the shared `AppointmentsPanel` (`frontend/src/components/`) on "הפניות שלי" and the review screen, with a day-range filter (default today..+30). Without `APPOINTMENT_SERVICE_URL`/`APPOINTMENT_API_KEY` the routes answer `404 appointments_not_enabled` - there is no mock list. Sub-project 17 (`docs/superpowers/specs/2026-09-26-staff-fixes-design.md`) fixes the owner's review list without weakening any safety mechanism: every LLM attempt is logged as one code-only line (`llm/telemetry.py`; `hospital_agent/logging_setup.py` finally configures the app's INFO lines), a permanent provider error (auth, not found, `insufficient_quota`) is not retried but escalates exactly as before, `/health` carries `llm: ok|error|unknown` and the staff-only `GET /api/staff/system-status` feeds a staff banner; the document-service now answers a provider failure `503 classifier_unavailable` instead of calling the file unreadable, tags every refusal with a `reason`, and reads JPEG/PNG and scanned PDFs through a vision call; the staff lists are one paginated call each (`{items, next_cursor}`), the Case Monitor filters by the five `state_groups.STATE_GROUPS`, the review queue is ordered newest entry into AwaitingHumanReview first in one SQL statement, one `components/Loading.tsx` is every loader, and the decision notice is transient. The authoritative input is the **binding demo spec** `Hospital_Agent_Clean.docx` (Hebrew, final-project scope). A Markdown copy lives in `docs/spec/`, one file per spec section: **spec §N → `docs/spec/NN-*.md`** (index: `docs/spec/README.md`). The docx is the source of truth. `docs/spec/` is generated, so don't hand-edit it. After the docx changes, regenerate it:

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
- Remaining work: none within the demo's own scope (sub-projects 1-8 are all implemented and merged - see *Verification targets* and *What is left*). What is left is only what the spec itself defers: the companion document's (המסמך הנלווה) implementation conditions, not in the repo, and anything beyond the §0 demo scope, which the spec calls the vision document (אפיון מלא).

## Commands

Run from the repo root. The backend runs in Docker (Python 3.13). The repo is mounted into the container, so code changes need no rebuild; dependency changes do.

```bash
docker compose up --build
```

Postgres on `localhost:54322` (database `hospital`, owner `hospital_owner`; the app connects as `hospital_app`), API on `localhost:8000` (`BACKEND_HOST_PORT` overrides the host side: after a reboot Windows can put 8000 inside a Hyper-V / WinNAT excluded port range - `netsh interface ipv4 show excludedportrange protocol=tcp` - and then nothing may bind it; start with e.g. `BACKEND_HOST_PORT=8200 docker compose up -d`. The UI never needs it, since it reaches the API as `backend:8000` inside the compose network), and the UI on `localhost:5273` (the Vite dev server, listening on `5173` inside the container - `docker-compose.yml` maps `127.0.0.1:5273:5173` because another of the owner's projects holds `5173` on the host - proxying `/api` to the backend so the browser needs no CORS). Migrations run on start.

**The database can live on a managed Postgres (AWS RDS today).** The four database URLs in `docker-compose.yml` default to the local `db` container, and `.env` (git-ignored) overrides them: `DATABASE_URL` / `TEST_DATABASE_URL` as `hospital_app`, `MIGRATION_DATABASE_URL` / `TEST_MIGRATION_DATABASE_URL` as the instance's master user, all with `?sslmode=require`, plus a strong `READER_DB_PASSWORD`. `.env` currently points everything - the live `hospital` database **and** the tests' `hospital_test` - at the RDS instance; delete those lines to go back to the container. `db/init/` never reaches a managed instance, so run this once against a new one, before the backend first migrates it (idempotent; it creates `hospital_app`, `hospital` and `hospital_test`, and never drops anything):

```bash
docker compose run --rm backend python ../scripts/managed_db_bootstrap.py
```

A managed instance's master user is not a real superuser (on RDS it is `rds_superuser`), and two things differ there: migration 0004 cannot revoke `PUBLIC`'s `EXECUTE` on the large-object functions, which belong to `rdsadmin` - Postgres only warns - so `hospital_reader` can create large objects on RDS, and `tests/test_patients.py` reports that as a strict `xfail`; and the superuser-refusal test is skipped, because nobody but `rdsadmin` can create a `SUPERUSER` role there. The `managed_postgres` fixture (`tests/conftest.py`) is how the tests tell. A third difference needs no fixture: the master user only has `ADMIN OPTION` on a role it creates, so `throwaway_reader` grants itself the role before `DROP OWNED BY` (`docs/spec_corrections.md` row 71). A full run against RDS takes about 54 minutes, against the local container a few. The local `db` container still starts with the stack and keeps its old data; with `.env` pointing at RDS nothing uses it.

```bash
cd frontend && npm install
npm test
npm run build
```

The frontend needs Node 22 (matching the `node:22-alpine` image `frontend/Dockerfile` uses). `npm test` runs Vitest once (`vitest run`); `npm run build` runs `tsc -b` then `vite build`. Both run outside Docker, against the checked-out `frontend/` tree, and touch no service.

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

`AUTH_SECRET` (the key that signs the API's tokens) and `DEMO_PASSWORD` (the one password of the §18.3 demo users, default `demo`) can also be set in `.env`; both have a development default, so the demo runs without them. `CORS_ORIGINS` adds allowed origins beyond the UI dev server. `READER_DB_PASSWORD` (default `hospital_reader_dev`) sets `hospital_reader`'s password when migration 0004 first creates that role; an existing role keeps its password on a later run.

Sub-project 10: `APPOINTMENT_SERVICE_URL` and `APPOINTMENT_API_KEY` (both default empty) point `CheckAppointment` at the owner's appointment-service instead of `MockGateway`. Without the URL the mock is used, exactly as before. With the URL set but the key empty, or the URL not an http(s) URL, the configuration is refused and the Agent Orchestrator does not start, the same fail-closed pattern as a missing OpenAI key; `/health` reports which - `"appointments": "mock"` or `"appointment-service"`, and the refusal reason under `"orchestrator"`. From inside the backend container the service is `http://host.docker.internal:8080`.

Sub-project 13: `DOCUMENT_SERVICE_URL` and `DOCUMENT_API_KEY` (both default empty) point `CheckDocuments` at the owner's document-service instead of `MockGateway`, the same fail-closed pattern as sub-project 10 - a URL set with the key empty, or a URL that is not http(s), refuses the configuration and the Agent Orchestrator does not start. Without the URL the mock is used, exactly as before. `/health` reports `"documents": "mock"` or `"document-service"` whenever the orchestrator is running (the document gateway is built first and, when configured, is the appointment gateway's fallback, so `CheckAppointment` and `CheckDocuments` can each reach their own real system independently); both fields are absent only when the orchestrator itself did not start. The same two variables also turn on the patient's PDF upload route (`POST .../documents/file`, sub-project 13 task 3, independent of the Agent Orchestrator): without them the patient view's `document_upload` stays `"text"` and that route answers `404 file_upload_not_enabled`. From inside the backend container the service is `http://host.docker.internal:8090`.

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

- `fsm.py` is the §3 table as data. Each row keeps its Guard cell verbatim in `spec_guard`, and `tests/test_fsm.py` compares all 41 rows with `docs/spec/03-transitions-guards.md`. Change the spec first, then the row. Sub-project 15's three rows live in `fsm.EXTENSION_TRANSITIONS`, outside the 41; `resolve()` searches both.
- A guard (`guards.py`) returns `None` when it holds, or a reason code. A fact that another component determines is trusted only from its owning component: for a system-owned event, because `StateManager` has already checked that the event came from its owner (`naming.EVENT_OWNER`); for an external event (e.g. `DOCUMENT_UPLOADED`, which has no owner), the guard itself checks `GuardContext.source` (e.g. `DocumentValid` requires `Component.SESSION_SERVICE`).
- Only `StateManager.apply()` writes State. `HUMAN_REVIEW_REQUIRED` enters only through `EscalationCoordinator.signal()` - routing every internal escalation through it, instead of letting a component emit `HUMAN_REVIEW_REQUIRED` directly, is a convention inside the trusted computing base (§6.5): the code enforces it, but §6.4's safety proofs assume every component that could call `signal()` keeps to it.
- Guards evaluated outside the State Manager are ports (`GuardPorts`); `wiring` plugs in the real `ExecutorReverified` (`execution/verify.py`). Test doubles - including the permissive monitors some State Manager unit tests use - live only in `tests/fakes.py`; application code has no permissive defaults.
- Application code builds a State Manager only through `wiring.build_state_manager()`: the real Temporal Monitor, and the policy files' hash in `rule_version`. Policy decisions go through `PolicyService.apply()`, readiness through `ReadinessCheck.run()`.
- `policy/policy.rego` is spec §8 verbatim; `policy/rules.pl` is spec §10 without its CASE-482 example facts (those live in `tests/fixtures/case_482.pl`); `policy/flows.dl` is spec §11. Tests compare all three with `docs/spec/`. `tests/opa_reference.py` must agree with the real OPA on every input in `tests/policy_inputs.py` - change them together.
- The Temporal Monitor (`policy/temporal.py`) reads guard results and evidence from the audit rows' `guards` JSON. A new rule needs its evidence recorded there by whoever emits the event; only Policy decision events may carry `evidence`.
- `db.py` mirrors the Alembic migrations, and `tests/test_schema.py` fails if they drift. A schema change is a new migration, never an edit to `0001`. Every new migration must `GRANT` the new tables to `hospital_app` (§18.2) - `SELECT, INSERT, UPDATE` for an ordinary table, but `SELECT, INSERT` only for an audit-style append-only table (as `0001` does for `audit_log`); `patients` (migration 0004) breaks that pattern deliberately - `hospital_app` gets `SELECT` only, because nothing in the agent's flow writes a patient - so the DB itself, not just the app, enforces both that Audit can't be changed or deleted and that this table can't be written by the application.
- `patients` is seeded by migration 0004 and mirrors `DEMO_USERS`; `tests/test_patients.py` fails if they drift. `hospital_reader` has `SELECT` on `patients` only, and 0004 also revokes large-object creation and `TEMPORARY` from `PUBLIC` and caps the role at 5 connections; the contract for other systems is `docs/patients-registry.md`. The `app_engine` fixture (`tests/conftest.py`) deliberately does not `TRUNCATE` `patients` between tests, unlike the other application tables. Roles are cluster-wide and `hospital` and `hospital_test` share one cluster, so no test alters or drops the real `hospital_reader`: the tests that re-create the role point 0004 at a throwaway `hospital_reader_t_<id>` through the Alembic config attribute `reader_role` (fixture `throwaway_reader`).
- `hospital_agent/llm/` holds the four LLM calls of §18.5 and the components around them: `provider.py` (`OpenAIProvider`; `FakeProvider`, deterministic, for tests and `obs.golden` only), `schemas.py`, `prompts/`, `classifier.py`, `planner.py`, `evaluator.py` (a separate process, §6.5), `message.py` (the fixed status template) and `orchestrator.py` (the Agent Orchestrator, which keeps no state of its own and steps each case from its stored State). Every answer is schema-checked; three unusable answers in a row escalate (`ClassificationFailed` / `PlanningFailed`). No schema lets the model assert a fact or a flag.
- `hospital_agent/auth.py` (the §18.3 demo IdP: the fixed user list and HMAC tokens), `session.py` (the patient's side: submit, upload, `patient_view`) and `human_review.py` (the staff's side: the queue, the shown context and the decision) are the components behind the `/api` routes (`api/routes_auth.py`, `routes_patient.py`, `routes_staff.py`, with `api/deps.py` for the identity). Identity always comes from the verified token - `patient_id`, `reviewer_id` and `reviewer_role` are never read from a request body (§18.3). A decision is bound to what the reviewer was shown by `shown_context_ref`; a context that changed meanwhile is refused (409 `context_changed`). The API's contract is `docs/api.md`.
- `session.py`'s `upload_pdf` (sub-project 13, design §5.3) forwards the patient's PDF to the document-service through `document_intake.py`'s `DocumentIntakeClient` - not a `ToolGateway`, and one of two recorded exceptions to "only the Tool Executor calls an external system" (`docs/spec_corrections.md` row 79; the other is sub-project 16's read-only `appointment_list.py`, row 89), because it is the patient's own action, never proposed, policy-checked or retried. It computes the effective type (an `ACCEPTED` answer's `document_type`, or a `DUPLICATE_DOCUMENT`'s, treating a timed-out retry as the document already delivered), and either applies `DOCUMENT_UPLOADED` with that type as `document_id` and the document-service's own id as `document_extra["document_ref"]` (row 80), or - not required, already held, or rejected - returns an outcome code with no event at all (row 81). The route (`POST /api/patient/requests/{case_id}/documents/file`, `api/routes_patient.py`) is offered only when the patient view's `document_upload` is `"file"`, which needs both `DOCUMENT_SERVICE_URL` and `DOCUMENT_API_KEY`; unset, the case keeps the text upload.
- `session.py`'s `patient_view` has two delivery sources for `completed`: a `CASE_RESOLVED` row (the agent's own status update), or a clinical answer - a **consumed** `ContentApproval` for `AnswerClinicalQuestion`, granted by `clinical_staff`, whose `content_hash` matches an outgoing Data Log message that is still present (`_clinical_answer()`).
- `hospital_agent/data_log.py` is the §12.3 Data Log (table `data_log`, migration 0003): request text, uploaded documents, retrieved instructions and outgoing messages. Audit keeps only `content_hash`; deletion is a tombstone.
- `hospital_agent/scripted.py` drives the patient through the real Session Service and still inserts the reviewers' approvals directly (so a test can build an invalid one); `tests/driver.py` adds test-only shortcuts that emit a Classifier / Planner / Orchestrator event directly. The Tool Executor is the only code that calls an external system for a plan step (`execution/gateway.py`, plus `execution/appointment_service.py` for sub-project 10's `CheckAppointment` and `execution/document_service.py` for sub-project 13's `CheckDocuments`, both over HTTP through the transport the two share, `execution/http.py` - no redirect, no proxy, a bounded body; `build_gateway()`/`build_document_gateway()` choose the real gateway over `MockGateway` by configuration, and the document gateway is the appointment gateway's fallback so each moves independently); `POLICY_ALLOWED` writes the `executions` intent row, and `StateManager.start_execution()` writes the STARTED / AUDIT_RECORDED pair. `RESULT_FIELDS` (`execution/gateway.py`) is which result fields each action's owning system may set - sub-project 13 moved `required_documents` from `CheckDocuments` to `CheckAppointment` there, and `MockGateway` with it (`docs/spec_corrections.md` row 77).

## Working in the frontend (`frontend/`)

- **Layout:** `src/api/` (the one `/api` client, `client.ts`, plus `types.ts`); `src/auth/` (`AuthContext`, the token and `role`); `src/components/` (`Logo`, `Button`, `TextField`, `Alert`, `StatusPill`, `AuthLayout`, `AppShell`, `ThemeToggle`); `src/pages/patient/*` and `src/pages/staff/*` (one route file per screen, plus each area's `*Routes.tsx`); `src/styles/` (`tokens.css`, `app.css`). Screens and components each have a co-located `*.test.tsx`; a few files don't (`AuthContext`, `AuditTimeline`, `PatientRoutes.tsx`, `icons`, `main`, `src/test/helpers`) because they are exercised through the tests of what renders or uses them, wire up routing only, or are icon/bootstrap code with no logic of their own.
- **`src/styles/tokens.css` is a byte-identical copy of `design/ramon-ui/tokens.css`.** `src/styles/tokens.test.ts` diffs the two files and fails on any drift; edit the design copy and re-copy it, never hand-edit `tokens.css` in place.
- **Every shape the UI renders or sends comes from `docs/api.md`, and nothing else.** No field, status value, `escalation_kind` or route is invented client-side; if the UI needs something `docs/api.md` doesn't have, that is a sub-project 5 gap to fix there, not to paper over here.
- **The Case Monitor's expanded row reads `GET /api/staff/cases/{id}/context`**, which answers for a case in any
  State and carries the Data Log and the audit trace together: the correspondence with the patient (what they
  wrote, what they uploaded, the instructions that were loaded, the message that was sent) and the trace, in one
  request. It never sends `shown_context_ref` back - that binds a decision, and deciding and §18.4 deletion stay
  on the review screen.
- **A Latin run inside Hebrew text is isolated, never left to pick its own direction.** `.mono` uses
  `unicode-bidi: isolate` (a guard test in `src/styles/app.css.test.ts` fails on `plaintext`): the line keeps its
  RTL direction, so a code sits where its Hebrew label is instead of jumping to the opposite edge, and the code
  itself still reads left-to-right. Where the order of two codes carries meaning - which State a row came from and
  which it went to - words say it ("ממצב X למצב Y"), because an arrow between them reads either way.
- **Hebrew and RTL throughout:** `frontend/index.html` sets `<html lang="he" dir="rtl">`, and `app.css` uses logical CSS properties only (`margin-inline-start`, `inset-inline-end`, etc.), never physical ones (`margin-left`, `right`) - a physical property silently mirrors wrong under RTL instead of failing.
- **Sub-project 13's file upload:** the patient view's `document_upload` (`"file"` | `"text"`) picks which the `needs_document` screen offers. `"file"` shows a document picker (`RequestDetail.tsx`, `ReplyToRequest.tsx`; PDF, JPEG or PNG since sub-project 17, `isAcceptedDocumentFile` in `helpers.ts`, refusing anything else or a file over 10 MB client-side before it is even sent) that posts to `POST .../documents/file`; the result of each upload is a Hebrew sentence per outcome code (`accepted`, `not_required`, `already_received`, `not_medical`, `unreadable`, `expired`, `not_yours`, and sub-project 17's finer `unrecognised_type`, `unreadable_scan`, `bad_date`, `no_date`, `unsupported_format`, `too_large` from the document-service's `reason`), never the raw code. `documentLabel` (`helpers.ts`) adds the sub-project 11 catalog types (`CBC`, `COAGULATION_TESTS`, `ECG`, `URINALYSIS`, `PREOP_SUMMARY`) beside the demo's older ids, falling back to the id alone for one it does not know. The staff Case Monitor shows the same required/held lists with the same labels (`CaseMonitor.tsx`, `labels.ts`).
- **The patient UI must never render an escalation kind, a policy reason or an Audit row (§12.3).** It shows only the abstract status the API returns (`received`, `in_progress`, `needs_document`, `needs_reply`, `in_review`, `completed`, `closed`) plus, for `needs_document`, the missing document ids and template, for `needs_reply`, the staff's request (`reply_request`), for `completed`, the delivered message, for `closed`, the staff's closing message when there is one, and the `conversation` the server already filtered (sub-project 15). No error code or upload outcome code reaches the patient raw: each maps to a Hebrew sentence in `pages/patient/helpers.ts`, with a generic fallback. The request screen's timeline is built from the view's `history` - the same seven abstract statuses with the time the case entered each - so it can say what happened and when without exposing a State or an event. `AuditTimeline` and the raw `escalation_kind`/reasons are staff-only (`src/pages/staff/`).
- **The staff screens label every code they show, never replace it.** A State, an `intent`, a `safety_level` or an
  `escalation_kind` is printed with its Hebrew label beside it (`labels.ts`), and a code the labels don't know
  falls back to itself - the code is what the spec, the Audit and the guards use, so it has to stay on screen.
- **Components mirror `design/ramon-ui/components.html`:** same class names, same markup shape, same tokens (surfaces, lines, radii, `.state` mono) for the pieces the design doesn't show (table, StatusPill, timeline, top nav) - see design decision 6.
- **Tests are Vitest + Testing Library and must not hit the network.** `src/test/setup.ts` resets `sessionStorage`/`localStorage` and mocks `matchMedia` before each test; every page/component test stubs `fetch` (or the API client) instead of calling the real backend, so `npm test` runs standalone, with no backend or database up.

## What is left

Sub-project 8 (the clinical answer) closed the last item the §0 demo needed. Sub-projects 1-8, including the React UI, are implemented and merged into `main`; nothing here is pending integration. (Sub-project 9, the patients registry, and sub-project 10, the appointment-service integration, are both separate from the demo - see *Project status* - and are not part of this list.)

- **Sub-project 6 (React UI)** (`frontend/`: patient screen, staff screen, §1, D24) is merged into `main` and served by the compose `frontend` service on `http://localhost:5273`.
- **The golden traces (§15)** are produced by the running system, not hand-derived: `python -m obs.golden` prints `35`, `4`, `54` audit rows for the three §0 scenarios, matching the spec's expected counts.
- **The demo runs end to end**, on the same code and the same model, through exactly the three scenarios of §0 (normal flow with a missing document, medical escalation, technical failure with bounded retry) - only the patient's input and the mocked external responses change between them.
- **Anything beyond §0 is the "full characterization" (אפיון מלא) vision document** (see *Project status* above), not this demo - it is out of scope, not a gap.

## What the system is

A hospital patient-service agent that handles *operational* requests (appointment status, required documents, approved preparation instructions). It must **never give medical answers automatically**. The design principle is "the LLM proposes, deterministic layers decide". Each case is an event-driven state machine. Every step the LLM proposes must pass layered formal checks before any external call. Any unknown condition **fails closed**: the system stops and escalates to a human, never continues (§14).

## Architecture (big picture)

**State machine (§2, §3).** There are 12 states and 26 events. The transition table in §3 plus the guards in §3.1 are the *only* legal transitions - the spec's own lists and their tests (`test_naming`, `test_fsm`, `test_guards`) are unchanged. Sub-project 15 adds one state and two events on top of that, as a marked extension outside those closed lists: `naming.EXTENSION_STATES` / `naming.EXTENSION_EVENTS` and `fsm.EXTENSION_TRANSITIONS`, kept separate from the spec's 12/26/41 and checked by their own test (`test_extension.py`), never folded into the counts above. **Only the State Manager writes state.** If no table row's guards hold for a (state, event) pair, the event is `Blocked: guard_failed`: state stays unchanged and a `Blocked` audit row is written. Terminal states (`Completed`, `Failed`) absorb all later events.

**Every transition is one Postgres transaction (§18.2).** It reads `cases` at the current `state_version`, INSERTs into `audit_log`, UPDATEs `cases ... WHERE state_version=?`, and, when needed, UPDATEs `executions` and `approvals.consumed_at`. If zero rows update, the event is reprocessed against the fresh state. The Temporal Monitor must approve the extended trace before commit. If the Monitor is unavailable, nothing commits. There are six tables: the four of §18.2 - `cases`, `executions` (the outbox), `audit_log` (the app role has INSERT only), and `approvals` - plus `data_log` (§12.3, deletable with a tombstone) and `patients` (sub-project 9's read-only registry for other systems, outside §18.2).

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
- **Temporal Monitor.** Evaluates the past-time LTL rules T1–T12 (§6), plus sub-project 15's T13 (no agent state after `PATIENT_REPLY_REQUESTED`), over each case's audit trace before every commit. A violation blocks the transition and escalates as `TemporalViolation`.

**LLM (§18.5).** One model, four separate calls: Intent, Safety, Planner, and Response Evaluator. Each has its own prompt, temperature 0, and a JSON Schema. The Evaluator never sees the Planner's prompt. Output that fails its schema is rejected, and 3 consecutive schema failures escalate (`ClassificationFailed` / `PlanningFailed`). Model and prompt versions go into `rule_version`. **The LLM never supplies authoritative facts.** It cannot set `approved`/`valid` flags or `outgoing_message.evaluated` (only the Response Evaluator sets that), and it cannot assert approvals.

**Escalation.** Internal components only *signal*. Only the **Escalation Coordinator** emits the canonical `HUMAN_REVIEW_REQUIRED` event, carrying `escalation_kind` and `escalated_from_state`. `HUMAN_APPROVED` can resume a case only for these kinds, each with a required field:

| `escalation_kind` | Required field |
|---|---|
| `PatientVerificationFailed` | `verified_identity_ref` |
| `RetryExhausted` | none (opens a new retry cycle) |
| `PolicyReview` | a one-shot override bound to `plan_hash` + `current_step` |
| `Z3Counterexample` | new `patient_deadline` |
| `PatientSlaExpired` | new `patient_deadline` |

Every other escalation (SafetyEscalation, TemporalViolation, PolicyDenied, …) can only be **resolved or rejected**. `MedicalQuestion` can be resolved or rejected too, or a `clinical_staff` reviewer can answer it instead (below).

A `MedicalQuestion` never resumes, but a `clinical_staff` reviewer can answer it: `HumanReviewService.answer()` records the text in the Data Log, binds a `ContentApproval` (§12.4) to its `content_hash`, and closes the case with `HUMAN_RESOLVED_CASE`. The State Manager verifies and consumes that approval in the same transaction, and the patient screen shows the text only when such a consumed approval exists - the read-side counterpart of T6.

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
- **Demo stubs:** the IdP is a fixed user list (§18.3). External systems (appointments, documents, instructions, patient channel) are mocks. All three scenarios run on the same code and model, and only patient input and mock responses change. On the owner's own stack, `CheckAppointment` may instead reach the real appointment-service (sub-project 10) and `CheckDocuments` the real document-service (sub-project 13); `LoadInstructions` and `SendStatusUpdate` stay mocks there too.
- **D33 (§16, §6.5):** the Response Evaluator's recall over `backend/eval/messages.jsonl` (48 messages, 24 medical / 24 operational, mostly Hebrew), run with `python -m eval.d33` (`--live` for the real model). Declared threshold: `D33_RECALL_THRESHOLD = 0.95`. This is measured, never proven (§6.5): a false negative here is medical content that would have reached the patient without a `ContentApproval`. **It bounds the automatic path only** - the message the agent composed and `SendStatusUpdate` delivers. A clinical answer (sub-project 8) never reaches the Evaluator: it is medical by assumption and is authorised by the `ContentApproval` bound to its `content_hash`, so there is no classification there to get wrong, and `tests/test_clinical_answer.py` is where that path is tested. Measured: `FakeProvider` (offline, not compared to the threshold - its keyword rule is English-only against a mostly-Hebrew set) **0.1667 (4/24)**; live `gpt-5.6-luna` (`docs/d33-report.md`) **1.0000 (24/24), meets the threshold**, with 2 false positives among the 24 operational messages (a delay only, §6.5) and 0 unusable answers. That live figure was measured on the status template's earlier `(UTC)` wording and was not re-run after sub-project 10 changed only that label to `(שעון ישראל)` - a label-only change, not a re-measurement (`docs/spec_corrections.md` row 74).

## Tech stack (decided by the user)

- **Backend:** Python + FastAPI.
- **Frontend:** React, with the patient screen and the staff screen described in §1.
- **Database:** PostgreSQL, using the four-table model in §18.2, plus the §12.3 Data Log table (`docs/spec_corrections.md` row 29), plus `patients` (`docs/spec_corrections.md` rows 65-68).
- **LLM:** OpenAI `gpt-5.6-luna`, through the Model Selector (`llm/model_selector.py`; `OPENAI_MODEL` overrides it). It accepts no `temperature` but the default, so the calls use `reasoning_effort="none"` and strict JSON Schemas instead of §18.5's temperature 0 (`docs/spec_corrections.md` row 28). The model and a hash of the four prompts are recorded in `rule_version`.
- **Policy engines:** OPA (the `opa` 1.9.0 binary, plus a Python reference evaluator) and the Python Prolog/Datalog engines run **inside the backend service**. There is no OPA server and no sidecar, and SWI-Prolog is not installed - it is only the spec's reference engine, used to derive the expected results the tests compare against. Z3 is `z3-solver==4.15.4`, through its Python bindings.
- **Backend layout:** a **single FastAPI app** with one module per spec component (§1). The components are internal boundaries, not separate services.
