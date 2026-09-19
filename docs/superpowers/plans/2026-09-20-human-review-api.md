# Sub-project 5 (Human Review + write API) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let real people drive a case:
- a fixed-list IdP (§18.3);
- a Session Service for patients (submit, identify, upload);
- a Human Review Service for staff (queue, shown context, approve / resolve / reject → `WorkflowDecision` + the human event);
- an authenticated `/api` for both, which sub-project 6's React UI builds on.

**Architecture:** Three new domain modules sit between FastAPI routers and the existing State Manager, and they add no new state:
- `auth.py`, `session.py` and `human_review.py`;
- `scripted.py` delegates its patient actions to `SessionService`, so `obs.golden` and the tests exercise the same code.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy Core, pytest, Postgres 16, Docker Compose. No new dependencies: tokens use stdlib `hmac`/`hashlib`/`base64`.

**Spec:** `docs/superpowers/specs/2026-09-20-human-review-api-design.md` (autonomous mode, decisions §8). Binding spec: `docs/spec/` §1, §3, §3.1, §12.4–12.5, §13.2, §18.3, §18.4, D24. Read `CLAUDE.md` first, especially *Autonomous mode* and *Hand-off to sub-project 5*.

**Process note (design decision 9):** this plan specifies exact interfaces, behaviour and tests. It does not give full code as the previous plans did. Implementers write the code, TDD, following the existing style: terse docstrings that cite the spec, and SQLAlchemy Core through `repository` / `data_log`.

## Global Constraints

- Everything runs in Docker. In a worktree, use `docker compose -p <project> -f docker-compose.yml -f <isolation override> run --rm backend …`. Never `up`, `down` or `-v`.
- **Event ownership (§13.2):**
  - `REQUEST_SUBMITTED`, `DOCUMENT_UPLOADED` and the three human decisions come from `Component.EXTERNAL`.
  - `REQUEST_VALIDATED` and `PATIENT_VERIFICATION_FAILED` come from `Component.SESSION_SERVICE`.
  - Only `StateManager.apply()` writes State. `HUMAN_REVIEW_REQUIRED` is never emitted from here.
- **Identity comes only from the token.** `reviewer_id`, `reviewer_role` and the patient's `patient_id` come from the verified token and are never read from the request body (§18.3).
- **§12.3:**
  - The application log never contains `patient_id`, request text or secrets.
  - Patients never see escalation kinds, reasons or Audit.
  - Staff see Data Log content only through the review context.
- **Data Log discipline (see `CLAUDE.md` hand-off):**
  - Record `request_text` / `uploaded_document` *before* the event.
  - Put the upload's `content_hash` in the `DOCUMENT_UPLOADED` payload.
  - Tombstone an upload that did not move the case to Classifying.
- **Approvals:** `WorkflowDecision` rows follow §12.4–12.5 exactly. They are single-use, so the existing guards consume them.
- **Golden traces:** `python -m obs.golden` must keep printing `audit rows: 35`, `4`, `54`. The full suite must pass with 0 failures.
- **Never** read or print `.env` or the OpenAI key.
- **Line endings:** LF.
- **Commits:** every commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

### Task 1: The IdP (`auth.py`)

**Files:** Create `backend/hospital_agent/auth.py`. Test: `backend/tests/test_auth.py`.

**Interfaces (produce exactly):**
```python
PATIENT, CLINICAL_STAFF, ADMIN_STAFF = "patient", "clinical_staff", "admin_staff"
STAFF_ROLES = frozenset({CLINICAL_STAFF, ADMIN_STAFF})   # must equal guards.REVIEWER_ROLES

@dataclass(frozen=True)
class DemoUser:
    user_id: str; role: str; display_name: str; identity_verified: bool = True

DEMO_USERS: dict[str, DemoUser]   # exactly these five:
#   P-10041  patient         "דנה כהן"        verified
#   P-20000  patient         "יוסי לוי"       verified
#   P-30000  patient         "מיכל אברהם"     identity_verified=False  (PatientVerificationFailed path)
#   coordinator_nurse  clinical_staff  "אחות מתאמת"   (name matches policy/rules.pl)
#   admin_coordinator  admin_staff     "רכזת מנהלה"   (name matches policy/rules.pl)

@dataclass(frozen=True)
class Principal:
    user_id: str; role: str; display_name: str
    @property
    def is_staff(self) -> bool: ...
    @property
    def patient_id(self) -> str | None: ...   # user_id for a patient, None for staff

def auth_secret(env: Mapping[str, str] = os.environ) -> str        # AUTH_SECRET, else a fixed dev default
def demo_password(env: Mapping[str, str] = os.environ) -> str      # DEMO_PASSWORD, else "demo"
def authenticate(user_id: str, password: str, *, env=os.environ) -> DemoUser | None   # hmac.compare_digest
def issue_token(user: DemoUser, *, now: datetime, secret: str, ttl: timedelta = timedelta(hours=8)) -> str
def verify_token(token: str, *, now: datetime, secret: str) -> Principal | None
```

