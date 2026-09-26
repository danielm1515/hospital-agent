# Exam types and preparation instructions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** every appointment has an exam type with an approved preparation instruction; the agent answers about
the appointment the patient chose, loads that appointment's instruction from the appointment-service, and the
patient can read it on the request screen and in the appointments panel.

**Spec:** `docs/superpowers/specs/2026-09-26-prep-instructions-design.md` (D1-D14, §2 constraints, §4 catalog).

## Global Constraints

- Worktrees only: appointment-service `C:/Apps/פרויקט גמר AI/appointment-service-instructions` (branch
  `feature/exam-types`; tests `MSYS_NO_PATHCONV=1 docker run --rm -v "<worktree>:/src" appt-test python -m pytest -q
  -p no:cacheprovider`, baseline 101); hospital-agent `C:/Apps/פרויקט גמר AI/hospital-agent-instructions`
  (branch `feature/prep-instructions`; backend tests ONLY via `docker compose -p instr-proto -f docker-compose.yml
  -f C:/Users/DANIEL~1.MAM/AppData/Local/Temp/claude/C--Apps------------AI/4ba1785e-5a6d-42d4-88ab-e01babb23dab/scratchpad/compose.proto.yml
  run --rm backend pytest <args>` with `MSYS_NO_PATHCONV=1`; frontend `npm ci` once, then `npm test`/`npm run build`).
  Never the owner's trees; never plain `docker compose`; never `.env`; never push; never `git stash`.
- Never weaken a safety mechanism (OPA, guards, T1-T13, Z3, prompts). `docs/spec/`, the 41 §3 rows,
  `policy.rego`, `rules.pl`, `flows.dl` stay byte-identical. Golden traces stay 35/4/54.
- §11: `instruction_system` gets no patient field; `appointment_system` may get `patient_id`, `appointment_id`.
- The LLM never supplies an appointment id or an instruction source.
- No patient text or instruction text in any log line; codes only.
- Patient UI never shows a code; staff UI shows label beside code; Hebrew/RTL; logical CSS; `tokens.css` untouched.
- Commits imperative with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; stage own paths only.

---

### Task 1 (appointment-service): exam-type catalog, tables, booking UI, seed backfill

- `app/catalog.py`: `ExamType` frozen dataclass `(code, department, label, documents: tuple[str,...],
  instruction_id, instruction_version, instruction_title, instruction_text)`; `EXAM_TYPES` exactly the 13 of
  design §4 (write the full Hebrew texts from the summaries: 3-6 short sentences each, practical, no dosing, each
  ending "טיוטת דמו – טעונה אישור רפואי. בכל שאלה רפואית יש לפנות לצוות המטפל."); `instruction_id =
  "INSTR-" + code.replace("_", "-")`, version `"1"`; `EXAMS_BY_CODE`, `exams_of(department)`,
  `default_exam(department)` (the `…_VISIT`); a test that every department has exactly one `_VISIT` and every
  exam's department and documents are in the catalogs.
