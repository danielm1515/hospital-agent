# Sub-project 18: exam types and preparation instructions, per appointment

Status: design, autonomous mode (CLAUDE.md), owner's approval of 2026-09-26 ("מאשר, צא לדרך"), with the
owner's follow-up the same day: a patient with more than one appointment must be answered about the one they
mean.

## 1. The gaps

- **Instructions are a fixed mock.** `LoadInstructions` goes AppointmentServiceGateway → DocumentServiceGateway
  → `MockGateway`, which returns `INSTR-PREP-COLONOSCOPY v3` for every case. A Cardiology appointment on 07/10
  got colonoscopy preparation - misleading, and the worst of these gaps.
- **"באזור האישי" does not exist.** The status template (`llm/message.py`) says the instructions are available
  in the personal area; no patient screen shows them. The text sits only in the Data Log (staff-visible).
- **The appointment-service has no exam type.** Only departments; preparation depends on the exam (echo vs
  stress test vs Holter), not the department.
- **One appointment only.** `CheckAppointment` returns the earliest Scheduled appointment. A patient with two
  appointments asking about the later one is answered about the earlier one, and the message does not say which.
- **Nothing ties the text to the approval.** OPA approves `INSTRUCTION_SOURCE`; nothing checks that the text the
  instruction system returned is that source.

## 2. Binding constraints (spec, read before deciding)

- §5: the action list is closed; the fixed plan stays `CheckAppointment → CheckDocuments → LoadInstructions →
  SendStatusUpdate`. `LoadInstructions` stays its own step; only its backing system changes.
- §3.1 ApprovedSource / §8 / §9.2 P7 / §16 D17: the source is `source_id` + `version`, approved only by the
  registry packed into the OPA bundle (`policy/data/approved_instruction_sources.json`); the Planner/LLM never
  supplies it. The appointment-service does **not** approve anything.
- §11: `instruction_system` receives **no** patient fields; `appointment_system` may receive `patient_id` and
  `appointment_id` (`minimized(appointment_id, appointment_system)` is in the spec).
- §12.3: the Data Log keeps the instructions that were shown.
- §0 / golden traces: the three scenarios stay on `MockGateway` and must still print 35 / 4 / 54.

## 3. Decisions

| # | Decision |
|---|---|
| D1 | **Exam-type catalog in the appointment-service** (`app/catalog.py` `EXAM_TYPES`): every exam belongs to one department and carries a Hebrew label, suggested required documents, and one preparation instruction (`source_id` `INSTR-<CODE>`, `version` `"1"`, Hebrew title and text). Every department has a default `…_VISIT` (a clinic visit). Stored in two **new** tables (`exam_types`, upserted on every start like `document_types`; `appointment_exam_types`, one row per appointment) - `create_all` adds new tables to an existing SQLite database, never a column. |
| D2 | **Booking UI:** an exam-type select filtered by department (like doctors), required; choosing it pre-ticks its suggested documents (client-side; the admin can still change them; the server validates the exam belongs to the department). The dashboard shows the exam type. An appointment with no stored exam type (booked before this change) resolves to its department's `…_VISIT`. The two seed appointments are backfilled once if they exist without one: APT-8391 → `NEURO_VISIT`, APT-8392 → `CARD_STRESS`. |
| D3 | **Appointment-service API:** `AppointmentOut` gains `exam_type {code, label}` and `instruction {source_id, version, title}` (the text is not in the appointment answer). `CheckAppointment` takes an optional `?appointment_id=`: that appointment, only if it is the patient's (otherwise `found=false`, never another patient's) - and its answer adds `upcoming_count` (Scheduled, future). A new `GET /api/v1/instructions/{source_id}?version=` returns `{source_id, version, title, text}` (404 `instruction_not_found`); same API key, audit row, no patient data. |
| D4 | **The texts are demo drafts.** Written from common public practice, each ends with "טיוטת דמו – טעונה אישור רפואי. בכל שאלה רפואית יש לפנות לצוות המטפל." In a real hospital a clinician approves the wording and the version. |
| D5 | **The patient picks the appointment.** "פנייה חדשה" offers the patient's upcoming Scheduled appointments (the sub-project 16 list, next 90 days); one appointment is pre-selected; "התור הקרוב ביותר" (no id) is offered only when the patient has at most one upcoming appointment - with more, choosing is mandatory. Before sending, a client-side check compares the text with the patient's other appointments (a date written as d/m or d.m, a department or exam label); if it names another appointment than the chosen one, an inline notice offers to switch ("נראה שכתבת על … - לעבור לתור הזה?"), and the patient may switch or send as is. Deterministic string matching, no LLM, no server block (a new escalation kind would break the closed list); staff keep sub-project 15's "האם התכוונת ל…" clarification. `POST /api/patient/requests` takes an optional `appointment_id`; it rides on `REQUEST_SUBMITTED` and is stored on the case. The LLM never picks it. |
| D6 | **CheckAppointment reads that appointment** (`appointment_id` from the case - allowed by §11) and returns, besides `appointment_at` / `required_documents`, `appointment_id`, `department`, `exam_type_label`, `instruction_source` and `upcoming_count`, all stored on the case (migration 0007, nullable columns; `RECORD_RETRIEVAL` stores them). A chosen appointment that is not the patient's, cancelled or past is `not_found` - the existing error path, never a substitute appointment. |
| D7 | **The approved source comes from the case.** The Orchestrator passes the case's `instruction_source` to the policy (not the Planner, not a constant). A case without one (the mock path returns the demo colonoscopy source, so the golden traces are unchanged) is denied `unapproved_instruction_source` by OPA - fail closed. |
| D8 | **LoadInstructions reads the instruction system = the appointment-service's instruction endpoint** with only `source_id` + `version` (no patient field, §11). The gateway checks the answer is exactly that source and version (`invalid_response` otherwise) and returns `instruction_ids` + `instruction_text` (title + text) → Data Log + the Safety re-check, as today. Configured by the same `APPOINTMENT_SERVICE_URL`/`KEY`; without them the mock stays. |
| D9 | **The registry lists every catalog instruction** (`approved_instruction_sources.json`: each `INSTR-…` v1, valid 2026-01-01 to 2030-01-01; the three existing entries stay). An instruction the appointment-service adds later but the registry does not list is denied - the escalation path, never delivered. |
| D10 | **The message names the appointment and points to the case:** "התור שלך ל{exam} ({department}) נקבע ל־{date} בשעה {time} (שעון ישראל). … הוראות ההכנה המאושרות ({source_id}, גרסה {version}) מופיעות בפנייה זו." Without an exam type (mock): "התור שלך נקבע ל־…" as before, with "מופיעות בפנייה זו". When no appointment was chosen and `upcoming_count > 1`: "יש לך תורים נוספים - אפשר לפתוח פנייה על תור מסוים." When an appointment was chosen and `upcoming_count > 1`: "אם התכוונת לתור אחר, אפשר לפתוח פנייה חדשה ולבחור אותו." A wording change, recorded like row 74; the D33 labelled set's template messages are updated to the new wording. |
| D11 | **The patient sees the instructions** on the request screen under the delivered message (`completed`): the case's own Data Log `instructions` entry (present, not tombstoned) - exactly what was approved and shown (§12.3). |
| D12 | **The appointments panel** shows each appointment's exam type and instruction title, with "הצגת הוראות ההכנה" loading the text through `GET /api/patient/instructions/{source_id}?version=` (staff: `/api/staff/instructions/...`), which answers **only** a source the registry approves and is currently valid (`404 instruction_not_approved` otherwise) - the panel can never show unapproved text. |
| D13 | **Staff** see the chosen appointment, its exam type and the instruction source on the case (Case Monitor row detail / review context). |
| D14 | Recorded in `docs/spec_corrections.md` rows 90-93: the instruction system is backed by the appointment-service (D8); the source comes from the case, fixed by the appointment's exam type (D7); `REQUEST_SUBMITTED` carries an optional patient-chosen `appointment_id` and `CheckAppointment` sends it (§11 allows it) (D5-D6); the template wording (D10). |

