# HTTP API contract

The backend's complete public interface. Sub-project 6 (the React UI) is built from this
file alone: every route, every request body, every response body and every error code is
here, and the JSON below is real output of the running system. (FastAPI's own
`/openapi.json`, `/docs` and `/redoc` are also reachable without a token in this demo
build - see the note under the route table.)

- **Base URL (demo):** `http://localhost:8000`
- **Content type:** `application/json`, UTF-8. Text may be Hebrew.
- **Timestamps:** ISO 8601 in UTC, e.g. `2026-09-19T22:12:47.693947Z`. The one timestamp
  the client sends (`patient_deadline`) must carry a timezone offset.
- **Errors:** always `{"detail": "<code>"}` - a short, stable code, never a sentence, never
  internal detail and never the value that was sent. A body, query or path that fails
  validation is `422 {"detail": "invalid_body"}`, with no field list and no echo of the
  input; the UI validates the form itself (the rules below are exact) and treats a 422 as
  "the form is invalid".
- **Identity is never sent in a body.** `patient_id`, `reviewer_id` and `reviewer_role`
  come from the token (§18.3). Extra fields in a request body are ignored.
- **The patient never sees** an escalation kind, a policy reason or an audit row (§12.3).

## 1. Authentication

The demo IdP (§18.3) is a fixed user list with one shared password. `POST /api/auth/login`
returns a stateless token; send it on every other `/api` request:

```
Authorization: Bearer <token>
```

The token is `base64url({"exp":…,"role":…,"sub":…}).<hmac-sha256>`, valid **8 hours**. It
is opaque to the UI: store it (e.g. in `sessionStorage`), send it, and on `401` send the
user back to the login screen. There is no refresh endpoint and no server session - log in
again.

### Demo users

| `user_id` | `role` | `display_name` | Note |
|---|---|---|---|
| `P-10041` | `patient` | דנה כהן | The patient of all three §0 scenarios |
| `P-20000` | `patient` | יוסי לוי | A second patient (privacy checks) |
| `P-30000` | `patient` | מיכל אברהם | Identity does not verify: every request of hers goes to review as `PatientVerificationFailed` |
| `coordinator_nurse` | `clinical_staff` | אחות מתאמת | Staff screen |
| `admin_coordinator` | `admin_staff` | רכזת מנהלה | Staff screen |

**Password:** the same for all of them - the value of `DEMO_PASSWORD`, default `demo`
(set in `.env` at the repo root). `AUTH_SECRET` (also `.env`) signs the tokens; changing it
invalidates every issued token.

`role` is `patient`, `clinical_staff` or `admin_staff`. The two staff roles have exactly
the same API rights here; the UI shows the staff screen for both (`role !== "patient"`).

### CORS

The API allows `http://localhost:5273` and `http://127.0.0.1:5273` (the Vite dev server),
plus any comma-separated origin in the `CORS_ORIGINS` environment variable. Allowed
methods: `GET`, `POST`, `DELETE`, `OPTIONS`. Allowed headers: `Authorization`,
`Content-Type`. Credentials (cookies) are not used.

## 2. Route table

| Method | Path | Who | What |
|---|---|---|---|
| GET | `/health` | public | Liveness and the database |
| POST | `/api/auth/login` | public | Log in, get a token |
| GET | `/api/auth/me` | any token | The identity of the token |
| POST | `/api/patient/requests` | patient | Submit a request |
| GET | `/api/patient/requests` | patient | My requests |
| GET | `/api/patient/requests/{case_id}` | patient | One of my requests |
| POST | `/api/patient/requests/{case_id}/documents` | patient | Upload a document |
| POST | `/api/patient/requests/{case_id}/documents/file` | patient | Upload a PDF, forwarded to the document-service (sub-project 13) |
| POST | `/api/patient/requests/{case_id}/reply` | patient | Answer a staff question (sub-project 15) |
| POST | `/api/patient/requests/{case_id}/reply/file` | patient | Upload the PDF a staff member asked for (sub-project 15) |
| GET | `/api/staff/cases` | staff | All cases (`?state=`) |
| GET | `/api/staff/cases/{case_id}` | staff | One case, in full |
| GET | `/api/staff/cases/{case_id}/audit` | staff | The case's audit trace |
| GET | `/api/staff/reviews` | staff | The human-review queue |
| GET | `/api/staff/cases/{case_id}/context` | staff | What the reviewer is shown |
| POST | `/api/staff/cases/{case_id}/decision` | staff | Approve / resolve / reject |
| POST | `/api/staff/cases/{case_id}/answer` | staff | Answer a `MedicalQuestion` with an approved clinical message |
| GET | `/api/staff/message-templates` | staff | The fixed staff messages (sub-project 15) |
| POST | `/api/staff/cases/{case_id}/request` | staff | Ask the patient a question or for a document (sub-project 15) |
| DELETE | `/api/staff/cases/{case_id}/data/{entry_id}` | staff | Delete one Data Log entry |
| GET | `/api/admin/metrics` | admin_staff | System metrics over a window (sub-project 14) |

Codes used everywhere: `401 not_authenticated` (no token, a malformed token, an expired or
forged one), `403 patients_only` / `403 staff_only` / `403 admin_only` (the wrong role), `404 case_not_found`
(unknown, or not this patient's case), `422 invalid_body` (the body, a query parameter or a
path parameter failed validation).