**Token format:** `base64url(json.dumps({"sub","role","exp"}, sort_keys=True, separators=(",",":"))) + "." + base64url(hmac_sha256(secret, that_first_part))`, with no padding and `exp` as integer epoch seconds.

`verify_token` returns `None` for each of these, and never raises:
- a malformed token or bad base64;
- a wrong signature (checked with `hmac.compare_digest`);
- `exp <= now`;
- an unknown `sub`;
- a role that differs from `DEMO_USERS[sub].role`.

- [ ] **Step 1:** Write `tests/test_auth.py`, covering:
  - the five users and their roles;
  - `STAFF_ROLES == guards.REVIEWER_ROLES`;
  - `authenticate`:
    - right password → user;
    - wrong password or unknown user → `None`;
    - `DEMO_PASSWORD` override via the `env` argument;
  - `issue_token` → `verify_token` round-trip gives `Principal`;
  - `patient_id` / `is_staff`;
  - `verify_token` returns `None` for:
    - an expired token;
    - a tampered payload (a different `sub` re-encoded with the old signature);
    - a wrong secret;
    - garbage strings (`""`, `"abc"`, `"a.b.c"`);
    - an unknown user;
  - `auth_secret` default vs override.
- [ ] **Step 2:** Run it and see it fail (`ModuleNotFoundError`).
- [ ] **Step 3:** Implement `auth.py`, with a module docstring citing §18.3 and design §3.
- [ ] **Step 4:** Run the test, then the whole suite. It must have 0 failures.
- [ ] **Step 5:** Commit: `Add the demo IdP: fixed users and HMAC tokens`.

---

### Task 2: Session Service and Human Review Service

**Files:**
- Create `backend/hospital_agent/session.py` and `backend/hospital_agent/human_review.py`.
- Modify `backend/hospital_agent/scripted.py`: `submit`, `validate`, `verification_failed` and `upload` delegate to `SessionService`. Keep their current signatures and behaviour; `approval` / `human` stay as they are.
- Tests: `backend/tests/test_session.py` and `backend/tests/test_human_review.py`.

**Consumes:** `StateManager`, `repository`, `data_log`, `naming`, `case`, `EscalationKind`. This task does NOT depend on `auth.py`, so it runs in parallel with Task 1. Reviewer identity arrives as plain `reviewer_id: str, reviewer_role: str`.