- `app/models.py`: `ExamTypeRow` (`exam_types`: code PK, department, label_he, instruction_id, instruction_version,
  instruction_title, instruction_text) and `AppointmentExamType` (`appointment_exam_types`: appointment_id PK/FK
  cascade, exam_code FK). `Appointment.exam_code` property (the link's code or None).
- `main.py`: upsert `exam_types` on every start (like `_seed_document_types`); backfill APT-8391 → `NEURO_VISIT`,
  APT-8392 → `CARD_STRESS` only when they exist without a link; new seeds get links.
- Booking/edit: an `exam_type` select (required, options `data-department`, filtered client-side like doctors;
  choosing pre-ticks its `documents` client-side); server validates it belongs to the department (400 Hebrew
  message otherwise); stored in the link; edit pre-fills; dashboard column "סוג בדיקה".
- The "old schema" test pattern: a DB made before this change still starts, and its appointments resolve to the
  department default.

### Task 2 (appointment-service): API

- `AppointmentOut` gains `exam_type: {code, label}` and `instruction: {source_id, version, title}` (resolved:
  the link's exam or the department default). Update the key-set test.
- `CheckAppointment`: optional `?appointment_id=` (same id pattern as the path's patient id): that appointment if
  it belongs to the patient and is Scheduled, else `found=false` (never another patient's). Add `upcoming_count`
  (the patient's Scheduled, future appointments) to `AppointmentResult`. Without the parameter, unchanged.
- `GET /api/v1/instructions/{source_id}?version=`: same API key; `{source_id, version, title, text}`; unknown
  id or version is 404 `instruction_not_found`; audit row (`operation=GetInstruction`, no patient id); bad
  id pattern 400.
- README sections; tests for each (ownership, cancelled, unknown, upcoming_count, key-set, auth).

### Task 3 (hospital-agent): the chosen appointment and CheckAppointment's new facts

- Migration 0007: nullable `cases` columns `appointment_id`, `department`, `exam_type_label`,
  `instruction_source_id`, `instruction_version`, `upcoming_count`; `db.py` mirror; `CaseRecord` fields.
- `POST /api/patient/requests` body: optional `appointment_id` (pattern `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`,
  422 otherwise); `submit_request(..., appointment_id=None)`; stored on the case when it is opened (on the
  `REQUEST_SUBMITTED` creation - read how `open_case` creates the row); documented in `docs/api.md` §4.
- `ACTION_TARGETS[CheckAppointment]` patient fields `("patient_id", "appointment_id")` - both minimized for
  `appointment_system` (§11); `_parameters` sends `appointment_id` only when set.
- `execution/appointment_service.py`: send `?appointment_id=` when present; `map_response` reads and validates
  `appointment_id`, `department`, `exam_type.label`, `instruction.source_id`/`version`, `upcoming_count` (all
  optional in the answer; invalid shapes → `invalid_response`); `RESULT_FIELDS[CheckAppointment]` adds them;
  `Effect.RECORD_RETRIEVAL` stores them (flattened: `instruction_source_id`, `instruction_version`).
- `MockGateway.CheckAppointment` adds `instruction_source` = the demo colonoscopy source v3 (so the golden path
  keeps loading it) and nothing else new. Golden 35/4/54 unchanged.
- Tests: route (optional id, bad id 422, identity from token), client mapping, stored facts, a chosen
  appointment that is not the patient's → the existing `not_found` path (no substitute).

### Task 4 (hospital-agent): LoadInstructions from the appointment-service, source from the case

- Orchestrator: `instruction_source` for the policy and the message = the case's
  (`instruction_source_id`/`instruction_version`); none → `None` → OPA denies `unapproved_instruction_source`
  (fail closed; add a test). Remove the module constant's use (keep `InstructionSource`).
- `ACTION_TARGETS[LoadInstructions]` stays `("instruction_system", ())`; the executor passes the source as
  non-patient parameters `source_id`, `version` (from the case) - extend `_parameters` without touching patient
  fields; a test that the policy input's `patient_fields` for LoadInstructions stays `[]`.
- `AppointmentServiceGateway.call(LoadInstructions)`: `GET /api/v1/instructions/{source_id}?version=`; the answer
  must be exactly that source and version, with non-empty title and text (else `invalid_response`); returns
  `{"instruction_ids": [f"{id}:{version}"], "instruction_text": f"{title}\n{text}"}`. 404 → `error not_found`.
  Without the service configured, the fallback (mock) stays.
- `approved_instruction_sources.json`: add the 13 `INSTR-…` v1 entries (2026-01-01 → 2030-01-01); keep the three.
  Regenerate nothing else; the OPA/Prolog/Datalog/Z3 spec tests must stay green.
- `docs/spec_corrections.md` rows 90-93 (design D14).

### Task 5 (hospital-agent): the message and what the patient sees

- `llm/message.py`: design D10 templates (with exam + department when the case has them; the "additional
  appointments" sentence when no appointment was chosen and `upcoming_count > 1`); "מופיעות בפנייה זו".
- `eval/messages.jsonl` + `tests/test_d33.py`: update the template-derived messages to the new wording
  (label-only change, like row 74); the recall report is not re-run (record it in the row).
- `session.patient_view`: `completed` gains `instructions: {title, text} | null` from the case's Data Log
  `instructions` entry (present, not tombstoned); `docs/api.md` §4.
- Staff context/case detail: `appointment_id`, `department`, `exam_type_label`, instruction source (read-only,
  staff side; not part of anything patient-facing).
- Tests.

### Task 6 (hospital-agent): instruction reads for the UI, appointments list fields

- `appointment_list.py` / `AppointmentView`: add `exam_type {code,label}|null`, `instruction {source_id,
  version, title}|null` (validated like the other fields).
- `GET /api/patient/instructions/{source_id}?version=` and `GET /api/staff/instructions/{source_id}?version=`:
  answer only when the registry (`approved_instruction_sources.json`, read the same file OPA uses) approves that
  id+version and now is inside its validity - else `404 instruction_not_approved`; fetch the text from the
  appointment-service instruction endpoint (a small client reusing `appointment_list`'s transport rules); `503
  instructions_unavailable` on failure; `404 instructions_not_enabled` when not configured. `docs/api.md` §9.
- Tests (approved/unapproved/expired, not configured, unavailable, no patient data in logs).

### Task 7 (hospital-agent frontend)

- New request form: "לאיזה תור הפנייה?" select from `listMyAppointments` (next 90 days, Scheduled only):
  exam label + department + date; one → pre-selected; always "התור הקרוב ביותר" (no id); a failed load leaves
  only that option with a quiet note; sends `appointment_id`.
- RequestDetail `completed`: the instructions under the message, title + text, as a readable section.
- AppointmentsPanel: exam type label beside the department; instruction title with a "הצגת הוראות ההכנה"
  toggle that loads the text (patient/staff route); `instruction_not_approved` → "הוראות ההכנה טרם אושרו";
  no code shown to the patient.
- Staff case view: chosen appointment + exam type + source (label beside code).
- Types/client from `docs/api.md`; tests; `npm test`, `npm run build`.

### Task 8: verification, docs, CLAUDE.md

- Full suites of both repos; golden 35/4/54; consistency 7/9; fsm table; CLAUDE.md project status; the
  appointment-service README.