**Not shown in the table above:** FastAPI's own `/openapi.json`, `/docs` (Swagger UI) and
`/redoc` are also public - they carry no patient data, only the route/schema shapes already
in this document, so leaving them enabled is fine for the demo. A non-demo build would pass
`docs_url=None, redoc_url=None, openapi_url=None` to `FastAPI(...)` to turn them off.

## 3. Public

### GET /health

```json
{"status": "ok", "database": "ok", "orchestrator": "running", "appointments": "mock", "documents": "mock"}
```

`orchestrator` appears only on a real server: `"running"`, `"disabled: OPENAI_API_KEY is not set"`,
`"disabled: APPOINTMENT_API_KEY is not set"`, `"disabled: APPOINTMENT_SERVICE_URL is not an http(s) URL"`,
`"disabled: DOCUMENT_API_KEY is not set"` or `"disabled: DOCUMENT_SERVICE_URL is not an http(s) URL"`.
While it runs, `appointments` says where `CheckAppointment` goes: `"mock"`, or `"appointment-service"`
when `APPOINTMENT_SERVICE_URL` is set (sub-project 10), and `documents` says where `CheckDocuments`
goes: `"mock"`, or `"document-service"` when `DOCUMENT_SERVICE_URL` is set (sub-project 13) - the
word only, never the URL. `503` with
`{"status": "degraded", "database": "unavailable"}` when Postgres cannot be reached.

### POST /api/auth/login

Request:

```json
{"user_id": "P-10041", "password": "demo"}
```

`200`:

```json
{
  "user_id": "P-10041",
  "role": "patient",
  "display_name": "דנה כהן",
  "token": "eyJleHAiOjE3ODk4ODQ3NjcsInJvbGUiOiJwYXRpZW50Iiwic3ViIjoiUC0xMDA0MSJ9.C_x2NxY1fVJ6GMzgKRWmPFjp0tQZoLzcPVdQPSiafso"
}
```

- `401 invalid_credentials` - unknown user **or** wrong password (the two are not told
  apart, so the user list cannot be probed).
- `422 invalid_body` - `user_id` over 64 characters, `password` over 256, or a missing
  field. The submitted password never appears in the answer.

### GET /api/auth/me

`200`:

```json
{"user_id": "P-10041", "role": "patient", "display_name": "דנה כהן"}
```

- `401 not_authenticated`.

## 4. Patient routes