**`session.py` interfaces:**
```python
MISSING_DOCUMENT_TEMPLATE_ID = "missing-document-v1"          # D24

class CaseNotFound(Exception): ...        # unknown case, or not this patient's (the API maps both to 404)
class EventRejected(Exception):
    def __init__(self, reason: str): self.reason = reason   # a blocked event (API: 409 {"detail": reason})

@dataclass(frozen=True)
class PatientView:
    case_id: str; status: str; created_at: datetime; updated_at: datetime
    request_text: str | None                   # the latest non-tombstoned request_text
    missing_document_ids: list[str]            # required - held, sorted; [] unless status == "needs_document"
    missing_document_request_template_id: str | None   # MISSING_DOCUMENT_TEMPLATE_ID iff needs_document
    message: str | None                        # the latest delivered outgoing_message iff status == "completed"

def patient_status(case: CaseRecord, delivered: bool) -> str
#   Received -> "received"; AwaitingPatientInput -> "needs_document"; AwaitingHumanReview -> "in_review";
#   Completed -> "completed" if delivered else "closed"; Failed -> "closed"; every other State -> "in_progress".
#   delivered = the case has a committed CASE_RESOLVED Transition row (audit).

class SessionService:
    def __init__(self, state_manager: StateManager, *, wake: Callable[[], None] = lambda: None) -> None
    def open_case(self, patient_id: str) -> TransitionResult                        # REQUEST_SUBMITTED, EXTERNAL
    def validate_request(self, case_id: str, text: str, *, identity_verified: bool = True) -> TransitionResult
        # records request_text in the Data Log FIRST, then REQUEST_VALIDATED (SESSION_SERVICE)
    def verification_failed(self, case_id: str) -> TransitionResult                 # PATIENT_VERIFICATION_FAILED
    def submit_request(self, patient_id: str, text: str, *, identity_verified: bool) -> str
        # open_case; if identity_verified: validate_request, else: record request_text then verification_failed;
        # raises EventRejected(reason) if a step is blocked; calls wake(); returns case_id
    def revalidate(self, case_id: str) -> TransitionResult
        # after a PatientVerificationFailed approval: REQUEST_VALIDATED from the stored request_text
        # (identity_verified=True, NOT a new Data Log row); EventRejected if no request text is left
    def upload_document(self, patient_id: str, case_id: str, document_id: str, content: str,
                        fmt: str = "pdf") -> TransitionResult
        # case must belong to patient_id else CaseNotFound; record uploaded_document; DOCUMENT_UPLOADED
        # (EXTERNAL) with {"document": {document_id, format, patient_id}, "content_hash": entry.content_hash};
        # tombstone the entry unless the result committed with state_after == Classifying; wake(); return result
        # (a rejected upload returns the result - the API reports the unchanged view - it does NOT raise)
    def case_for_patient(self, patient_id: str, case_id: str) -> CaseRecord          # CaseNotFound otherwise
    def patient_view(self, case_id: str) -> PatientView
    def cases_of(self, patient_id: str) -> list[PatientView]                         # newest first
```
`scripted.ScriptedAgents` must keep producing exactly the same events and Data Log rows as today. `obs.golden` and `test_scenarios.py` must be unchanged and still show 35/4/54. It builds one `SessionService(sm)` and delegates:
- `submit` → `open_case`;
- `validate(text, identity_verified)` → `validate_request`;
- `verification_failed` → `verification_failed`;
- `upload(document_id, content, **document)` → an equivalent path. The extra `**document` keys (e.g. a foreign `patient_id` in D25 tests) must still reach the payload, so add an optional `document_extra: Mapping | None = None` parameter to `upload_document` that is merged into the document dict after the defaults.

**`human_review.py` interfaces:**
```python
RESUMABLE: dict[EscalationKind, tuple[str, ...]] = {        # §3 HUMAN_APPROVED rows + their required fields
    PATIENT_VERIFICATION_FAILED: ("verified_identity_ref",), RETRY_EXHAUSTED: (), POLICY_REVIEW: (),
    Z3_COUNTEREXAMPLE: ("patient_deadline",), PATIENT_SLA_EXPIRED: ("patient_deadline",)}
DECISION_EVENT = {"approve": Event.HUMAN_APPROVED, "resolve": Event.HUMAN_RESOLVED_CASE,
                  "reject": Event.HUMAN_REJECTED}

class NotInReview(Exception): ...                 # case not in AwaitingHumanReview (API 409 "not_in_review")
class ContextChanged(Exception): ...              # shown_context_ref mismatch (API 409 "context_changed")
class DecisionRejected(Exception):                # event blocked / invalid input (API 409 {"detail": reason})
    def __init__(self, reason: str): self.reason = reason

@dataclass(frozen=True)
class ReviewItem:
    case_id: str; patient_id: str; escalation_kind: str; escalated_from_state: str | None
    reasons: list[str]; allowed_decisions: list[str]; required_fields: list[str]; updated_at: datetime

@dataclass(frozen=True)
class ReviewContext:
    case_id: str; patient_id: str; state: str; escalation_kind: str | None; escalated_from_state: str | None
    reasons: list[str]
    data: list[dict]      # non-tombstoned Data Log entries: entry_id, kind, content, content_hash, created_at
                          # (uploaded_document only if accepted - same rule as the Orchestrator's _accepted_uploads)
    trace: list[dict]     # audit rows: audit_id, record_type, event, state_before, state_after, action,
                          # policy_result, policy_reasons, recorded_at
    shown_context_ref: str   # "ctx-" + sha256 of the canonical JSON of every field above

class HumanReviewService:
    def __init__(self, state_manager: StateManager, session: SessionService, *,
                 wake: Callable[[], None] = lambda: None, approval_ttl: timedelta = timedelta(hours=1)) -> None
    def queue(self) -> list[ReviewItem]                      # AwaitingHumanReview cases, oldest update first
    def context(self, case_id: str) -> ReviewContext         # CaseNotFound for an unknown case
    def decide(self, *, reviewer_id: str, reviewer_role: str, case_id: str, decision: str, reason: str,
               shown_context_ref: str, verified_identity_ref: str | None = None,
               patient_deadline: datetime | None = None) -> TransitionResult
    def tombstone(self, case_id: str, entry_id: str) -> bool  # False if the entry is not this case's / already gone
```

