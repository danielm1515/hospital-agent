# HTTP API contract

The backend's complete public interface. Sub-project 6 (the React UI) is built from this
file alone: every route, every request body, every response body and every error code is
here, and the JSON below is real output of the running system.

- **Base URL (demo):** `http://localhost:8000`
- **Content type:** `application/json`, UTF-8. Text may be Hebrew.
- **Timestamps:** ISO 8601 in UTC, e.g. `2026-09-19T22:12:47.693947Z`. The one timestamp
  the client sends (`patient_deadline`) must carry a timezone offset.
- **Errors:** always `{"detail": "<code>"}` - a short, stable code, never a sentence and
  never internal detail. The one exception is `422`, where FastAPI's own validation body
  (a list under `detail`) is returned; the UI can treat any 422 as "the form is invalid".
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

The API allows `http://localhost:5173` and `http://127.0.0.1:5173` (the Vite dev server),
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
| GET | `/api/staff/cases` | staff | All cases (`?state=`) |
| GET | `/api/staff/cases/{case_id}` | staff | One case, in full |
| GET | `/api/staff/cases/{case_id}/audit` | staff | The case's audit trace |
| GET | `/api/staff/reviews` | staff | The human-review queue |
| GET | `/api/staff/cases/{case_id}/context` | staff | What the reviewer is shown |
| POST | `/api/staff/cases/{case_id}/decision` | staff | Approve / resolve / reject |
| DELETE | `/api/staff/cases/{case_id}/data/{entry_id}` | staff | Delete one Data Log entry |

Codes used everywhere: `401 not_authenticated` (no token, a malformed token, an expired or
forged one), `403 patients_only` / `403 staff_only` (the wrong role), `404 case_not_found`
(unknown, or not this patient's case), `422` (the body failed validation).

## 3. Public

### GET /health

```json
{"status": "ok", "database": "ok", "orchestrator": "running"}
```

`orchestrator` appears only on a real server: `"running"`, or
`"disabled: OPENAI_API_KEY is not set"`. `503` with
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
- `422` - `user_id` over 64 characters, `password` over 256, or a missing field.

### GET /api/auth/me

`200`:

```json
{"user_id": "P-10041", "role": "patient", "display_name": "דנה כהן"}
```

- `401 not_authenticated`.

## 4. Patient routes

All four routes work on the patient the token names. Another patient's case is `404
case_not_found`, exactly like a case that does not exist.

### The patient view

Every patient route answers with this object, and nothing else:

```json
{
  "case_id": "CASE-23FE645294B7",
  "status": "needs_document",
  "created_at": "2026-09-19T22:12:47.693947Z",
  "updated_at": "2026-09-19T22:12:47.898132Z",
  "request_text": "When is my appointment and which documents do I need?",
  "missing_document_ids": ["blood_test"],
  "missing_document_request_template_id": "missing-document-v1",
  "message": null
}
```

| `status` | Meaning | What the UI shows |
|---|---|---|
| `received` | Submitted, not yet being worked on | "Received" |
| `in_progress` | The agent is working (classifying, planning, retrieving) | A spinner; keep polling |
| `needs_document` | A document is missing | The upload form for `missing_document_ids` |
| `in_review` | A human is handling it | "A staff member is reviewing your request" - **no** reason, no kind |
| `completed` | An answer was delivered | `message` |
| `closed` | Finished without a delivered message (a reviewer resolved or rejected it) | "Your request was closed. The clinic will contact you." |

- `request_text` is the text the patient submitted; `null` once a staff member has deleted
  it from the Data Log (§18.4).
- `missing_document_ids` is non-empty only in `needs_document`;
  `missing_document_request_template_id` is then `"missing-document-v1"` (D24) - the UI
  renders the request for the document itself, the system never sends one.
- `message` is non-null only in `completed`: the exact text that was delivered.

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
- `422` - empty, whitespace-only or over 2000 characters.
- `409 <reason>` - the event was refused by the state machine (it does not happen for a
  valid body; `detail` carries the machine's reason code).

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

`200`: the patient view **as it now stands**, also when the upload was refused - a document
the case was not waiting for is dropped and deleted again (D25), and the patient simply
sees an unchanged view. The UI should compare `status` / `updated_at` rather than assume
success.

- `404 case_not_found`, `403 patients_only`, `422` for a bad body.

## 5. Staff routes

Every route needs a `clinical_staff` or `admin_staff` token; a patient token gets
`403 staff_only`.

### GET /api/staff/cases

Optional `?state=<State>`; an unknown state is `422`. `200`:

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
`Completed`, `Failed`.

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
    "updated_at": "2026-09-19T22:12:39.693277Z"
  }
]
```

- `escalation_kind`: `PatientVerificationFailed`, `MedicalQuestion`, `SafetyEscalation`,
  `ClassificationFailed`, `TemporalViolation`, `PlanningFailed`, `PolicyDenied`,
  `PolicyReview`, `RetryExhausted`, `NonIdempotentFailure`, `ExecutionUnknown`,
  `Z3Counterexample`, `PatientSlaExpired`, `DeliveryStepMissing`.
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
  `uploaded_document`, `instructions` or `outgoing_message`. Deleted entries and uploads
  the case never accepted are not listed at all.
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

`reviewer_id` and `reviewer_role` are taken from the token; sending them changes nothing.

`200`:

```json
{"case_id": "CASE-6FFF40DFB8DA", "state": "Completed"}
```

`state` is the case's state **after** the decision - and after anything that followed it
automatically. What to expect:

| Decision | Kind | Resulting state |
|---|---|---|
| `resolve` | any | `Completed` (the patient sees `closed` - nothing was sent) |
| `reject` | any | `Failed` (the patient sees `closed`) |
| `approve` | `PatientVerificationFailed` | `Classifying` (the request is re-validated automatically and goes on) |
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
| 409 | `request_text_unavailable` | An approved `PatientVerificationFailed` case whose request text was deleted: the decision stands, but the case stays in `Received` |
| 409 | other codes | Any other guard that refused the human event; show `detail` and re-fetch the case |
| 422 | (validation body) | A field is over its length limit, or `patient_deadline` has no timezone |

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
- **There is no content-approval route.** A medical message is denied and the case reaches
  the queue; the reviewer closes it with `resolve` (design decision 6).
- **Error handling:** `401` → back to login; `403` → the wrong screen for this role;
  `404` → the case is gone or not the user's; `409` → show `detail`, re-fetch, try again;
  `422` → a form error; `503` on `/health` → the database is down.