Every route below works on the patient the token names. Another patient's case is `404
case_not_found`, exactly like a case that does not exist.

### The patient view

Every patient route answers with this object, and nothing else (the PDF upload wraps it, as
`request`, beside its outcome code):

```json
{
  "case_id": "CASE-23FE645294B7",
  "status": "needs_document",
  "created_at": "2026-09-19T22:12:47.693947Z",
  "updated_at": "2026-09-19T22:12:47.898132Z",
  "request_text": "When is my appointment and which documents do I need?",
  "missing_document_ids": ["blood_test"],
  "missing_document_request_template_id": "missing-document-v1",
  "message": null,
  "history": [
    {"status": "received", "at": "2026-09-19T22:12:47.693947Z"},
    {"status": "in_progress", "at": "2026-09-19T22:12:47.812004Z"},
    {"status": "needs_document", "at": "2026-09-19T22:12:47.898132Z"}
  ],
  "document_upload": "text",
  "reply_request": null,
  "conversation": []
}
```

`reply_request` and `conversation` are sub-project 15 (§8 below documents them); every other
patient route already returned everything else shown here.

| `status` | Meaning | What the UI shows |
|---|---|---|
| `received` | Submitted, not yet being worked on | "Received" |
| `in_progress` | The agent is working (classifying, planning, retrieving) | A spinner; keep polling |
| `needs_document` | A document is missing | The upload form for `missing_document_ids` |
| `in_review` | A human is handling it | "A staff member is reviewing your request" - **no** reason, no kind |
| `needs_reply` | Sub-project 15: a staff member asked a question or for a document | `reply_request` - the question or the requested document, and the deadline |
| `completed` | An answer was delivered | `message` - either the status update the agent sent, or a clinical answer a `clinical_staff` reviewer wrote and approved |
| `closed` | Finished without a delivered message (a reviewer resolved or rejected it) | `message` when the reviewer sent a closing message (sub-project 15), otherwise "Your request was closed. The clinic will contact you." |

- `request_text` is the text the patient submitted; `null` once a staff member has deleted
  it from the Data Log (§18.4).
- `missing_document_ids` is non-empty only in `needs_document`;
  `missing_document_request_template_id` is then `"missing-document-v1"` (D24) - the UI
  renders the request for the document itself, the system never sends one.
- `message` is non-null in `completed` (the exact text that was delivered) and, since
  sub-project 15, in `closed` when the reviewer sent a closing message with the decision
  (`POST .../decision`'s optional `message`, §8 below); otherwise `null`.
- `history` is the case's abstract status over time, oldest first: one entry each time the
  status actually changed, with the time the case entered it. It holds the same seven values
  as `status` (since sub-project 15 added `needs_reply`) and nothing else - never a State, an
  event, an escalation kind or a reason
  (§12.3) - and a status the case entered twice appears twice. The last entry's `status`
  always equals `status`, and the first is the submission. It is `[]` only for a case with
  no committed transition, which the patient routes never return.
- `document_upload` says which upload the screen offers for `needs_document`: `"file"` when the
  server is configured with the document-service (`DOCUMENT_SERVICE_URL` and
  `DOCUMENT_API_KEY`) - a PDF picker, sent to `POST .../documents/file` - or `"text"` without
  it - the text box, sent to `POST .../documents`. It is the same for every case of a running
  server, and present in every state.

**Polling.** The case advances in the background, so after a submit or an upload the UI
polls `GET /api/patient/requests/{case_id}` (every ~2 s is plenty) until `status` stops
being `in_progress`.

### POST /api/patient/requests

Request:

```json
{"text": "When is my appointment and which documents do I need?"}
```

`text` is trimmed, and must then be 1-2000 characters.

`201`: the patient view. A verified patient's case starts in `in_progress`; `P-30000`'s
case comes back `in_review` at once.

- `403 patients_only` - a staff token.
- `422 invalid_body` - empty, whitespace-only or over 2000 characters.
- `409 request_rejected` - the state machine refused the request. It does not happen for a
  valid body; the exact reason stays on the server (the Blocked audit row and the
  application log), because a guard's reason code is internal (§12.3). Show a general
  "your request could not be submitted" and let the patient try again.

### GET /api/patient/requests

`200`: an array of patient views, **newest first**. `[]` when there are none.

### GET /api/patient/requests/{case_id}

`200`: the patient view. `404 case_not_found` for an unknown case or someone else's.

### POST /api/patient/requests/{case_id}/documents

Request:

```json
{"document_id": "blood_test", "format": "pdf", "content": "Blood test results: normal."}
```

| Field | Rule |
|---|---|
| `document_id` | 1-64 characters of `A-Z a-z 0-9 _ -`. Use an id from `missing_document_ids`. |
| `format` | `pdf`, `jpg` or `png` |
| `content` | 1-20000 characters. The demo has no binary upload: send the document's text (or a base64 string within that limit). |

`200`: the patient view **as it now stands**. The only signal is `status`: an accepted
document moves the case out of `needs_document`. Two other things can happen behind the same
`200`, and neither is success: a document the case was still waiting for but that fails
validation is a committed self-loop - it bumps `state_version` / `updated_at` but leaves
`status` at `needs_document` - and a document the case was not waiting for at all (D25)
changes nothing, not even `updated_at`. Either way the UI must keep polling and must not
show a confirmation from this response alone.

Refused with `409 use_file_upload` once a document-service is configured (`DOCUMENT_SERVICE_URL`
+ `DOCUMENT_API_KEY`, sub-project 13): the patient view's `document_upload` is then `"file"`,
the real upload is `POST .../documents/file`, and this route would otherwise let arbitrary text
be recorded as a held document without the document-service's intake ever running. Without a
document-service this route behaves exactly as before.

- `404 case_not_found`, `403 patients_only`, `409 use_file_upload`, `422 invalid_body` for a
  bad body (the document's content never comes back in the error).

### POST /api/patient/requests/{case_id}/documents/file

Sub-project 13 (design §5.3). Offered only when the patient view says `"document_upload":
"file"`. Request: `multipart/form-data` with one part named `file` carrying a filename - the
PDF, at most 10 MB (10 485 760 bytes). The request must carry a `Content-Length`; a body over
10 MB + 64 KiB (the file plus its multipart framing) is refused by that header alone, before
it is read. Other parts are ignored.

The server checks that the case is this patient's and is waiting for a document **before**
anything is sent on, then forwards the file to the document-service, which reads it,
classifies it and stores it only if it is accepted. The PDF is never stored here, and its
content never enters the Data Log, the Audit or a log line: an accepted, required document
records one reference line in the Data Log (`DOC-3F2A1B9C0D4E CBC ACCEPTED` - the
document-service's id, the type, the result) and moves the case down the same
`DOCUMENT_UPLOADED` path as the text upload. The document-service can take up to 70 s to
answer; the server waits up to 75 s.

`200`:

```json
{
  "upload": {"code": "accepted", "document_type": "CBC"},
  "request": { "...": "the patient view, as it now stands" }
}
```

| `upload.code` | Meaning | The case |
|---|---|---|
| `accepted` | A readable, valid document of a type this case needs, and not yet held. A re-sent copy of a document the document-service already accepted (a retry after a timeout) is `accepted` too. | Leaves `needs_document` (`in_progress`, then re-checked) |
| `not_required` | Accepted by the document-service, but this appointment does not need that type | Unchanged |
| `already_received` | Of a type the case already holds | Unchanged |
| `not_medical` | Not a medical document | Unchanged |
| `unreadable` | Could not be read or classified (also any answer this version does not know) | Unchanged |
| `expired` | Past its validity | Unchanged |
| `not_yours` | Names another patient | Unchanged |

`upload.document_type` is the catalog type (`CBC`, `COAGULATION_TESTS`, `ECG`, `URINALYSIS`,
`PREOP_SUMMARY`) for `accepted`, `not_required` and `already_received`, and `null` for every
other code. The document-service's own document id never comes back. `request` is the patient
view after the upload - for every code but `accepted` it is exactly what it was before.

- `404 file_upload_not_enabled` - the server has no document-service configured
  (`document_upload` is `"text"`); use `POST .../documents`.
- `404 case_not_found` - an unknown case, or someone else's. Nothing is sent on.
- `409 not_waiting_for_document` - the case is not in `needs_document` (also when it moved on
  while the document-service was answering). Nothing is sent on, or nothing is recorded.
- `411 length_required` - no `Content-Length`, or one that is not a plain non-negative
  number (empty, signed, `abc`, `1e3`).
- `413 too_large` - a `Content-Length` over 10 MB + 64 KiB (however many digits it has), or
  a file part over 10 MB.
- `422 invalid_body` - not a `multipart/form-data` body with a part named `file` (and a
  filename) among its first 64 parts, or a body that cannot be parsed at all.
- `503 document_service_unavailable` - the document-service did not answer, answered an
  error, or answered something that is not its contract. Nothing is recorded. Tell the
  patient the document service could not take the file, to try again later or contact the
  call centre - a retry is not promised to help (some of these answers are about the file
  itself), though a re-sent copy of a document that was in fact accepted comes back
  `accepted`. The application log records only the kind (`no_answer`, `status_<n>`,
  `invalid_response`).
- `401 not_authenticated`, `403 patients_only` - as everywhere.

## 5. Staff routes

Every route needs a `clinical_staff` or `admin_staff` token; a patient token gets
`403 staff_only`.

### GET /api/staff/cases

Optional `?state=<State>`; an unknown state is `422 invalid_body`. `200`:

```json
[
  {
    "case_id": "CASE-23FE645294B7",
    "state": "Completed",
    "escalation_kind": null,
    "updated_at": "2026-09-19T22:12:48.986200Z"
  }
]
```

States: `Received`, `Classifying`, `Classified`, `Planning`, `RetrievingData`,
`Delivering`, `AssessingReadiness`, `AwaitingPatientInput`, `AwaitingHumanReview`, `Ready`,
`Completed`, `Failed`, `AwaitingPatientReply` (sub-project 15, §8).

### GET /api/staff/cases/{case_id}

`200`:

```json
{
  "case_id": "CASE-23FE645294B7",
  "state": "Completed",
  "escalation_kind": null,
  "updated_at": "2026-09-19T22:12:48.986200Z",
  "patient_id": "P-10041",
  "state_version": 27,
  "intent": "AppointmentPreparation",
  "safety_level": "MediumRisk",
  "identity_verified": true,
  "plan_hash": "70471a828372f98f7949666f569537f3bc213e64aa0d3ec84f55ec9e2ba8cb99",
  "ordered_steps": [
    {"step": 1, "action": "CheckAppointment"},
    {"step": 2, "action": "CheckDocuments"},
    {"step": 3, "action": "LoadInstructions"},
    {"step": 4, "action": "SendStatusUpdate"}
  ],
  "current_step": 4,
  "retry_cycle": 0,
  "attempt_count": 1,
  "required_documents": ["referral", "blood_test"],
  "held_documents": ["referral", "blood_test"],
  "escalated_from_state": null,
  "patient_deadline": "2026-09-20T22:12:38.786253Z",
  "created_at": "2026-09-19T22:12:38.560531Z"
}
```

`intent` is `AppointmentPreparation`, `MedicalQuestion` or `Unsupported`; `safety_level` is
`LowRisk`, `MediumRisk`, `HighRisk` or `CriticalRisk`. `404 case_not_found`.

### GET /api/staff/cases/{case_id}/audit

`200`: the case's audit rows, oldest first (`audit_id` ascending):

```json
[
  {
    "audit_id": 1,
    "record_type": "Transition",
    "event": "REQUEST_SUBMITTED",
    "state_before": null,
    "state_after": "Received",
    "action": null,
    "guards": {"PatientIdentified": true},
    "policy_result": null,
    "policy_reasons": [],
    "execution_id": null,
    "attempt_number": 0,
    "retry_cycle": 0,
    "approval_id": null,
    "rule_version": "transitions-v1+policy-979c3594ccc2",
    "recorded_at": "2026-09-19T22:12:38.560531Z"
  }
]
```

`record_type` is `Transition` (an event that moved the case; a policy decision is one of
these, with `policy_result` set), `Blocked` (an event that was refused - the state did not
change), `ExecutionStarted`, or an execution's outcome: `ExecutionSucceeded`,
`ExecutionFailed`, `ExecutionUnknown`. The audit never contains
message or document text - only ids, decisions and hashes (§12.3). `404 case_not_found`.

### GET /api/staff/reviews

The queue of cases in `AwaitingHumanReview`, oldest update first. `200`:

```json
[
  {
    "case_id": "CASE-6FFF40DFB8DA",
    "patient_id": "P-10041",
    "escalation_kind": "MedicalQuestion",
    "escalated_from_state": "Classifying",
    "reasons": [],
    "allowed_decisions": ["resolve", "reject"],
    "required_fields": [],
    "updated_at": "2026-09-19T22:12:39.693277Z",
    "human_engaged": false,
    "returned_by": null
  }
]
```

- `escalation_kind`: `PatientVerificationFailed`, `MedicalQuestion`, `SafetyEscalation`,
  `ClassificationFailed`, `TemporalViolation`, `PlanningFailed`, `PolicyDenied`,
  `PolicyReview`, `RetryExhausted`, `NonIdempotentFailure`, `ExecutionUnknown`,
  `Z3Counterexample`, `PatientSlaExpired`, `DeliveryStepMissing`.
- `human_engaged` / `returned_by` (sub-project 15) - see §8 below.
- `reasons`: the `policy_reasons` of the row that escalated the case (e.g.
  `["medical_answer_attempt"]`, `["hours_until:20"]`). May be empty.
- `allowed_decisions`: **render exactly these buttons.** `approve` appears only for the
  five kinds a case can resume from (`PatientVerificationFailed`, `RetryExhausted`,
  `PolicyReview`, `Z3Counterexample`, `PatientSlaExpired`); `resolve` and `reject` always.
- `required_fields`: the extra fields `approve` needs - `["verified_identity_ref"]`
  (`PatientVerificationFailed`) or `["patient_deadline"]` (`Z3Counterexample`,
  `PatientSlaExpired`); otherwise `[]`. Render them in the approve form; the server
  refuses an approve without them (`409 verified_identity_ref_required` /
  `409 patient_deadline_required`).

### GET /api/staff/cases/{case_id}/context

Everything the reviewer is shown, plus the reference that binds the decision to it. `200`:

```json
{
  "case_id": "CASE-6FFF40DFB8DA",
  "patient_id": "P-10041",
  "state": "AwaitingHumanReview",
  "escalation_kind": "MedicalQuestion",
  "escalated_from_state": "Classifying",
  "reasons": [],
  "data": [
    {
      "entry_id": "DATA-0080b88ca384",
      "kind": "request_text",
      "content": "Should I stop taking my blood thinner?",
      "content_hash": "39d813fac47b85aba91577cb8ff34c584f018d92cf083d724a3a6c366a193bf3",
      "created_at": "2026-09-19T22:12:39.681696Z"
    }
  ],
  "trace": [
    {
      "audit_id": 36,
      "record_type": "Transition",
      "event": "REQUEST_SUBMITTED",
      "state_before": null,
      "state_after": "Received",
      "action": null,
      "policy_result": null,
      "policy_reasons": [],
      "recorded_at": "2026-09-19T22:12:39.678380Z"
    }
  ],
  "shown_context_ref": "ctx-ba3e0652b0ea355663db57e7d19550c61ee1d156fea64c91c2cacd539bbc7d83"
}
```

- `data` is the Data Log (§12.3) - the only place content lives. `kind` is `request_text`,
  `uploaded_document`, `instructions`, `outgoing_message`, `staff_message` or `patient_reply`
  (the last two, sub-project 15, §8). Deleted entries and uploads the case never accepted
  are not listed at all.
- `trace` is the same audit rows as `/audit`, with the content-free subset above.
- `shown_context_ref` **must be sent back with the decision**. Fetch the context, show it,
  and post the decision with the `shown_context_ref` that came with what the reviewer read.
  Any change in between (a new audit row, a deleted entry) makes the decision
  `409 context_changed`: re-fetch the context, show it again, and let the reviewer decide
  on what is now true.
- `404 case_not_found`. A case that is not in review still returns its context (`state` is
  then whatever it is, and `reasons` is `[]`), so the staff screen can show any case.

### POST /api/staff/cases/{case_id}/decision

Request:

```json
{
  "decision": "resolve",
  "reason": "Referred to the clinic; the patient was called.",
  "shown_context_ref": "ctx-ba3e0652b0ea355663db57e7d19550c61ee1d156fea64c91c2cacd539bbc7d83"
}
```

| Field | Rule |
|---|---|
| `decision` | `approve`, `resolve` or `reject`, and it must be in the queue item's `allowed_decisions` |
| `reason` | Required, non-blank, up to 2000 characters. It is stored on the approval record (§12.5). |
| `shown_context_ref` | The value from the context just shown |
| `verified_identity_ref` | Only for `approve` on `PatientVerificationFailed`: how the identity was established, e.g. `"ID-DESK-17"` |
| `patient_deadline` | Only for `approve` on `Z3Counterexample` / `PatientSlaExpired`: the new deadline, an ISO datetime **with a timezone**, e.g. `"2026-09-24T09:00:00+00:00"` |
| `message` | Only for `resolve` / `reject` (sub-project 15): an optional closing message for the patient - see §8 below |

`reviewer_id` and `reviewer_role` are taken from the token; sending them changes nothing.

`200`:

```json
{"case_id": "CASE-6FFF40DFB8DA", "state": "Completed"}
```

`state` is the case's state **after** the decision - and after anything that followed it
automatically. What to expect:

| Decision | Kind | Resulting state |
|---|---|---|
| `resolve` | any | `Completed` (the patient sees `closed`, with an optional closing message - see §8; for a `MedicalQuestion`, answering it through `POST .../answer` instead leaves the patient seeing `completed`) |
| `reject` | any | `Failed` (the patient sees `closed`) |
| `approve` | `PatientVerificationFailed` | `Classifying` - the request is re-validated automatically and goes on; or `Received` if the request text had been deleted: the decision stands and the case waits for staff |
| `approve` | `RetryExhausted` | `Planning` (a new retry cycle) |
| `approve` | `PolicyReview` | `Planning` (a one-shot override for this plan and step) |
| `approve` | `Z3Counterexample` / `PatientSlaExpired` | `AwaitingPatientInput` with the new deadline |

Errors - all of them leave the case exactly as it was:

| Status | `detail` | When |
|---|---|---|
| 404 | `case_not_found` | Unknown case |
| 409 | `not_in_review` | The case is not in `AwaitingHumanReview` (someone else decided first - refresh the queue) |
| 409 | `context_changed` | `shown_context_ref` is not the current one |
| 409 | `invalid_decision` | `decision` is not one of the three |
| 409 | `decision_not_allowed` | `approve` on a kind that cannot resume |
| 409 | `reason_required` | `reason` is empty or blank |
| 409 | `verified_identity_ref_required` | `approve` on `PatientVerificationFailed` without it |
| 409 | `patient_deadline_required` | `approve` on `Z3Counterexample` / `PatientSlaExpired` without it |
| 409 | `workflow_decision_invalid` | The state machine refused the approval record (e.g. a non-staff reviewer role) |
| 409 | other codes | Any other guard that refused the human event; show `detail` and re-fetch the case |
| 422 | `invalid_body` | A field is over its length limit, or `patient_deadline` has no timezone |

Sub-project 15's optional `message` field adds three more codes to this route - `403
clinical_staff_only`, `409 message_not_allowed` and `409 human_engaged` - documented in §8
below, along with `message`'s other validation codes (`invalid_request`, `message_required`,
`invalid_template`, `unexpected_param`, `invalid_param`).

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

Errors - all of them leave the case exactly as it was:

| Status | `detail` | When |
|---|---|---|
| 404 | `case_not_found` | Unknown case |
| 409 | `not_in_review` | The case is not in `AwaitingHumanReview` (someone else decided first - refresh the queue) |
| 409 | `context_changed` | `shown_context_ref` is not the current one |
| 403 | `clinical_staff_only` | Only `clinical_staff` may grant a ContentApproval (§12.4). This is a role rule, not an authentication one: an `admin_staff` token reaches the route and is refused |
| 409 | `decision_not_allowed` | The escalation is not a `MedicalQuestion` |
| 409 | `answer_required` | `answer` is empty or blank - reachable even past the schema's `min_length=1`, because a whitespace-only string passes it and then fails the trim check |
| 409 | `reason_required` | `reason` is empty or blank, same as above |
| 409 | other codes | Any other guard that refused the `HUMAN_RESOLVED_CASE` transition; show `detail` and re-fetch the case |
| 422 | `invalid_body` | A field is over its length limit |

### DELETE /api/staff/cases/{case_id}/data/{entry_id}

§18.4: delete one Data Log entry's content. The entry stays as a tombstone (its
`content_hash` is kept) and the audit trace is never touched.

- `204` with an empty body.
- `404 entry_not_found` - no such entry on this case, or it was already deleted.

Take `entry_id` from the context's `data`. After a delete, re-fetch the context: the
previous `shown_context_ref` is no longer valid.

## 6. Notes for the UI

- **Two screens** (§1): the patient screen (login, submit, status, upload) and the staff
  screen (queue, context, decision, the case monitor). `role` from `/api/auth/me` (or the
  login response) decides which one to show.
- **Nothing is optimistic.** Every state in the UI comes from a response; the backend is
  the only source of truth, and a refresh must never change what the user sees.
- **A staff decision is a two-step flow by design**: fetch the context, then post the
  decision with its `shown_context_ref`. Never cache a context across a decision.
- **A medical message is never sent automatically.** It is denied and the case reaches the
  queue; from there a `clinical_staff` reviewer either answers it through
  `POST .../answer` (a `ContentApproval`, bound to the exact text, and the patient sees
  `completed`) or closes it without a message through `POST .../decision` with
  `resolve`/`reject` (the patient sees `closed`) - `admin_staff` may do the latter but not
  the former (design decision 6).
- **Error handling:** `401` → back to login; `403` → the wrong screen for this role;
  `404` → the case is gone or not the user's; `409` → show `detail`, re-fetch, try again;
  `422` → the form is invalid - validate locally against the rules listed above, since the
  body carries no field list; `503` on `/health` → the database is down.

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
    "decisions": {"HUMAN_APPROVED": 1, "HUMAN_RESOLVED_CASE": 1, "HUMAN_REJECTED": 0,
                  "PATIENT_REPLY_REQUESTED": 0},
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

## 8. Staff requests to the patient (sub-project 15)

While a case is in `AwaitingHumanReview`, a reviewer may ask the patient a question or for one
catalog document instead of (or before) approving, resolving or rejecting it. The case then
waits in `AwaitingPatientReply` until the patient answers or the deadline passes (the SLA
Worker returns it to the queue), and comes back to review with the same escalation it left
with - it never resumes automatically (design decision: never back to the agent after a
human has engaged).

### The patient view's new fields

- `status` gains `needs_reply`: a staff member is waiting for the patient's answer.
- `reply_request` is non-null only while `status` is `needs_reply` - what was asked, never a
  reason or an escalation kind:

  ```json
  {"kind": "question", "message": "לא הצלחנו להבין את פנייתך...", "document_type": null,
   "deadline": "2026-09-26T09:00:00Z"}
  ```

  `kind` is `"question"` or `"document"`; `message` is the exact text sent (a template's
  rendering, or a `clinical_staff` member's free text) - `null` only if it was later deleted
  (§18.4); `document_type` is the requested catalog type for `"document"`, else `null`;
  `deadline` is when the request expires.
- `conversation` is the staff messages and the patient's replies, oldest first, shown only
  by design §7.5's fail-closed rule (a template's own text, or clinical free text with a
  consumed `ContentApproval`; a document reply is shown as its document's label, never its
  raw reference line):

  ```json
  [
    {"sender": "staff", "text": "לא הצלחנו להבין את פנייתך...", "at": "2026-09-25T09:00:00Z"},
    {"sender": "patient", "text": "תור לאורתופדיה", "at": "2026-09-25T09:05:00Z"}
  ]
  ```
- `message` is now also non-null in `closed` when the reviewer sent a closing message with
  the decision (below); otherwise it stays `null` there as before.

### POST /api/patient/requests/{case_id}/reply

The patient's text answer to a staff question. Request:

```json
{"text": "תור לאורתופדיה"}
```

`text` is trimmed, and must then be 1-2000 characters. `200`: the patient view, now `in_review`
(back with the reviewer, `returned_by: "patient_reply"`).

- `404 case_not_found`
- `409 not_waiting_for_reply` - the case is not in `needs_reply` (already answered, timed
  out, or never asked)
- `409 reply_kind_mismatch` - the staff member asked for a document, not text
- `409 reply_not_accepted` - the state machine refused the reply for an internal reason; the
  exact reason stays on the server (application log, §12.3), the same pattern as
  `POST /api/patient/requests`' `request_rejected` - not reachable through a conforming
  client today (a trimmed, non-blank, ≤2000-character `text` is always accepted or refused
  with a more specific code above), kept as the safe default for a guard failure the service
  does not otherwise name
- `422 invalid_body` - empty, whitespace-only or over 2000 characters
- `422 reply_too_long` - defence only: the schema already caps `text` at 2000, so this is not
  reachable through a conforming client; kept in case the service ever counts length
  differently, so it can never surface as a 500

### POST /api/patient/requests/{case_id}/reply/file

Exactly like `POST /api/patient/requests/{case_id}/documents/file` (sub-project 13: same
multipart contract, same size limits, same 411/413 body-size checks in front of the route) -
offered only while `reply_request.kind` is `"document"`. `200`:

```json
{"upload": {"code": "accepted", "document_type": "URINALYSIS"}, "request": {"...": "the patient view"}}
```

`upload.code` is one of §4's codes reachable here - `accepted`, `not_medical`, `unreadable`,
`expired`, `not_yours` - plus one more:

| `upload.code` | Meaning | The case |
|---|---|---|
| `accepted` | A readable, valid document of the type requested (or a re-sent copy already accepted) | Leaves `needs_reply`, back to `in_review` |
| `wrong_document_type` | Readable and valid, but not the catalog type the staff member asked for | Unchanged |
| `not_medical`, `unreadable`, `expired`, `not_yours` | As in §4 | Unchanged |

`upload.document_type` is the catalog type the document-service classified the file as, for
`accepted` and also for `wrong_document_type` (the type it actually was, not the one that was
requested); `null` for every other code.

- `404 file_upload_not_enabled`, `404 case_not_found`
- `409 not_waiting_for_reply`, `409 reply_kind_mismatch`
- `411 length_required`, `413 too_large`, `422 invalid_body` - as in §4
- `503 document_service_unavailable` - as in §4

### GET /api/staff/message-templates

The fixed messages a staff member may send without a `ContentApproval` (design §7.1) - the UI
keeps no copy of them. `200`:

```json
[
  {"template_id": "clarify_general", "purpose": "question",
   "text": "לא הצלחנו להבין את פנייתך. נשמח אם תפרט/י במה נוכל לעזור.", "param": null, "options": {}},
  {"template_id": "clarify_did_you_mean", "purpose": "question",
   "text": "האם התכוונת ל{topic}? נשמח לאישור או לפירוט.", "param": "topic",
   "options": {"appointment_time": "מועד התור", "required_documents": "המסמכים הנדרשים לתור",
               "preparation": "הוראות ההכנה לתור"}},
  {"template_id": "document_request", "purpose": "document", "text": "נא להעלות את המסמך: {document}.",
   "param": "document", "options": {"CBC": "ספירת דם מלאה", "COAGULATION_TESTS": "בדיקות קרישה",
   "ECG": "תרשים פעילות חשמלית של הלב", "URINALYSIS": "בדיקת שתן", "PREOP_SUMMARY": "סיכום טרום ניתוח"}},
  {"template_id": "close_out_of_scope", "purpose": "closing",
   "text": "פנייתך אינה בתחום שהמערכת מטפלת בו. לשאלות אחרות ניתן לפנות למוקד.", "param": null, "options": {}}
]
```

This example shows 4 of the server's 7 templates (`clarify_general`, `clarify_did_you_mean`,
`document_request`, `close_out_of_scope`); `clarify_appointment`, `close_handled` and
`close_no_reply` follow the same shape, each with `param: null` and `options: {}`.

`purpose` is `"question"`, `"document"` or `"closing"` - which route(s) may use that template.
`options` is `{}` exactly when `param` is `null`: a template takes a parameter if and only if
it lists one or more options for it.

- `403 staff_only` - a patient token.

### POST /api/staff/cases/{case_id}/request

Ask the patient a question, or for one catalog document. Request:

```json
{"kind": "question", "template_id": "clarify_general", "reason": "unclear", "shown_context_ref": "ctx-..."}
```

or, for clinical free text:

```json
{"kind": "question", "text": "מהו התאריך המדויק?", "reason": "unclear", "shown_context_ref": "ctx-..."}
```

or, for a document:

```json
{"kind": "document", "document_type": "URINALYSIS", "reason": "need a urine test", "shown_context_ref": "ctx-..."}
```

| Field | Rule |
|---|---|
| `kind` | `"question"` or `"document"` |
| `template_id` | A template of the right purpose (`question` or, for a document, `document_request` only - or omit it) |
| `param` | The template's parameter, from its closed `options` list, when it takes one - never for `kind: "document"`, whose message is rendered from `document_type` (`409 unexpected_param`) |
| `text` | Clinical free text instead of a template - `question` only, and only from a `clinical_staff` token; exactly one of `template_id` / `text` |
| `document_type` | A catalog type (`CBC`, `COAGULATION_TESTS`, `ECG`, `URINALYSIS`, `PREOP_SUMMARY`) - `kind: "document"` only |
| `deadline` | Optional ISO datetime **with a timezone**; defaults to 24 hours ahead (never past the appointment); at most 7 days ahead and never past the appointment |
| `reason` | Required, non-blank, up to 2000 characters - internal, like a decision's reason |
| `shown_context_ref` | The value from the context just shown |

`200`: `{"case_id": "...", "state": "AwaitingPatientReply"}`.

Errors - all of them leave the case exactly as it was:

| Status | `detail` | When |
|---|---|---|
| 404 | `case_not_found` | Unknown case |
| 409 | `not_in_review` | The case is not in `AwaitingHumanReview` |
| 409 | `context_changed` | `shown_context_ref` is not the current one |
| 409 | `reason_required` | `reason` is empty or blank |
| 409 | `invalid_request` | `kind` is neither `question` nor `document`; a `question` request also sets `document_type`; a `document` request carries `text`, an unrelated `template_id` or an unknown `document_type`; or neither/both of `template_id` and `text` are given |
| 403 | `clinical_staff_only` | `text` from a non-`clinical_staff` token |
| 409 | `message_required` | `text` is empty or blank once trimmed (over 2000 characters is the same defence as `reply_too_long` above - the schema already caps `text` at 2000) |
| 409 | `invalid_template` | `kind: "question"` with a `template_id` that is unknown, or is not a `question` template (a `document` request's `template_id` is checked as part of `invalid_request` above, never this code) |
| 409 | `unexpected_param` | `param` given for a template that takes none, or `param` given at all for `kind: "document"` (a document request never takes one) |
| 409 | `invalid_param` | `param` is not one of the template's `options` |
| 409 | `document_service_not_configured` | `kind: "document"` with no document-service configured |
| 409 | `invalid_deadline` | `deadline` is in the past, more than 7 days ahead, or after the appointment |
| 409 | `appointment_passed` | `appointment_at` is known and is already in the past - refused before the deadline is even computed, so a default deadline never masquerades as `invalid_deadline` |
| 409 | other codes | Any other guard that refused the `PATIENT_REPLY_REQUESTED` transition; show `detail` and re-fetch the case |
| 422 | `invalid_body` | A field is over its length limit, `kind`/`reason`/`shown_context_ref` missing, or `deadline` has no timezone |

### POST /api/staff/cases/{case_id}/decision: the optional closing message

`resolve` and `reject` (never `approve`) may now carry a closing message for the patient, in
the same shape as the request route's `template_id`/`param` or `text`:

```json
{"decision": "reject", "reason": "out of scope", "shown_context_ref": "ctx-...",
 "message": {"template_id": "close_out_of_scope"}}
```

`message` is optional; when given, it is validated exactly like the request route's message
(`invalid_request`, `clinical_staff_only`, `message_required`, `invalid_template`,
`unexpected_param`, `invalid_param` - all `409`, except `clinical_staff_only` which is `403`).
Two more codes are sub-project 15's own:

| Status | `detail` | When |
|---|---|---|
| 409 | `message_not_allowed` | `message` given with `decision: "approve"` |
| 409 | `human_engaged` | `decision: "approve"` on a case a staff request has already been sent in - `approve` is gone for good once a person has written to the patient (`allowed_decisions` already omits it) |

### ReviewItem: `human_engaged` and `returned_by`

`GET /api/staff/reviews` and the review queue now also carry:

```json
{"...": "...", "human_engaged": true, "returned_by": "patient_reply"}
```

- `human_engaged`: `true` once any staff request has been sent on this case - `approve` is
  then removed from `allowed_decisions` (never sent by the server).
- `returned_by`: how the case last came back to `AwaitingHumanReview` - `"patient_reply"`
  (the patient answered), `"reply_timeout"` (the deadline passed, the SLA Worker returned
  it), or `null` (it came from elsewhere, e.g. it just escalated).
