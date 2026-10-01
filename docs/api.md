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

The demo IdP (§18.3) is the `users` table (migration 0009, `docs/spec_corrections.md` row 97):
each user has a role and its own scrypt password hash. `POST /api/auth/login` returns a
stateless token; send it on every other `/api` request:

```
Authorization: Bearer <token>
```

The token is `base64url({"exp":…,"role":…,"sub":…}).<hmac-sha256>`, valid **8 hours**. It
is opaque to the UI: store it (e.g. in `sessionStorage`), send it, and on `401` send the
user back to the login screen. There is no refresh endpoint and no server session - log in
again. Every request re-reads the user, so a token stops working at once when its user is made
inactive or its role changes. An unknown user, an inactive one and a wrong password are all the
same `401 invalid_credentials`.

### Demo users

| `user_id` | `role` | `display_name` | Note |
|---|---|---|---|
| `P-10041` | `patient` | דנה כהן | The patient of all three §0 scenarios |
| `P-20000` | `patient` | יוסי לוי | A second patient (privacy checks) |
| `P-30000` | `patient` | מיכל אברהם | Identity does not verify: every request of hers goes to review as `PatientVerificationFailed` |
| `coordinator_nurse` | `clinical_staff` | אחות מתאמת | Staff screen |
| `admin_coordinator` | `admin_staff` | רכזת מנהלה | Staff screen |

**Password:** migration 0009 seeded each user with its own hash of the value `DEMO_PASSWORD` had
when the migration ran (default `demo`, set in `.env` at the repo root), so all five start with
the same password; changing `DEMO_PASSWORD` afterwards changes nothing - a password changes by
updating that user's `password_hash`. `AUTH_SECRET` (also `.env`) signs the tokens; changing it
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
| POST | `/api/patient/requests/{case_id}/documents/file` | patient | Upload a document (PDF, JPEG or PNG), forwarded to the document-service (sub-project 13) |
| POST | `/api/patient/requests/{case_id}/reply` | patient | Answer a staff question (sub-project 15) |
| POST | `/api/patient/requests/{case_id}/reply/file` | patient | Upload the document (PDF, JPEG or PNG) a staff member asked for (sub-project 15) |
| GET | `/api/patient/appointments` | patient | My appointments (`?from=&to=`, sub-project 16) |
| GET | `/api/patient/instructions/{source_id}` | patient | The text behind one approved preparation instruction (`?version=`, sub-project 18) |
| GET | `/api/staff/cases` | staff | All cases, keyset-paginated (`?state=` or `?group=&escalation_kind=`, `&limit=&cursor=`; staff-fixes design Tasks 3-4) |
| GET | `/api/staff/cases/{case_id}` | staff | One case, in full (plan, documents, counters) |
| GET | `/api/staff/cases/{case_id}/audit` | staff | The case's audit trace |
| GET | `/api/staff/cases/{case_id}/appointments` | staff | The case's patient's appointments (`?from=&to=`, sub-project 16) |
| GET | `/api/staff/instructions/{source_id}` | staff | The same instruction text as the patient's route, for any approved source (`?version=`, sub-project 18) |
| GET | `/api/staff/reviews` | staff | The human-review queue, keyset-paginated, newest entry first (staff-fixes design Task 5) |
| GET | `/api/staff/reviews/{case_id}` | staff | One queue item (staff-fixes design Task 3) |
| GET | `/api/staff/cases/{case_id}/context` | staff | What the reviewer is shown |
| POST | `/api/staff/cases/{case_id}/decision` | staff | Approve / resolve / reject |
| POST | `/api/staff/cases/{case_id}/answer` | staff | Answer a `MedicalQuestion` with an approved clinical message |
| GET | `/api/staff/message-templates` | staff | The fixed staff messages (sub-project 15) |
| POST | `/api/staff/cases/{case_id}/request` | staff | Ask the patient a question or for a document (sub-project 15) |
| DELETE | `/api/staff/cases/{case_id}/data/{entry_id}` | staff | Delete one Data Log entry |
| GET | `/api/staff/system-status` | staff | Whether the Agent Orchestrator runs and the LLM's last outcome (staff-fixes design Task 1) |
| GET | `/api/staff/llm-costs` | staff | The LLM cost of the cases opened in a window (`?from=&to=`, sub-project 19, §10) |
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
{"status": "ok", "database": "ok", "orchestrator": "running", "llm": "ok",
 "appointments": "mock", "documents": "mock"}
```

`orchestrator` appears only on a real server: `"running"`, `"disabled: OPENAI_API_KEY is not set"`,
`"disabled: APPOINTMENT_API_KEY is not set"`, `"disabled: APPOINTMENT_SERVICE_URL is not an http(s) URL"`,
`"disabled: DOCUMENT_API_KEY is not set"` or `"disabled: DOCUMENT_SERVICE_URL is not an http(s) URL"`.
While it runs, `appointments` says where `CheckAppointment` goes: `"mock"`, or `"appointment-service"`
when `APPOINTMENT_SERVICE_URL` is set (sub-project 10), and `documents` says where `CheckDocuments`
goes: `"mock"`, or `"document-service"` when `DOCUMENT_SERVICE_URL` is set (sub-project 13) - the
word only, never the URL. `llm` appears alongside `orchestrator` (staff-fixes design Task 1) as
`"ok"` | `"error"` | `"unknown"` only - `"unknown"` until the first of the four LLM calls (Intent,
Safety, Planner, Response Evaluator), `"error"` when the last one was unusable and no later one
succeeded, `"ok"` otherwise. Being public, `/health` never carries the error code or a timestamp
(fix round 1, M6) - that detail is staff-only, on `GET /api/staff/system-status` (§5). `503` with
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

Every patient route answers with this object, and nothing else (the document upload wraps it, as
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
  "conversation": [],
  "instructions": null
}
```

`reply_request` and `conversation` are sub-project 15 (§8 below documents them); `instructions`
is sub-project 18 (documented below); every other patient route already returned everything
else shown here.

