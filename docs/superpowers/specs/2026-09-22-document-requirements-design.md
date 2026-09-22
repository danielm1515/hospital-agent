# Sub-projects 11-13 - required documents per appointment, and a real documents system

**Status:** approved by the owner (2026-09-22). Sub-project 11 built and live (appointment-service: 78 tests; plan `docs/superpowers/plans/2026-09-22-appointment-required-documents.md`). Sub-project 12 built and running (document-service: 123 tests, plan `docs/superpowers/plans/2026-09-22-document-service.md`); S3 awaits the owner's bucket.
**Input:** the owner's focused spec `Hospital_Agent_Document_Focused_Spec.docx` (v1.0, September
2026: "אפיון ממוקד לחיבור מערכות תורים ומסמכים"). Answers the owner gave while designing:
build all of it, in stages; an LLM classifies each PDF; files go to AWS S3; the patient uploads
in the Hospital Agent's own patient screen; validity per document type, with new 2026 copies of
the demo files (the 2024 originals demonstrate `DOCUMENT_EXPIRED`).
**Scope:** outside the numbered spec work, like sub-projects 9 and 10. No new State, Event,
Action or escalation kind; no change to OPA's rules, the Temporal Monitor or Z3 (the focused
spec §1).

## 1. What changes, in three sub-projects

| # | System | Where | What |
|---|---|---|---|
| 11 | Appointment service | the owner's `appointment-service` project | every appointment stores its own required document types, chosen in the form; `CheckAppointment` returns them |
| 12 | Document service | a new project, `document-service`, next to `appointment-service` | the patient's PDFs: intake checks, LLM classification, private S3 storage, and the patient's documents for `CheckDocuments` |
| 13 | Hospital Agent | this repo | reads the requirements from 11 and the documents from 12, computes what is missing, and sends the patient's PDF to 12 |

Each is designed, planned, built and reviewed on its own, in that order; each works and is
tested before the next starts. 11 and 12 know nothing of each other.

## 2. The document types (shared by all three)

The focused spec's catalog, one code each (§3.2 there):

| Code | Hebrew | Validity (days before the document is too old) |
|---|---|---|
| `CBC` | ספירת דם מלאה | 90 |
| `COAGULATION_TESTS` | בדיקות קרישה | 90 |
| `ECG` | תרשים פעילות חשמלית של הלב | 180 |
| `URINALYSIS` | בדיקת שתן | 90 |
| `PREOP_SUMMARY` | סיכום טרום ניתוח | 30 |

A document is valid for its type when `document_date` is at most that many days before the day it
is checked. `PREOP_SUMMARY` is its own type and never stands in for `CBC`, `COAGULATION_TESTS` or
`ECG` (focused spec §4.3). An electricity bill is not a type. The validity numbers are this
design's choice (the focused spec leaves them open); they live in one table in the document
service.

## 3. Sub-project 11 - the appointment service

- **Storage.** A catalog table `document_types(code PK, label_he, max_age_days)` seeded with §2,
  and the focused spec's link table `appointment_required_documents(appointment_id FK,
  document_type FK, PRIMARY KEY (appointment_id, document_type))`. SQLite foreign keys are turned
  on (`PRAGMA foreign_keys=ON`), so an unknown type or a dangling appointment is refused by the
  database.
- **The form.** Booking and editing get a multi-select (checkboxes) of the five types. Editing
  shows the stored selection; it is never derived from the doctor or the department (focused
  spec §3.1). Saving an edit replaces the set, in the same transaction as the appointment and its
  audit row. None selected is allowed (an appointment that needs nothing).
- **The API.** `appointment` in `GET /api/v1/patients/{id}/appointment` gains
  `required_documents: [code, ...]`, sorted. Nothing else in the contract changes.
- **The seed.** `APT-8391` (P-10041) needs `CBC`, `COAGULATION_TESTS`, `ECG` (the focused spec's
  §6.1 example); `APT-8392` needs none. Existing appointments in the live database get none.
- **A change on an open case.** The Hospital Agent re-reads the requirements every time its plan
  runs `CheckAppointment` again - after each accepted upload (§5.4). A change made while a case
  waits for the patient is picked up on the patient's next upload; nothing is pushed.

## 4. Sub-project 12 - the document service

A small FastAPI service, built like `appointment-service` (single container, SQLite for its
metadata, `X-API-Key` for callers, JSON errors as codes), on `http://localhost:8090`.