**Rules for `decide`, in order:**
1. **The case:** unknown → `CaseNotFound`; not in AwaitingHumanReview → `NotInReview`.
2. **The input:**
   - `decision` must be one of `approve`, `resolve`, `reject`, else `DecisionRejected("invalid_decision")`;
   - an empty `reason` → `DecisionRejected("reason_required")`;
   - `approve` for a kind not in `RESUMABLE` → `DecisionRejected("decision_not_allowed")`;
   - a missing required field → `DecisionRejected("<field>_required")`.
3. **The context:** `shown_context_ref != context(case_id).shown_context_ref` → `ContextChanged`.
4. **The approval:** insert the `WorkflowDecision` through `repository.insert_approval`:
   - `approval_id = "APPR-" + uuid hex[:12]`;
   - `reviewer_id` and `reviewer_role` from the arguments;
   - `granted_at = sm.clock()`, `valid_until = granted_at + approval_ttl`;
   - `escalation_kind` from the case, plus `shown_context_ref`;
   - for POLICY_REVIEW also `plan_hash` and `current_step` from the case;
   - `verified_identity_ref` and `patient_deadline` as given.
5. **The event:** `sm.apply(case_id, DECISION_EVENT[decision], {"approval_id": ...}, Component.EXTERNAL)`. If it is not committed → `DecisionRejected(result.reason)`.
6. **Identity re-check:** if the decision was approve on `PATIENT_VERIFICATION_FAILED` and the case is now in Received → `session.revalidate(case_id)`.
7. **Finish:** call `wake()` and return the event's result.

The shown context must never include a tombstoned entry's content.

- [ ] **Step 1: Tests.** Write `tests/test_session.py` and `tests/test_human_review.py`. They use `app_engine`, the `sm` fixture, and `ScriptedAgents` or `Driver` to reach states. Cover:
  - **Submitting:** `submit_request` for a verified patient ends in Classifying; the Data Log holds the request text; `wake` was called once.
  - **The unverified patient:** submitting for `identity_verified=False` ends in AwaitingHumanReview / PatientVerificationFailed, and the request text is still recorded.
  - **`patient_status`:** one parametrized case per State; Completed is `completed` only with a CASE_RESOLVED row.
  - **`patient_view` for needs_document:** gives the sorted `missing_document_ids` and the template id (D24).
  - **`patient_view` for completed:** gives the message. Use `Orchestrator` + `FakeProvider` to complete scenario 1.
  - **Uploads:**
    - an upload to another patient's case → `CaseNotFound`;
    - a rejected upload (foreign `patient_id` in `document_extra`) is tombstoned and the state is unchanged;
    - an accepted upload is not tombstoned and carries `content_hash` on the audit row.
  - **`queue()`:**
    - it lists escalations with `allowed_decisions`;
    - MedicalQuestion → `["resolve","reject"]`;
    - RetryExhausted → `["approve","resolve","reject"]`;
    - `required_fields` for Z3Counterexample is `["patient_deadline"]`.
  - **`context()`:**
    - it is deterministic (same ref twice);
    - the ref changes after a new audit row;
    - it excludes tombstoned content and rejected uploads.
  - **`decide()`:**
    - resolve of a MedicalQuestion → Completed, with approval `consumed_at` set;
    - approve of RetryExhausted → Planning, `retry_cycle` 1;
    - approve of PatientVerificationFailed with `verified_identity_ref` → the case re-validates to Classifying automatically;
    - approve without the required field → `DecisionRejected("verified_identity_ref_required")`;
    - approve of MedicalQuestion → `DecisionRejected("decision_not_allowed")`;
    - a stale ref → `ContextChanged`;
    - a case not in review → `NotInReview`;
    - `reviewer_role` is stored as given, and an invalid role (e.g. `"patient"`) is blocked by the guard → `DecisionRejected`.
  - **`tombstone()`:** true once, then false.
  - **Golden:** `scripted` still yields 35/4/54. `test_scenarios.py` covers this; run it.