| `status` | Meaning | What the UI shows |
|---|---|---|
| `received` | Submitted, not yet being worked on | "Received" |
| `in_progress` | The agent is working (classifying, planning, retrieving) | A spinner; keep polling |
| `needs_document` | A document is missing | The upload form for `missing_document_ids` |
| `in_review` | A human is handling it | "A staff member is reviewing your request" - **no** reason, no kind |
| `needs_reply` | Sub-project 15: a staff member asked a question or for a document | `reply_request` - the question or the requested document, and the deadline |
| `completed` | An answer was delivered | `message` - either the status update the agent sent, or a clinical answer a `clinical_staff` reviewer wrote and approved; `instructions` when the case loaded a preparation instruction |
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
- `instructions` (sub-project 18, design D11) is `{"title": ..., "text": ...}` only when
  **every one** of these holds (fix round 1, I1/I2):
  - the status is `completed` *because* the agent itself delivered a `CASE_RESOLVED` message -
    never a `clinical_staff` answer to a `MedicalQuestion`, and never `closed`;
  - the case has a sub-project 18 instruction source (`instruction_source_id` is set) - a
    pre-sub-project-18 case never shows the old fixed mock text; the mock path itself (design
    D7) does set one (the demo colonoscopy source) and so is not excluded by this gate alone;
  - `safety_level` is `LowRisk` or `MediumRisk` - text a later Safety re-check flagged
    `HighRisk`/`CriticalRisk` is never shown, even when a human overrode a `PolicyReview`
    escalation to let the case proceed regardless;
  - the case's **latest** Data Log `instructions` entry (§12.3) is present (not tombstoned) -
    the fixed plan loads instructions once (after the patient's upload, golden scenario 1 goes
    back through `Classifying` to `AssessingReadiness`, with no re-plan), but nothing in the
    Data Log limits a case to one such entry, so this is always the latest entry by recency;
    if that latest entry was deleted, the result is `null`, and this never falls back to an
    older, still-present entry.
  Exactly what was approved and shown, split into its title and body on the first newline.
  `null` whenever any of the above fails to hold, in particular for every status but
  `completed`.
- `document_upload` says which upload the screen offers for `needs_document`: `"file"` when the
  server is configured with the document-service (`DOCUMENT_SERVICE_URL` and
  `DOCUMENT_API_KEY`) - a document picker (PDF, JPEG or PNG), sent to `POST .../documents/file` - or `"text"` without
  it - the text box, sent to `POST .../documents`. It is the same for every case of a running
  server, and present in every state.