### 4.1 API

```
POST /api/v1/patients/{patient_id}/documents     multipart: file (PDF)       X-API-Key
201 {"document_id": "DOC-…", "document_type": "CBC" | null, "document_date": "2026-09-01" | null,
     "result": "ACCEPTED" | "NON_MEDICAL_DOCUMENT" | "DOCUMENT_UNREADABLE" | "DOCUMENT_EXPIRED"
               | "DUPLICATE_DOCUMENT" | "PATIENT_MISMATCH"}
GET  /api/v1/patients/{patient_id}/documents                                   X-API-Key
200 {"documents": [{"document_id", "document_type", "document_date", "result", "valid_until",
                    "uploaded_at"}, …]}
```

Validity is judged on the day a document is checked (§2), not only on the day it was uploaded:
the listing reports `result` as of today - an upload that was `ACCEPTED` but has since passed its
`valid_until` (`document_date` + the type's validity) is listed as `DOCUMENT_EXPIRED` - and gives
`valid_until` for every accepted type. The stored row keeps the result it got at upload. A
`DUPLICATE_DOCUMENT` answer carries `duplicate_of` (the accepted original's `document_id`) and the
original's `document_type` and `document_date`, so a caller whose first request timed out can
treat the retry as the document it already delivered. Every error is `{"error": code}`.

`ACCEPTED` means a readable medical document of a catalog type, the patient's own, not a
duplicate and within its validity. Whether an accepted document is *required* is not the
document service's to say: it does not know the appointment. The Hospital Agent derives the
focused spec's `ACCEPTED_REQUIRED` / `DOCUMENT_NOT_REQUIRED` itself (§5.2). This is the one
place this design departs from the focused spec's §6.2 contract - see §6.1 for why.

### 4.2 Intake, in order (the first failure decides)

1. Size at most 10 MB, and the bytes start with `%PDF-`; otherwise `DOCUMENT_UNREADABLE`.
2. `pypdf` parses it and extracts text; no text (a scan with no text layer) -> `DOCUMENT_UNREADABLE`.
3. Same patient, same SHA-256 as an earlier **accepted** upload -> `DUPLICATE_DOCUMENT`. A
   rejected file may be sent again and is checked afresh - otherwise one failed classification
   call would lock that file out for good.
4. The LLM (§4.3) classifies the text. Not medical -> `NON_MEDICAL_DOCUMENT`; medical but no
   catalog type, or no confident answer -> `DOCUMENT_UNREADABLE`.
5. A patient identifier in the text that is not this patient's -> `PATIENT_MISMATCH`. The demo
   files hide the patient's details ("מסווג"), so ownership there is the uploader.
6. No `document_date`, or older than the type's validity -> `DOCUMENT_EXPIRED` (no date is
   treated as expired: fail closed).
7. Otherwise `ACCEPTED`.

Only an `ACCEPTED` file is stored in S3. A rejected file is never stored; its metadata row keeps
the result and the hash, for the audit and the patient's list. A patient identifier is compared
only when it has the demo IdP's shape (`P-` and digits); anything else the model reports (a
document or customer number) is ignored rather than guessed at.

### 4.3 The classifier

