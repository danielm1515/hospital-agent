# Sub-project 16: the patient's appointments, for the patient and for the staff

Status: design, autonomous mode (CLAUDE.md). Owner's request (2026-09-26): the patient sees their
appointments on the main screen, filterable by a from-to date range (default 30 days); a staff member
inside a patient's case sees the same list with the same filter (default 30 days). Agreed earlier
(paused brainstorm, sub-project 15 session): a list, an error with retry, and approach A - a new list
endpoint in the owner's appointment-service plus a direct read route in hospital-agent, **not** through
the agent or the FSM.

## 1. The gap

- The appointment-service has no JSON list: `GET /api/v1/patients/{id}/appointment` (CheckAppointment)
  returns at most one appointment (the earliest `Scheduled`), and the only listing is the HTML dashboard.
- hospital-agent reaches the appointment-service only through the Tool Executor's
  `AppointmentServiceGateway`, which lives inside the Agent Orchestrator (built only with an OpenAI key)
  and serves a plan step. No route can reach it, and no plan step should: listing appointments is not
  part of any §0 scenario and changes no State.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | The appointment-service gets `GET /api/v1/patients/{patient_id}/appointments?from=&to=` beside CheckAppointment: the same `X-API-Key`, the same registry check (404 `patient_not_found`, 503 `patient_registry_unavailable`), an audit row with operation `ListAppointments`. | The owner of the fact answers it; one auth and registry path. |
| D2 | `from` and `to` are required timezone-aware ISO instants, `from < to`, at most 366 days apart; otherwise `400 validation_error`. The answer is every appointment (`Scheduled` **and** `Cancelled`) with `from <= appointment_at < to`, ordered by `appointment_at`, capped at 100 rows (`truncated: true` when more exist). | A cancelled appointment is a fact the patient should see (it explains a missing visit). The cap keeps the body under hospital-agent's 64 KB transport bound. |
| D3 | The comparison is done in Israel time: SQLite stores the local wall-clock without an offset (`AppointmentOut._israel_time`), so the service converts `from`/`to` to Asia/Jerusalem and compares naive local values. | Matches how the rows are stored; one conversion point. |
| D4 | hospital-agent reads the list with its own small client, `hospital_agent/appointment_list.py` (`AppointmentListClient`), over the shared `execution/http.py` transport (no redirect, no proxy, bounded body, 5 s timeout), built from the **same** `APPOINTMENT_SERVICE_URL` / `APPOINTMENT_API_KEY` and injected into the app like sub-project 13's `document_intake`. Recorded as `docs/spec_corrections.md` row 89: a second exception to "only the Tool Executor calls an external system" - read-only, never proposed or policy-checked, never an event, never stored. | Same pattern as row 79; nothing enters the FSM, Audit or Data Log. |
| D5 | Routes: `GET /api/patient/appointments?from=&to=` (`require_patient`, the patient from the token) and `GET /api/staff/cases/{case_id}/appointments?from=&to=` (staff; the patient resolved server-side from the case). Both default `from`=now, `to`=now+30 days when omitted; `422 invalid_range` on a bad pair. The staff route is separate from `/context` so `shown_context_ref` does not change. | Identity only from the token (§18.3); the decision binding stays untouched. |
| D6 | Answers: `200 {"from","to","appointments":[…],"truncated"}`; each appointment is `appointment_id`, `appointment_at`, `department`, `doctor_name`, `location`, `status` (`Scheduled`/`Cancelled`), `required_documents`. Failures: `404 appointments_not_enabled` (not configured), `404 case_not_found` (staff, unknown case), `404 patient_not_found` (the registry does not know the patient), `503 appointments_unavailable` (anything else: no answer, 5xx, 401, invalid body). | One code per thing the UI must say differently; nothing internal leaks. |
| D7 | hospital-agent validates the answer strictly (a dict; `appointments` a list of dicts; aware `appointment_at`; `status` in the two known values; strings where strings are expected; `required_documents` a list of strings) and fails the whole answer as `appointments_unavailable` otherwise. | Fail closed on a malformed external answer. |
| D8 | Not configured (no URL): the patient view says nothing is available (`appointments_not_enabled`), no mock list. | A made-up appointment list would mislead a patient; the mock gateway has no per-patient data. |
| D9 | The Application Log gets the outcome code and the latency only - never `patient_id`, never an appointment. | §12.3. |
| D10 | UI: one shared component, `AppointmentsPanel`, used at the top of the patient's "הפניות שלי" and on the staff review screen under the case header. Two date inputs (`from`, `to`, whole days, the browser's zone), default today through today+30, an "הצגה" button, a list (date and time, department label, doctor, location, status, required documents with their Hebrew labels), an empty state, a loading state, an error with a retry button, and a neutral line for `appointments_not_enabled`. The patient side never shows a code (a Hebrew sentence per failure); the staff side shows the Hebrew label beside the code. | The owner's two screens; one component, one behaviour. |
| D11 | The department's Hebrew label comes from a fixed map in the frontend mirroring the appointment-service catalog (Cardiology→קרדיולוגיה, …); an unknown department is shown as sent. | The service stores the English value; the label is presentation. |

## 3. Components

- **appointment-service** (`appointment-service`, own git, branch `feature/list-appointments`):
  `app/main.py` new route; `app/schemas.py` `AppointmentList`; tests in `tests/test_list_api.py`;
  README section. No schema change (no migration exists in that project).
- **hospital-agent backend:** `hospital_agent/appointment_list.py` (client, validation, errors);
  `api/app.py` wiring (`create_app(..., appointment_list=...)`); `api/routes_patient.py` and
  `api/routes_staff.py` routes; `api/schemas.py` response models; tests; `docs/api.md` §9;
  `docs/spec_corrections.md` row 89; CLAUDE.md.
- **hospital-agent frontend:** `api/types.ts` + `client.ts`; `components/AppointmentsPanel.tsx`
  (+ test); `pages/patient/MyRequests.tsx`; `pages/staff/ReviewCase.tsx`; labels; CSS (new selectors).

## 4. Error handling

Every failure of the external call becomes one code (D6); the panel keeps the last range and offers
retry. A range the user types badly (to before from) is refused client-side before any request, and
the server refuses it again (`422 invalid_range` / `400 validation_error`).

## 5. Testing

- appointment-service: range filter (inclusive from, exclusive to), both statuses, order, cap and
  `truncated`, auth 401, registry 404/503, validation 400 (naive, reversed, over 366 days), audit row.
- hospital-agent: client mapping for every answer shape (fake transport); both routes (identity from
  the token, another patient's case, staff-only, defaults, invalid range, not configured, each failure
  code); no `patient_id` in the log.
- frontend: panel default range, refetch on apply, empty, error + retry, not enabled, patient never
  shows a code, staff label beside code; both screens render the panel.
- End to end on an isolated stack is not possible without the real service; the owner's stack is
  checked after the merge by `/health` and one live read (a route call with a demo token is the
  owner's to run - no password is typed by the agent).

## 6. Out of scope

Booking, cancelling or editing an appointment from hospital-agent; the Case Monitor's expanded row
(the owner asked for the case screen); showing appointments to the LLM or using them in any decision.