## 4. The catalog (demo drafts, D4)

| Department | Code | Exam | Preparation (summary; the full text lives in the catalog) |
|---|---|---|---|
| Cardiology | CARD_VISIT | ביקור במרפאה קרדיולוגית | רשימת תרופות, סיכומים ובדיקות קודמות (אק"ג, אקו); להגיע 15 דקות לפני |
| | CARD_ECHO | אקו לב | ללא הכנה מיוחדת; אוכלים, שותים ונוטלים תרופות כרגיל; לבוש נוח |
| | CARD_STRESS | מבחן מאמץ | צום 3 שעות (שתייה מותרת), בלי קפאין 12 שעות, בגדים ונעלי ספורט; חוסמי בטא רק לפי הנחיית הרופא המפנה |
| | CARD_HOLTER | הולטר לב | להתקלח לפני (אסור להרטיב 24 שעות), חולצה מכופתרת, יומן תסמינים, החזרת המכשיר למחרת |
| Dermatology | DERM_VISIT | ביקור במרפאת עור | רשימת תרופות ומשחות; צילום הנגע אם השתנה |
| | DERM_MOLES | מיפוי שומות | עור נקי בלי קרמים, איפור ולק; בלי שיזוף שבועיים לפני; להביא מיפוי קודם |
| Neurology | NEURO_VISIT | ביקור במרפאה נוירולוגית | רשימת תרופות, הדמיות קודמות, יומן התקפים או תסמינים |
| | NEURO_EEG | EEG | לחפוף שיער ערב לפני, בלי ג'ל או שמן; לאכול כרגיל; תרופות כרגיל אלא אם נאמר אחרת |
| | NEURO_EMG | EMG | בלי קרמים על העור; בגדים רחבים; לדווח על מדללי דם או קוצב |
| Ophthalmology | OPHTH_VISIT | בדיקת עיניים | משקפיים ועדשות; עדשות מגע - לפי הנחיית המרפאה |
| | OPHTH_DILATED | בדיקה עם הרחבת אישונים | ראייה מטושטשת 4-6 שעות: לא לנהוג, להגיע עם מלווה, משקפי שמש |
| Orthopedics | ORTHO_VISIT | ביקור במרפאה אורתופדית | צילומים והדמיות קודמים; בגדים שמאפשרים חשיפת המפרק |
| | ORTHO_INJECTION | זריקה למפרק | לדווח על מדללי דם; לאכול כרגיל; להגיע עם מלווה אם נאמר |

## 5. Out of scope

A clinician-facing approval workflow for instruction texts (the registry stays the static OPA bundle, §3.1);
per-doctor instructions; reminders or notifications; changing the three §0 scenarios.