- [ ] **Step 2:** Run the tests and see them fail.
- [ ] **Step 3:** Implement `session.py`, `human_review.py` and the `scripted.py` delegation.
- [ ] **Step 4:** Run the new tests, `test_scenarios.py`, `test_d_tests.py`, `test_orchestrator.py`, the whole suite and `python -m obs.golden`.
- [ ] **Step 5:** Commit: `Add the Session Service and the Human Review Service`.

---

### Task 3: The `/api` routes, app wiring, API contract and docs

**Files:**
- Create:
  - `backend/hospital_agent/api/deps.py`;
  - `api/routes_auth.py`, `api/routes_patient.py` and `api/routes_staff.py`;
  - `docs/api.md`.
- Modify:
  - `backend/hospital_agent/api/app.py`;
  - `backend/hospital_agent/api/schemas.py`;
  - `backend/tests/test_api.py`: the monitor tests move to `/api/staff/...` with a staff token;
  - `backend/tests/test_app_orchestrator.py` if needed;
  - `CLAUDE.md`;
  - `docs/spec_corrections.md`.
- Tests: `backend/tests/test_api_patient.py`, `backend/tests/test_api_staff.py` and `backend/tests/test_api_e2e.py`.

**Consumes:** Tasks 1 and 2 exactly as specified above.

**App:**
- The signature becomes `create_app(engine: Engine | None = None, orchestrator: Orchestrator | None = None)`.
- The lifespan builds, in this order:
  - the `sm`;
  - `app.state.session = SessionService(sm, wake=_wake)`;
  - `app.state.reviews = HumanReviewService(sm, app.state.session, wake=_wake)`.
- `_wake` calls `app.state.orchestrator.wake()` when there is one.
- An injected orchestrator is stored as-is and never started. Tests step it themselves.
- The server path (owned engine) is unchanged: it still starts recovery, the SLA Worker and the orchestrator when there is a key.
- **CORS:** `CORSMiddleware` for `http://localhost:5173`, `http://127.0.0.1:5173` and any comma-separated `CORS_ORIGINS`; allow the `Authorization` header.
- **Remove the unauthenticated `/cases*` routes.** Their responses move unchanged under `/api/staff/cases*`.
- `/health` stays public and unchanged.

**Auth dependency (`deps.py`):**
- `current_principal` reads `Authorization: Bearer <token>` and verifies it with `auth.verify_token(now=datetime.now(UTC), secret=auth_secret())`.
- On failure it raises `HTTPException(401, "not_authenticated")`.
- `require_patient` raises `403 "patients_only"` for a non-patient; `require_staff` raises `403 "staff_only"` for a non-staff user.

**Routes:** as in design §6, with these request and response models:
- `POST /api/auth/login`:
  - body `{user_id, password}`;
  - 200 `{token, user_id, role, display_name}`;
  - 401 `invalid_credentials`.
- `GET /api/auth/me`: `{user_id, role, display_name}`.
- **Patient routes:**
  - `POST /api/patient/requests`:
    - body `{text}`, with `text` stripped, 1–2000 characters, else 422;
    - response 201 with the `PatientView` JSON.
  - `GET /api/patient/requests`: `[PatientView]`.
  - `GET /api/patient/requests/{case_id}`: `PatientView`, or 404 `case_not_found`.
  - `POST /api/patient/requests/{case_id}/documents`:
    - body `{document_id, format, content}`: `document_id` 1–64 characters of `[A-Za-z0-9_-]`, `format` in `{"pdf","jpg","png"}`, `content` 1–20000 characters;
    - response 200 with the `PatientView`, even for a rejected upload.