One OpenAI call (the same model family the Hospital Agent uses), temperature/effort as there, a
strict JSON Schema: `{"is_medical": bool, "document_type": one of the five codes | null,
"document_date": "YYYY-MM-DD" | null, "patient_identifier": string | null}`. The prompt asks for
classification only - never an interpretation of a result. An answer that fails the schema, or a
call that fails, is `DOCUMENT_UNREADABLE` (fail closed); nothing is retried silently. The text
sent is the extracted text only, capped at 8,000 characters. A `FakeClassifier` (deterministic,
keyword-based) serves the tests; one live test runs only when asked (`RUN_LIVE_LLM=1`).

### 4.4 Storage

- A private S3 bucket in the owner's account, region `eu-north-1` (the RDS instance's). Block
  Public Access on; default encryption SSE-S3; TLS only (a bucket policy denying
  `aws:SecureTransport=false`).
- Object key `patients/<patient_id>/<document_id>.pdf`; no public URL, ever. Reading a file back
  is a short-lived presigned URL (5 minutes) or the service's own credentials - not needed by the
  Hospital Agent, which never reads the PDFs.
- An IAM user for this service only, with `s3:PutObject` and `s3:GetObject` on
  `arn:aws:s3:::<bucket>/patients/*` and nothing else (least privilege). Its keys live only in
  the document service's `.env`.
- Behind an `ObjectStore` interface: `S3ObjectStore` in production, an in-memory store in the
  tests (no network, no AWS in the test suite); one live test against the real bucket, only when
  asked.
- The owner creates the bucket and the IAM user (step-by-step instructions come with the plan);
  the owner also puts `OPENAI_API_KEY` and the AWS keys into the service's `.env` - no secret
  is read or copied by the implementation.

### 4.5 Audit

Every upload and every listing writes an audit row (patient, operation, result, document id,
latency) - no text, no file name, no content.

## 5. Sub-project 13 - the Hospital Agent

### 5.1 Who supplies which fact

| Fact | Today | After |
|---|---|---|
| `appointment_at` | `CheckAppointment` | `CheckAppointment` (unchanged) |
| `required_documents` | `CheckDocuments` | **`CheckAppointment`** - the appointment service owns them (focused spec §3.3) |
| `held_documents` | `CheckDocuments` | `CheckDocuments` |

`RESULT_FIELDS` moves `required_documents` from `CheckDocuments` to `CheckAppointment`, so the
document system can no longer set the requirements and the appointment system cannot set what is
held. `MockGateway` moves with it (its `CheckAppointment` returns `required_documents =
["referral", "blood_test"]`, its `CheckDocuments` only `held_documents`), so the three §0
scenarios and the golden traces keep their events and their row counts (35 / 4 / 54). The F3
guard (`valid_tool_result`) already checks both lists' shape.

### 5.2 CheckDocuments against the document service

`DocumentServiceGateway`, built like sub-project 10's gateway (standard library HTTP, no
redirects, no proxy, bounded body, timeout, the same answer mapping): `GET
/api/v1/patients/{patient_id}/documents` sends only `patient_id` (spec §11 minimized fields for
`document_system`: `document_id`, `patient_id` - unchanged). `held_documents` = the types of the
patient's `ACCEPTED` documents. The agent computes `missing = required - held` as today
(`CaseRecord.readiness_complete`); the focused spec's `ACCEPTED_REQUIRED` /
`DOCUMENT_NOT_REQUIRED` are this computation's two sides, shown to staff, never sent back. Chosen
by configuration like sub-project 10 (`DOCUMENT_SERVICE_URL` + `DOCUMENT_API_KEY`; unset -> mock).

### 5.3 The patient's upload

- A new route `POST /api/patient/requests/{case_id}/documents/file` (multipart, one PDF, at most
  10 MB). The Session Service forwards it to the document service and never stores it.
- `ACCEPTED` and of a type in the case's `required_documents` -> the Data Log records a reference
  line only ("`DOC-…` `CBC` ACCEPTED" - no medical content), and `DOCUMENT_UPLOADED` is applied
  with `document_id`, `format="pdf"`, `patient_id` and `document_type`, down the existing path
  (`DocumentValid`, then `Classifying`, T10). The Safety re-check therefore sees the reference line,
  not the lab values: the agent never interprets medical content (focused spec §5).