**Polling.** The case advances in the background, so the request screen polls
`GET /api/patient/requests/{case_id}` (sub-project 18, the owner's request of 2026-09-26):

- every 5 s while the case is moving (`received`, `in_progress`), with no time limit;
- in every other non-final status (`needs_document`, `in_review`, `needs_reply`), every 5 s for
  a 60 s window, which restarts on a status change, on a patient action (an upload or a reply)
  and when the tab becomes visible again; after the window the screen shows when it was last
  updated and a "רענון" button, which reads the case and restarts the window;
- never in `completed` or `closed`.

"הפניות שלי" (the request list) keeps its own 3 s refresh while a case is moving.

### POST /api/patient/requests

Request:

```json
{"text": "When is my appointment and which documents do I need?", "appointment_id": "APT-8391"}
```

`text` is trimmed, and must then be 1-2000 characters. `appointment_id` (sub-project 18,
design D5) is optional - the appointment the patient picked from their own upcoming list
(`GET /api/patient/appointments`, §9). Omit the field entirely for "the nearest appointment"
(there is no value that means that - it is the field's absence, not any particular string).
When present, it must be 1-64 characters of `A-Z a-z 0-9 . _ -`, starting with a letter or
digit. It is stored on the case as it is opened and is never read from anywhere else - the
LLM never supplies it - and `CheckAppointment` (§11) reads it back to answer about that
exact appointment. Its answer also carries `upcoming_count`: the patient's Scheduled
appointments within 90 days from now - the same window the "פנייה חדשה" picker offers - and
when that is above 1 the status message adds the "you have other appointments" sentence
(design D10).

`201`: the patient view. A verified patient's case starts in `in_progress`; `P-30000`'s
case comes back `in_review` at once.

- `403 patients_only` - a staff token.
- `422 invalid_body` - `text` empty, whitespace-only or over 2000 characters, or
  `appointment_id` present but not that shape.
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

Sub-project 13 (design §5.3); sub-project 17 task 2: PDF, JPEG or PNG, not PDF only - a scanned
PDF with no text layer and a photo of a document both go through the document-service's vision
path. Offered only when the patient view says `"document_upload": "file"`. Request:
`multipart/form-data` with one part named `file` carrying a filename - the document, at most
10 MB (10 485 760 bytes). The request must carry a `Content-Length`; a body over 10 MB + 64 KiB
(the file plus its multipart framing) is refused by that header alone, before it is read. Other
parts are ignored.

The server checks that the case is this patient's and is waiting for a document **before**
anything is sent on, then forwards the file to the document-service - with its real
`Content-Type` sniffed by magic bytes (`application/pdf`, `image/jpeg` or `image/png`), never
trusted from the browser or the file name - which reads it, classifies it and stores it only if
it is accepted. The file is never stored here, and its content never enters the Data Log, the
Audit or a log line: an accepted, required document records one reference line in the Data Log
(`DOC-3F2A1B9C0D4E CBC ACCEPTED` - the document-service's id, the type, the result) and moves
the case down the same `DOCUMENT_UPLOADED` path as the text upload. The document-service can
take up to 70 s to answer; the server waits up to 75 s. A `503 classifier_unavailable` from the
document-service (a provider failure, never a verdict on the file) is not a rejection code at
all - it surfaces as this route's own `503 document_service_unavailable` below, exactly like any
other unavailable answer.

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
| `unrecognised_type` | A medical document, but not of a type the catalog knows (document-service `reason: unknown_type`) | Unchanged |
| `unreadable_scan` | A scanned PDF with no text layer and no usable embedded image for vision either (document-service `reason: no_text_layer`) | Unchanged |
| `bad_date` | The document's date is in the future (document-service `reason: future_date`) | Unchanged |
| `no_date` | No date found on the document (document-service `reason: no_date`) | Unchanged |
| `unsupported_format` | Not a PDF, JPEG or PNG by magic bytes (document-service `reason: not_supported_format`) | Unchanged |
| `too_large` | An image over the document-service's pixel limit - both sides already cap the file at 10 MB (document-service `reason: too_large`) | Unchanged |
| `unreadable` | Could not be read or classified for any other reason (a parse error, too many pages, too much text, an unparsable classifier answer, or any answer or reason this version does not know) | Unchanged |
| `expired` | Past its validity (a document-service `DOCUMENT_EXPIRED` whose reason is `too_old`, or none at all) | Unchanged |
| `not_yours` | Names another patient | Unchanged |

The six rows `unrecognised_type` … `too_large` are sub-project 17 task 2: the document-service's optional `reason` (its own
API, `DOCUMENT_UNREADABLE`/`DOCUMENT_EXPIRED` only) becomes a finer patient code exactly where
listed above; every other reason, or no reason at all, falls back to the coarser `unreadable` or
`expired`. `upload.document_type` is the catalog type (`CBC`, `COAGULATION_TESTS`, `ECG`,
`URINALYSIS`, `PREOP_SUMMARY`) for `accepted`, `not_required` and `already_received`, and `null`
for every other code. The document-service's own document id never comes back. `request` is the
patient view after the upload - for every code but `accepted` it is exactly what it was before.

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
  error (including `classifier_unavailable`), or answered something that is not its contract.
  Nothing is recorded. Tell the patient the document service could not take the file, to try
  again later or contact the call centre - a retry is not promised to help (some of these
  answers are about the file itself), though a re-sent copy of a document that was in fact
  accepted comes back `accepted`. The application log records only the kind (`no_answer`,
  `status_<n>`, `classifier_unavailable`, `invalid_response`).
- `401 not_authenticated`, `403 patients_only` - as everywhere.

## 5. Staff routes

Every route needs a `clinical_staff` or `admin_staff` token; a patient token gets
`403 staff_only`.

### GET /api/staff/cases

Staff-fixes design Task 3: one call, with every column the Case Monitor table shows, so
the client makes no per-row follow-up call. Optional `?state=<State>`; an unknown state is
`422 invalid_body`, and so is a non-integer `?limit=` (FastAPI's own query-parsing 422,
turned into the same body every route uses for a malformed request). Keyset pagination:
`?limit=` (default 50, at most 200; an integer outside that range is `422 invalid_limit`)
and `?cursor=` (the previous response's `next_cursor`; malformed, of the other list, or
naive is `422 invalid_cursor` - fix round 1 (M5): every cursor this endpoint's
`next_cursor` carries starts `c|`, and a `GET /api/staff/reviews` cursor starts `r|`, so a
cursor built for that list is caught by the prefix alone, and one whose timestamp carries
no timezone offset is refused too. A well-formed, correctly-prefixed cursor built by hand is
accepted by design - there is no HMAC or other signature over it, only the shape check).

Staff-fixes design Task 4: `?group=<staff|patient|automatic|done|rejected>` filters by one
of the fixed groups instead of one exact State (`hospital_agent.state_groups.STATE_GROUPS`,
mirrored in the frontend's `labels.ts`); an unknown group is `422 invalid_filter`, and so is
sending `?state=` and `?group=` together (fix round 1, I3) - they are alternative filters,
never combined into a narrower one.
`?escalation_kind=<EscalationKind>` narrows `group=staff` further to one escalation kind
(an unknown kind is `422 invalid_filter`); with any other group, or with no `group` at all,
`escalation_kind` is `422 invalid_filter`.
`200`:

```json
{
  "items": [
    {
      "case_id": "CASE-23FE645294B7",
      "patient_id": "P-10041",
      "state": "Completed",
      "intent": "AppointmentPreparation",
      "safety_level": "MediumRisk",
      "escalation_kind": null,
      "escalated_from_state": null,
      "created_at": "2026-09-19T22:12:38.560531Z",
      "updated_at": "2026-09-19T22:12:48.986200Z",
      "llm_cost_usd": "0.00213400",
      "llm_cost_partial": false,
      "llm_unpriced_calls": 0
    }
  ],
  "next_cursor": null
}
```

`llm_cost_usd` (sub-project 19) is the case's LLM cost so far, a money string or `null` under
§10's NULL rule. `llm_cost_partial` is `true` when the case has at least one unpriced attempt
(`price_input_per_mtok IS NULL`) **and** a non-null `llm_cost_usd`: the cost shown is then a
lower bound, since the unpriced attempts are not in it. It is `false` otherwise - including when
`llm_cost_usd` is `null` (no attempt at all, or nothing priced). `llm_unpriced_calls` is the
number of the case's attempts with no price (`price_input_per_mtok IS NULL`), `0` when it has no
attempt at all - it tells the two nulls apart. **UI rule:** a `null` cost with
`llm_unpriced_calls > 0` shows "מחיר לא ידוע"; a `null` cost with `llm_unpriced_calls` 0 shows
"—". All three come from the one grouped query over the page's case ids, never one per row.

The items are ordered `updated_at` descending, `case_id` descending (a tie-break, since
`updated_at` alone is not unique). `next_cursor` is an opaque string (a `c|` kind prefix
over base64 of `updated_at|case_id`, fix round 1 M5); `null` means there is no next page.
Ask for the next page with `?cursor=<next_cursor>&state=...` (repeat the same filter).

States: `Received`, `Classifying`, `Classified`, `Planning`, `RetrievingData`,
`Delivering`, `AssessingReadiness`, `AwaitingPatientInput`, `AwaitingHumanReview`, `Ready`,
`Completed`, `Failed`, `AwaitingPatientReply` (sub-project 15, §8). `intent` is
`AppointmentPreparation`, `MedicalQuestion` or `Unsupported`; `safety_level` is `LowRisk`,
`MediumRisk`, `HighRisk` or `CriticalRisk`.

### GET /api/staff/cases/{case_id}

The expanded row's detail only (staff-fixes design Task 3): the plan, the documents and
the counters the list above does not carry. `200`:

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
  "created_at": "2026-09-19T22:12:38.560531Z",
  "appointment_id": "APT-8392",
  "answered_appointment_id": "APT-8392",
  "department": "Cardiology",
  "exam_type_label": "מבחן מאמץ",
  "instruction_source_id": "INSTR-CARD-STRESS",
  "instruction_version": "1",
  "llm_usage": {
    "calls": 2, "input_tokens": 3600, "cached_input_tokens": 0, "output_tokens": 52,
    "cost_usd": "0.00078240", "unpriced_calls": 0,
    "by_call": [
      {"call": "Intent", "calls": 1, "input_tokens": 1800, "output_tokens": 40, "cost_usd": "0.00040800"},
      {"call": "Safety", "calls": 1, "input_tokens": 1800, "output_tokens": 12, "cost_usd": "0.00037440"}
    ]
  }
}
```

`intent` is `AppointmentPreparation`, `MedicalQuestion` or `Unsupported`; `safety_level` is
`LowRisk`, `MediumRisk`, `HighRisk` or `CriticalRisk`. `404 case_not_found`.

`llm_usage` (sub-project 19) is always an object: the case's LLM attempts (`calls`), their
summed tokens, `cost_usd` under §10's NULL rule, `unpriced_calls` and `by_call` (one entry per
call code, ordered by code; `[]` for a case with no attempt). It is deliberately **not** part of
`GET /api/staff/cases/{case_id}/context`: that `shown` is hashed into `shown_context_ref`, and
bookkeeping written while a reviewer reads must never refuse their decision as `context_changed`.

`appointment_id`, `answered_appointment_id`, `department`, `exam_type_label`,
`instruction_source_id` and `instruction_version` (sub-project 18, design D13) are read-only
staff fields: the appointment the patient chose (if any) and the one the appointment-service
actually resolved, its department and exam type, and the instruction source the policy
approved for this case. `appointment_id` is write-once: stored from the request at
`REQUEST_SUBMITTED` and never changed afterwards (the service's own answer goes to
`answered_appointment_id`, `docs/spec_corrections.md` row 92), so it is `null` exactly when
the patient chose no appointment - on the mock path too. Fix round 1 (M6):
`answered_appointment_id`, `department` and `exam_type_label` are `null` on the mock path
(`MockGateway` never sets them) and on a case that never resolved an appointment - but
`instruction_source_id` and
`instruction_version` are **not**: even the mock path stores a source
(`INSTR-PREP-COLONOSCOPY`/`3`, design D7), since every `LoadInstructions` needs one. Never
shown to the patient.

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

The queue of cases in `AwaitingHumanReview`, **newest entry into that State first**
(staff-fixes design Task 5) - a case that returns from a patient's reply or a request that
ran out of time re-enters and jumps to the top, since it needs attention again. One
statement (a LEFT LATERAL join to each case's latest entry row, fix round 1 M4 - a case
somehow missing that row is never dropped from the queue, only shown with its
`cases.updated_at` as a fallback `entered_at` and `reasons: []`), keyset-paginated the same
way as `GET /api/staff/cases`: `?limit=` (default 50, at most 200; otherwise `422
invalid_limit`) and `?cursor=` (opaque, an `r|` kind prefix over base64 of
`entered_at|case_id` - distinct from `GET /api/staff/cases`'s `c|` prefix, so a cursor from
one list is `422 invalid_cursor` on the other, fix round 1 M5). `200`:

```json
{
  "items": [
    {
      "case_id": "CASE-6FFF40DFB8DA",
      "patient_id": "P-10041",
      "escalation_kind": "MedicalQuestion",
      "escalated_from_state": "Classifying",
      "reasons": [],
      "allowed_decisions": ["resolve", "reject"],
      "required_fields": [],
      "entered_at": "2026-09-19T22:12:39.693277Z",
      "human_engaged": false,
      "returned_by": null
    }
  ],
  "next_cursor": null
}
```

- `escalation_kind`: `PatientVerificationFailed`, `MedicalQuestion`, `SafetyEscalation`,
  `ClassificationFailed`, `TemporalViolation`, `PlanningFailed`, `PolicyDenied`,
  `PolicyReview`, `RetryExhausted`, `NonIdempotentFailure`, `ExecutionUnknown`,
  `Z3Counterexample`, `PatientSlaExpired`, `DeliveryStepMissing`.
- `human_engaged` / `returned_by` (sub-project 15) - see §8 below.
- `reasons`: the `policy_reasons` of the row that escalated the case (e.g.
  `["medical_answer_attempt"]`, `["hours_until:20"]`). May be empty.
- `entered_at`: when the case entered `AwaitingHumanReview` - the queue's order key
  (staff-fixes design Task 5), replacing the older `updated_at` (the two agreed on every
  case that had never re-entered review, but meant the wrong thing for one that had).
- `allowed_decisions`: **render exactly these buttons.** `approve` appears only for the
  five kinds a case can resume from (`PatientVerificationFailed`, `RetryExhausted`,
  `PolicyReview`, `Z3Counterexample`, `PatientSlaExpired`); `resolve` and `reject` always.
- `required_fields`: the extra fields `approve` needs - `["verified_identity_ref"]`
  (`PatientVerificationFailed`) or `["patient_deadline"]` (`Z3Counterexample`,
  `PatientSlaExpired`); otherwise `[]`. Render them in the approve form; the server
  refuses an approve without them (`409 verified_identity_ref_required` /
  `409 patient_deadline_required`).

### GET /api/staff/reviews/{case_id}

Staff-fixes design Task 3: one queue item, in the same shape as a `GET /api/staff/reviews`
item (above) - so `ReviewCase` reads its own row without fetching the whole queue. `200` is
the single object (not wrapped in a page); `404 not_in_review` when the case is not (or no
longer) in `AwaitingHumanReview`; `404 case_not_found` for an unknown case.

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
      "recorded_at": "2026-09-19T22:12:39.678380Z",
      "guards": {"PatientIdentified": true},
      "outcome": null,
      "attempt_number": null,
      "retry_cycle": null,
      "execution_id": null,
      "approval_id": null
    }
  ],
  "intent": "MedicalQuestion",
  "safety_level": "LowRisk",
  "llm_calls": [
    {"call": "Intent", "source": "agent", "outcome": "ok", "created_at": "2026-09-19T22:12:40.912000Z"}
  ],
  "shown_context_ref": "ctx-ba3e0652b0ea355663db57e7d19550c61ee1d156fea64c91c2cacd539bbc7d83",
  "appointment_id": null,
  "answered_appointment_id": null,
  "department": null,
  "exam_type_label": null,
  "instruction_source_id": null,
  "instruction_version": null
}
```

- `appointment_id` … `instruction_version` (sub-project 18, design D13) are the same
  read-only fields as `GET /api/staff/cases/{case_id}` above - part of what the reviewer is
  shown, so they are part of `shown_context_ref` too (a case whose source changed after the
  context was fetched is `409 context_changed`, same as any other change).
- `data` is the Data Log (§12.3) - the only place content lives. `kind` is `request_text`,
  `uploaded_document`, `instructions`, `outgoing_message`, `staff_message` or `patient_reply`
  (the last two, sub-project 15, §8). Deleted entries and uploads the case never accepted
  are not listed at all.
- `trace` is the same audit rows as `/audit`, with the content-free subset above. `guards`
  is the row's guard results and policy evidence (`{name: bool}`; `{}` on a `Blocked` row),
  and `outcome` (`success` | `failed` | `unknown`), `attempt_number`, `retry_cycle`,
  `execution_id` and `approval_id` are the execution facts - the staff audit timeline shows
  the gates each row passed and each attempt from them. They are part of what is shown, so
  part of `shown_context_ref`.
- `intent` / `safety_level` are the case's **latest** classification (`cases`); the
  `INTENT_CLASSIFIED` audit row holds neither, so a case classified again (after a valid upload,
  T10) has lost its earlier values. `llm_calls` is the case's LLM attempts (`llm_usage`) in
  order - `call`, `source` (`agent` | `document_service`), `outcome` (`ok` or the unusable
  answer's code) and `created_at`, never text, tokens or cost. Both are part of
  `shown_context_ref`.
- `upload_attempts` (row 98) is every patient upload attempt on the case, in order - including
  the refused and unanswered ones, which add no event and so appear nowhere else: `kind`
  (`upload` | `reply`), `outcome` (the code the patient was shown: `accepted`, `not_medical`,
  `document_service_unavailable`, …), `reason` (the detail behind it - the document-service's
  own reason, or `no_answer` / `status_<n>` / `invalid_response` / `classifier_unavailable`,
  or `null`) and `created_at`. Codes only, never the file, its name or a document id. Part of
  `shown_context_ref` too.
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

### GET /api/staff/system-status

Staff-fixes design Task 1, decision 3: is the LLM (still) called at all, and does the Agent
Orchestrator run. `200`:

```json
{"orchestrator": "running",
 "llm": {"last_ok_at": "2026-09-26T10:00:03.512841+00:00", "last_error": null, "last_error_at": null},
 "documents": {"configured": true, "health": "ok", "last_ok_at": null, "last_error": null,
               "last_error_at": null}}
```

- `orchestrator` is exactly `/health`'s field: `null` on an injected (test) server, `"running"`,
  or a `"disabled: ..."` reason.
- `llm` carries the detail `/health`'s own `llm` field no longer does (fix round 1, M6 - `/health`
  is public, this route is staff-only): the last success time and the last error code and time,
  all `null` until the first LLM call, each timestamp `datetime.isoformat()` (a UTC offset,
  `+00:00`, with microseconds). The error code is one of `unparsable`, `schema_violation`,
  `worker_died` (the Response Evaluator's worker process died and was replaced), or
  `api:<ExceptionType>[:<code>]` (e.g. `api:AuthenticationError`,
  `api:RateLimitError:insufficient_quota`) - never request text, a prompt or an answer.

- `documents` (row 98): the document-service. `configured` is whether uploads go to it at all;
  `health` is a live `GET /health` made on this request (3 s): `ok`, `degraded` or `unreachable`
  (`null` when not configured). `last_ok_at` / `last_error` / `last_error_at` are the last
  upload's outcome, kept in memory like `llm`'s: `last_ok_at` is set when the service answered
  (whatever it said about the file), `last_error` is `no_answer`, `status_<n>`,
  `invalid_response` or `classifier_unavailable`.

The staff UI shows a banner while `orchestrator` is not `"running"`, or `last_error` is set and
newer than `last_ok_at` - each with a Hebrew label beside the code - and a second one for the
document-service while its `health` is not `ok`, or its `last_error` is newer than its
`last_ok_at`.

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
  },
  "llm": {
    "cases": 3,
    "cases_with_usage": 3,
    "calls": 10,
    "input_tokens": 17100,
    "cached_input_tokens": 0,
    "output_tokens": 380,
    "total_cost_usd": "0.00387600",
    "avg_cost_per_case_usd": "0.00129200",
    "avg_cost_per_completed_case_usd": "0.00129200",
    "unpriced_calls": 0,
    "by_call": [
      {"call": "Evaluator", "calls": 2, "input_tokens": 2600, "output_tokens": 20, "cost_usd": "0.00054400"},
      {"call": "Intent", "calls": 3, "input_tokens": 5400, "output_tokens": 120, "cost_usd": "0.00122400"},
      {"call": "Planner", "calls": 2, "input_tokens": 4000, "output_tokens": 180, "cost_usd": "0.00101600"},
      {"call": "Safety", "calls": 3, "input_tokens": 5100, "output_tokens": 60, "cost_usd": "0.00109200"}
    ],
    "by_source": [{"source": "agent", "calls": 10, "cost_usd": "0.00387600"}]
  }
}
```

`flow` and `llm` count the cases **opened** in the window (`flow` in their current state); every
other group counts what **happened** in it. Durations are seconds; `p50` / `p95` / `max` are `null` when
`count` is 0. `by_outcome` is one of `MedicalQuestion`, `EscalatedAtClassification`,
`AppointmentPreparation`, `Unsupported`, `NotClassified`. `open_by_kind`, `open_now` and
`oldest_open_seconds` describe the review queue **now**, whatever the window. `sources` is
`null` for each system while the Agent Orchestrator is not running. The answer carries no
`patient_id`, `case_id` or request text.

`llm` (sub-project 19) is the LLM cost of the same window's cohort, read inside the same
snapshot: exactly the body of `GET /api/staff/llm-costs` (§10) without its `window`.

### GET /api/admin/consistency

`admin_staff` only (`401` / `403 admin_only` as `/metrics`). Runs the spec §9.2 cross-layer
consistency proofs now - seven abstract properties in nine Z3 queries over the layers' encodings
(`policy/consistency.py`, the same check as `python -m hospital_agent.policy.consistency`).
A query holds when Z3 finds no counterexample (`unsat`). They are about the system, not a case,
so no case's Audit carries them. `200`:

```json
{
  "engine": "z3",
  "all_proved": true,
  "queries": [
    {"property": "P1", "description": "OPA allows what Prolog blocks", "result": "unsat", "proved": true}
  ]
}
```

- `queries` is always the nine, in `QUERIES` order (P1 three times, then P2-P7).
- `result` is Z3's answer: `unsat`, `sat` (a counterexample - the property fails) or `unknown`.

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
multipart contract, same size limits, same 411/413 body-size checks in front of the route;
sub-project 17 task 2: same PDF/JPEG/PNG acceptance and the same document-service `reason`
mapping) - offered only while `reply_request.kind` is `"document"`. `200`:

```json
{"upload": {"code": "accepted", "document_type": "URINALYSIS"}, "request": {"...": "the patient view"}}
```

`upload.code` is one of §4's codes reachable here - `accepted`, `not_medical`, `unrecognised_type`,
`unreadable_scan`, `bad_date`, `no_date`, `unsupported_format`, `too_large`, `unreadable`,
`expired`, `not_yours` - plus one more:

| `upload.code` | Meaning | The case |
|---|---|---|
| `accepted` | A readable, valid document of the type requested (or a re-sent copy already accepted) | Leaves `needs_reply`, back to `in_review` |
| `wrong_document_type` | Readable and valid, but not the catalog type the staff member asked for | Unchanged |
| `not_medical`, `unrecognised_type`, `unreadable_scan`, `bad_date`, `no_date`, `unsupported_format`, `too_large`, `unreadable`, `expired`, `not_yours` | As in §4 | Unchanged |

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

## 9. The patient's appointments (sub-project 16, sub-project 18)

```
GET /api/patient/appointments?from=&to=
GET /api/staff/cases/{case_id}/appointments?from=&to=
```

Read live from the appointment-service on every call and never stored here
(`docs/spec_corrections.md` row 89, beside row 79) - there is no cache, and a repeated
request simply asks again. The patient route works on the token's own patient - a query
`patient_id` is accepted but ignored, exactly like every other patient route (§18.3); the
staff route works on the case's patient, resolved server-side, and needs a `clinical_staff`
or `admin_staff` token. It is deliberately a separate route from `GET
.../cases/{case_id}/context`: the appointment list is not part of what a decision is bound
to, so reading it never changes `shown_context_ref`.

**The window.** `from` is inclusive, `to` is exclusive; both are ISO 8601 and must carry a
time zone offset (a value with no offset is rejected, not assumed to be UTC or local time;
`Z` is accepted for UTC). Neither given: `from` is now, `to` is 30 days after it. One given:
the other is 30 days from it (before `to`, or after `from`). Both bounds in the answer are
the exact instants the read actually used - a default `from` fixed once for that request, not
recomputed - converted to UTC regardless of what offset the query sent. A reversed window
(`to` at or before `from`), one longer than 366 days, or one whose bound, or whose default
30-day span computed from a bound near the very edge of the representable date range (year 1
or 9999), cannot be represented at all, is `422 invalid_range`.

A literal `+` in an offset (e.g. `+03:00`) must be percent-encoded as `%2B` in the query
string: an unencoded `+` is decoded as a space by ordinary URL decoding, and the resulting
value fails to parse - also `422 invalid_range`, not a silently wrong offset.

**Check order.** The staff route resolves the case first (`404 case_not_found` before
anything else). Both routes then check whether an appointment-service is configured at all
(`404 appointments_not_enabled`) *before* parsing the window - an unconfigured server answers
404 even for a query that would otherwise be `422`. Only once a client exists is the window
itself validated (`422 invalid_range`); only once that holds is the appointment-service
actually asked (`404 patient_not_found` / `503 appointments_unavailable`).

`200`:

```json
{
  "from": "2026-10-01T00:00:00Z",
  "to": "2026-10-31T00:00:00Z",
  "appointments": [
    {
      "appointment_id": "APT-8391",
      "appointment_at": "2026-10-03T07:30:00Z",
      "department": "Neurology",
      "doctor_name": "Dr. Cohen",
      "location": "Building B, Floor 2",
      "status": "Scheduled",
      "required_documents": ["CBC", "ECG"],
      "exam_type": {"code": "NEURO_VISIT", "label": "ביקור במרפאה נוירולוגית"},
      "instruction": {"source_id": "INSTR-NEURO-VISIT", "version": "1", "title": "הכנה לביקור במרפאה נוירולוגית"}
    }
  ],
  "truncated": false
}
```

`status` is `Scheduled` or `Cancelled`. `truncated` is `true` when the appointment-service's
own answer was cut off at its cap (100 rows) rather than the full window's worth - the UI
should say the list may be incomplete and suggest narrowing the range. `appointment_at` is
always normalised to UTC before it goes out, exactly like the window bounds - whatever offset
or zone the appointment-service itself answered with.

`exam_type` and `instruction` (sub-project 18, design D3) are never null in the real service,
but the client treats each as optional - an older service simply omits it, and a row missing
either is still delivered without it. A *present* `exam_type` or `instruction` with the wrong
shape (not an object, an empty or over-200-character `code`/`label`/`title`, or a
`source_id`/`version` that is not the same id shape used everywhere else) makes the whole
answer `503 appointments_unavailable` (`invalid_response`) - never a partial row. `instruction`
carries only the title, never the text; the appointments panel loads the text separately
through `GET /api/patient/instructions/{source_id}?version=` below.

- `401 not_authenticated` - as everywhere.
- `403 patients_only` (the patient route, a staff token) / `403 staff_only` (the staff
  route, a patient token).
- `404 case_not_found` - the staff route, an unknown case; checked before everything below.
- `404 appointments_not_enabled` - the server has no appointment-service configured
  (`APPOINTMENT_SERVICE_URL` + `APPOINTMENT_API_KEY`); there is no mock for this route
  (design decision: unlike `CheckAppointment`'s own gateway, sub-project 10). Checked before
  the window, so a bad window on an unconfigured server is still this code, not `422`.
- `422 invalid_range` - `from` or `to` does not parse as ISO 8601, either carries no time
  zone offset, `to` is at or before `from`, the span is over 366 days, or a bound (or the
  default span from one) does not fit in the representable date range. Neither bound is
  echoed in the error.
- `404 patient_not_found` - the appointment-service's registry does not know the patient.
- `503 appointments_unavailable` - the appointment-service did not answer, answered
  something other than its documented 200/404 shape, or answered any other status. The
  application log records one line, `appointment list: <code> in <n> ms`, for every outcome
  (success included), where `<code>` is `ok`, `patient_not_found`, one of the codes above
  (`no_answer`, `status_<n>`, `invalid_response`), or `client_error` (the appointment-list
  client's own defensive `ValueError` - a naive datetime, a malformed `patient_id`, or the
  client itself misconfigured, e.g. an invalid API key header the transport rejects before a
  request is even sent; the first two are not reachable through these routes, since they always
  resolve a token- or case-bound `patient_id` and always build the window as aware datetimes,
  so in practice `client_error` means an operator's configuration mistake) - never the patient_id and never an appointment (§12.3, design D9).

### The instruction text (sub-project 18, design D12)

```
GET /api/patient/instructions/{source_id}?version=
GET /api/staff/instructions/{source_id}?version=
```

The full text behind one appointment's `instruction` summary above (title only there). Read
live from the appointment-service on every call, never cached and never stored - the same
recorded exception as the appointment list itself (`docs/spec_corrections.md` row 89, which
sub-project 18 extends to cover this read too). The patient route answers for any `source_id`/`version`
the registry currently approves, not only ones on the patient's own appointments: the
instruction texts are generic catalog content (one per exam type, not per patient) with nothing
patient-specific in them, so the route needs no ownership check. The staff route is identical
and needs no case id - it is not bound to any one case's `shown_context_ref` (design D13's
appointment/instruction fields on `CaseDetail`/`ReviewContext` already carry the source for
staff viewing).

Both `source_id` and `version` must match the same id shape used everywhere else in this API
(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, e.g. `NewRequest.appointment_id`) - `version` is a
required query parameter, not optional; a missing or malformed one is `422 invalid_instruction`.

**Check order**, mirroring the appointment list's own convention: whether an
appointment-service is configured at all (`404 instructions_not_enabled`) is checked *before*
the id/version pattern - an unconfigured server answers 404 regardless of what the path or
query looks like. Only once a client exists are `source_id`/`version` validated
(`422 invalid_instruction`); only once that holds is the Approved Source Registry consulted
(`404 instruction_not_approved`, or `503 instructions_unavailable` when OPA itself cannot be
asked); only once the registry approves is the appointment-service actually asked
(`503 instructions_unavailable`).

**The registry, never the service, approves.** The requested `source_id`/`version` is put to
the real OPA binary - the same `policy.rego` rule (`instruction_source_approved`) the Policy
Service's own `decision` reads, over the same policy bundle
(`policy/data/approved_instruction_sources.json`) - never a second, Python reimplementation of
its time parsing, and never trusting the appointment-service's own answer. OPA approves only
when `source_id` is listed with `approved: true`, the exact same `version`, and the current
time (OPA's own clock) inside `[valid_from, valid_until)`. Anything else - unlisted, the wrong
version, not yet valid, or expired - is `404 instruction_not_approved`, and the
appointment-service is never called for it. When OPA itself cannot be asked (the binary
missing, a crash or non-zero exit, a timeout, or output that is not `opa eval`'s own shape),
that is not a verdict on the source: the answer is `503 instructions_unavailable` instead, and
it is exactly as closed - nothing is approved and the appointment-service is never called.

`200`:

```json
{
  "source_id": "INSTR-NEURO-VISIT",
  "version": "1",
  "title": "הכנה לביקור במרפאה נוירולוגית",
  "text": "רשימת תרופות, הדמיות קודמות, יומן התקפים או תסמינים. ... טיוטת דמו – טעונה אישור רפואי. בכל שאלה רפואית יש לפנות לצוות המטפל."
}
```

The answer is exactly the requested `source_id` + `version`, with a non-empty `title` (at most
200 characters) and a non-empty `text` (at most 4000 characters) - the same bounds
`LoadInstructions`'s own gateway enforces (`docs/spec_corrections.md` row 90).

- `401 not_authenticated` - as everywhere.
- `403 patients_only` (the patient route, a staff token) / `403 staff_only` (the staff route,
  a patient token).
- `404 instructions_not_enabled` - no appointment-service configured
  (`APPOINTMENT_SERVICE_URL` + `APPOINTMENT_API_KEY`); checked before everything below.
- `422 invalid_instruction` - `source_id` or `version` does not match the id shape, or
  `version` is missing.
- `404 instruction_not_approved` - the Approved Source Registry does not currently approve this
  exact `source_id` + `version` (unlisted, a different version, not yet valid, or expired). The
  appointment-service is never asked in this case. Only OPA's own answer is a deny; OPA being
  unavailable is the `503` below, never this.
- `503 instructions_unavailable` - OPA itself could not be asked (the binary missing, a crash or
  non-zero exit, a timeout, or unreadable output; the appointment-service is then never asked),
  or the appointment-service did not answer, answered something
  other than its documented 200 shape, answered for a different source or version than asked,
  itself answered `404 instruction_not_found` (the registry and the service disagree - never
  delivered as though approved), or the client itself was misconfigured (e.g. an invalid API
  key header the transport rejects before a request is even sent - an operator's mistake, never
  the caller's). The application log records one line, `instruction read: <code> in <n> ms`,
  for every *service-call* outcome (success included), and for an OPA outage
  (`policy_unavailable`) - never for the checks above it (not-configured, a bad id/version,
  or the registry's own denial, which are a fixed verdict on the request itself, not a call
  to the appointment-service) - where `<code>` is `ok`,
  `not_found`, one of the client's own codes (`no_answer`, `status_<n>`, `invalid_response`),
  `client_error` (the client's defensive `ValueError`), or `policy_unavailable` (OPA itself
  could not be asked, so the appointment-service was not either) - never the source_id, the
  title or the text (§12.3).

## 10. LLM cost (sub-project 19)

`docs/superpowers/specs/2026-09-27-llm-costs-design.md` (D5, D6). Staff only: no patient route
carries a cost, a token count or a model.

**What is counted.** One `llm_usage` row per LLM *attempt*, ok or failed: the agent's four
calls (`Intent`, `Safety`, `Planner`, `Evaluator`, `source` `agent`) and the document-service's
one call per upload (`DocumentClassify` for a text classification, `DocumentVision` for an
image or a scanned PDF, `source` `document_service`, reported on its 201 answer as
`llm_usage` and recorded by the Session Service with outcome `ok`). A document-service report
that is not the documented shape is dropped (logged `llm_usage_invalid`, code only) and the
upload goes on unchanged.

**Money** is a decimal string with exactly 8 places, never a JSON number: `"0.00213400"`,
`"0.00000000"`. Costs are in USD, `input_tokens × input price + output_tokens × output price`
per 1M tokens, rounded half-up to 8 places; cached input tokens are billed at the full input
price (design D3).

**The NULL rule** - for a case's `llm_cost_usd` / `llm_usage.cost_usd`, and for every
`total_cost_usd` and `cost_usd` below:
- the cost is the **sum of the non-null costs** of the rows;
- a row is **unpriced** when its model has no price (`price_input_per_mtok IS NULL`, e.g. the
  document-service's `fake` classifier) - not merely when its cost is NULL: an API error on a
  priced model keeps its price, has NULL tokens and a NULL cost, and billed nothing;
- the cost is `null` when there is **no row at all** (nothing processed yet; the UI shows "—",
  not "unknown"), or when there is **an unpriced row and no priced cost at all** (the UI shows
  "מחיר לא ידוע");
- rows that are all priced API errors cost `"0.00000000"`, not `null`;
- a cost beside `unpriced_calls > 0` is a lower bound: the unpriced attempts are not in it.

### GET /api/staff/llm-costs

`?from=<ISO-8601>&to=<ISO-8601>` - the admin metrics' window rules (§7): `from` inclusive, `to`
exclusive, both with a time zone, at most 90 days apart; a missing parameter is
`422 invalid_body`, an unparsable, zone-less, empty or reversed window `422 invalid_range`, a
longer one `422 range_too_large`. Any staff member (`clinical_staff` or `admin_staff`); a patient
token is `403 staff_only`. One read-only `REPEATABLE READ` snapshot with a 5 s statement timeout;
past it `503 llm_costs_unavailable`, never a partial answer. `200`:

```json
{
  "window": {"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"},
  "cases": 5,
  "cases_with_usage": 4,
  "calls": 6,
  "input_tokens": 7100,
  "cached_input_tokens": 1000,
  "output_tokens": 333,
  "total_cost_usd": "0.00130360",
  "avg_cost_per_case_usd": "0.00043453",
  "avg_cost_per_completed_case_usd": "0.00050200",
  "unpriced_calls": 2,
  "by_call": [
    {"call": "DocumentClassify", "calls": 1, "input_tokens": 900, "output_tokens": 40, "cost_usd": null},
    {"call": "DocumentVision", "calls": 1, "input_tokens": 1200, "output_tokens": 40, "cost_usd": null},
    {"call": "Intent", "calls": 2, "input_tokens": 4000, "output_tokens": 183, "cost_usd": "0.00101960"},
    {"call": "Planner", "calls": 1, "input_tokens": 1000, "output_tokens": 70, "cost_usd": "0.00028400"},
    {"call": "Safety", "calls": 1, "input_tokens": 0, "output_tokens": 0, "cost_usd": "0.00000000"}
  ],
  "by_source": [
    {"source": "agent", "calls": 4, "cost_usd": "0.00130360"},
    {"source": "document_service", "calls": 2, "cost_usd": null}
  ]
}
```

- **The cohort** is the cases **opened** in the window (`cases.created_at`), with **all** of
  their usage, whenever it was written - so a case's cost is counted once, in the window it was
  opened in. `cases` counts the cohort; `cases_with_usage` the cohort cases with at least one
  row; `calls`, the token sums and `unpriced_calls` count the cohort's rows (token sums count
  the attempts that reported usage).
- `by_call` / `by_source` hold one entry per call code / source present, ordered by code.
- **`avg_cost_per_case_usd`** = the cohort's priced cost / the number of cohort cases with **at
  least one priced row**. A case with no usage yet (not processed), or with only an unknown
  model's rows, has no known cost - counting it as 0 would drag the average down.
- **`avg_cost_per_completed_case_usd`** = the same average over the cohort cases now in
  `Completed`.
- Either average is `null` when its denominator is 0. Averages are rounded half-up to 8 places.
- The averages cover **priced cases only**, while `total_cost_usd` follows the NULL rule over
  every row. So an average can be `"0.00000000"` while `total_cost_usd` is `null`: e.g. one case
  whose only attempts were priced API errors (cost 0) and one with only unpriced rows (cost
  unknown) - the total is unknown, the average over the one priced case is 0.
- The answer carries no `case_id`, `patient_id` or model name.