- **Staff routes:**
  - `/api/staff/cases` routes: the same models as the old `/cases` routes.
  - `GET /api/staff/reviews`: `[ReviewItem]`.
  - `GET /api/staff/cases/{case_id}/context`: `ReviewContext`.
  - `POST /api/staff/cases/{case_id}/decision`:
    - body `{decision, reason, shown_context_ref, verified_identity_ref?, patient_deadline?}`, where `patient_deadline` is an ISO datetime with a timezone;
    - 200 `{case_id, state}`;
    - 404 / 409 as in design §6.
  - `DELETE /api/staff/cases/{case_id}/data/{entry_id}`: 204, or 404 `entry_not_found`.
- **Error body:** always FastAPI's default `{"detail": "<code>"}`.

**Tests:**
- **`test_api_patient.py`:**
  - login success and failure;
  - `/me`;
  - 401 without a token, and with an expired or tampered one;
  - a patient creates a request and lists it;
  - a patient cannot read another patient's case (404);
  - a patient gets 403 on `/api/staff/...`;
  - staff get 403 on `/api/patient/...`;
  - a request with empty or oversized text → 422;
  - a document with a bad format → 422.
- **`test_api_staff.py`:**
  - the monitor list, detail and audit under staff auth;
  - the old `/cases` path → 404;
  - the reviews queue shape;
  - the context and decision round-trip;
  - a stale `shown_context_ref` → 409 `context_changed`;
  - approve on a MedicalQuestion → 409 `decision_not_allowed`;
  - a decision on a case not in review → 409 `not_in_review`;
  - tombstone: 204, then 404.
- **`test_api_e2e.py`:** build `create_app(app_engine, orchestrator=Orchestrator(sm, FakeProvider(), gateway))`. After each patient or staff call, the test calls `orchestrator.run_case(case_id)`.
  - Scenario 1: submit → `needs_document` with `["blood_test"]` and the template id; upload → `completed`, and the message contains `INSTR-PREP-COLONOSCOPY`.
  - Scenario 2: the medical request goes into the queue as MedicalQuestion; the staff resolve it; the patient sees `closed`.
  - Scenario 3: with `MockGateway(failures={"CheckDocuments": 3})`, the case goes into the queue as RetryExhausted; the staff approve; then upload; → `completed`.
  - `P-30000`: `in_review` → the staff approve with `verified_identity_ref` → the case moves on to `needs_document` after a `run_case`.

**Docs:**
- **`docs/api.md`:** the full contract, with each route, auth, request and response JSON examples and error codes, plus the demo users and password. Sub-project 6's implementer reads only this file to build the UI.
- **`CLAUDE.md`:**
  - replace "Hand-off to sub-project 5" with "Hand-off to sub-project 6 (React UI)", pointing to `docs/api.md`, the CORS origins, the demo users and the design files in `design/ramon-ui/`;
  - update *Project status* to sub-projects 1–5;
  - under *Working in the backend*, add a bullet for `auth.py` / `session.py` / `human_review.py`;
  - in *Commands*, note that `AUTH_SECRET` and `DEMO_PASSWORD` can be set in `.env`.
- **`docs/spec_corrections.md`:** append rows 39–46 for design decisions 1–8. Each row has 4 cells: question | decision | where. Use the next free row numbers.

- [ ] **Step 1:** Write the three test files and update `test_api.py`. Run them and see them fail.
- [ ] **Step 2:** Implement `deps.py`, the routers, the schemas and the app changes.
- [ ] **Step 3:** Run the new tests, the whole suite and `python -m obs.golden`.
- [ ] **Step 4:** Write `docs/api.md`, and make the `CLAUDE.md` and `spec_corrections.md` updates.
- [ ] **Step 5:** Commit: `Add the authenticated /api for patients and staff; API contract`.

---

## Done when
- The full suite passes and `python -m obs.golden` prints 35 / 4 / 54.
- `docs/api.md` describes every route, and `CLAUDE.md` hands off to sub-project 6.