- `ACCEPTED` but not required -> no event; the patient is told it is not needed for this
  appointment (the focused spec's `DOCUMENT_NOT_REQUIRED`).
- Any rejection -> no event; the patient sees why, in Hebrew, per result code.
- The document service unreachable -> `503`, no event, nothing recorded.
- **The route parses the one-part multipart body itself, with the standard library** (sub-project
  13 task 3), after the patient's token is verified, instead of FastAPI's `UploadFile` - which
  would need `python-multipart` (a new runtime dependency) and would parse the body before
  authentication. The body is capped at 10 MB + 64 KiB (by `Content-Length`, before it is read)
  and at 64 parts, and it is parsed off the event loop.
- A document the document service accepted while the case moved on (`409
  not_waiting_for_document`) stays there without an event; a later retry of the same file comes
  back as `DUPLICATE_DOCUMENT` with `duplicate_of`, which the agent treats as `accepted`.
- **An exception to "only the Tool Executor calls an external system", recorded.** This call is
  the patient's own action, not a step of the plan; it is not proposed, allowed by policy or
  retried. The Session Service is the component that already owns uploads (`DocumentValid`
  requires it as the source), so it makes this one call.
- Without `DOCUMENT_SERVICE_URL`, the existing text upload stays as it is (tests, golden traces).
  The patient view gains `document_upload: "file" | "text"`, so the UI shows a file picker or the
  current text box.

### 5.4 The loop, end to end

Request -> `CheckAppointment` (time + requirements) -> `CheckDocuments` (held) ->
`LoadInstructions` -> readiness: nothing missing -> `Ready`; something missing and
`AskPatientSafe` -> `AwaitingPatientInput` with the missing codes; not safe -> human review
(focused spec's table, unchanged). An accepted, required upload -> `Classifying` -> the plan runs
again from `CheckAppointment`, so requirements and holdings are both re-read.

### 5.5 The UI

- The patient's request screen: the missing documents by their Hebrew labels (§2), a PDF picker,
  and the result of each upload.
- The staff case monitor: the case's required and held types, with Hebrew labels.

## 6. Decisions and why

1. **Only `patient_id` goes to the document service**, not the list of required tests. The list
   is medical context (which tests a patient's procedure needs); spec §11's Datalog model allows
   the document system `document_id` and `patient_id` only, and widening it would be a change to
   `flows.dl`. The agent can compute required vs held itself, so nothing is lost.
2. **Requirements move to the appointment system**, as the focused spec says (§5.1 above), with
   the mock moved in step so the demo's scenarios do not change.
3. **The Safety re-check sees a reference line, not the PDF's text** (§5.3).
4. **A rejected or not-required upload emits no event** - it cannot advance the case, and the
   patient gets an immediate answer.
5. **No date means expired; no confident classification means unreadable** (fail closed).
6. **Rejected files are not stored**; only their metadata and hash.
7. **Requirements changed on an open case are re-read on the next pass**, not pushed (§3).

## 7. Testing (per sub-project, detailed in each plan)

- 11: the tables and their constraints, the form (select, edit shows the stored set, replace on
  save), the API field, the seed, and the existing 56 tests unchanged.
- 12: every intake branch with the six demo files (the 2026 copies and the 2024 originals) through
  the `FakeClassifier` and the in-memory store; the API contract; audit rows; S3 key and settings
  (with a stub client); a live classification test and a live S3 test, only when asked.
- 13: the fact ownership move (with the golden traces still 35 / 4 / 54), the new gateway's
  mapping row by row, the upload route for every result code, the end-to-end loop with both new
  gateways over fake transports, and the UI.
- Live, on the owner's stack after each merge: book an appointment needing CBC + COAGULATION_TESTS
  + ECG, upload the six files, and watch the case reach `Ready`.
