# תת־פרויקט 1: Core — מסמך Design

**תאריך:** 2026-09-19
**סטטוס:** ממתין לאישור
**מקור אמת:** `Hospital_Agent_Clean.docx`, בהמרה ל־`docs/spec/` (סעיף N במפרט = `docs/spec/NN-*.md`)

## 1. הקשר

המערכת בנויה משבעה תת־פרויקטים, לפי סדר "ליבה קודם". כל אחד מהם עובר תכנון, תוכנית מימוש ומימוש משלו:

| # | תת־פרויקט | תוכן |
|---|---|---|
| **1** | **Core** | **המסמך הזה** |
| 2 | Policy | OPA (Rego + evaluator תואם בפייתון), מנועי Prolog ו־Datalog בפייתון, Z3 (סעיפים 9.1 ו־9.2), Temporal Monitor (T1–T12) |
| 3 | Execution | Tool Executor, Retry Manager, ה־outbox, ExecutorReverified, מערכות חיצוניות מדומות, SLA Worker, התאוששות אחרי restart |
| 4 | LLM | Model Selector + OpenAI GPT-5.6 Luna, ה־Classifiers, Planner, Response Evaluator, ‏`obs.golden` |
| 5 | Human Review + API | approvals, IdP מדומה, endpoints למטופל ולצוות |
| 6 | React UI | מסך מטופל, מסך צוות, Case Monitor |
| 7 | D33 | סט הערכה ודוח recall |

**החלטות שכבר התקבלו:**
- Backend: Python 3.13 ו־FastAPI, באפליקציה אחת עם מודול לכל רכיב במפרט.
- Frontend: React.
- DB: PostgreSQL ב־Docker, בפורט **54322** במחשב המארח.
- הכל רץ ב־Docker Compose.
- מנועי Prolog ו־Datalog כתובים בפייתון וקוראים את קבצי ה־`.pl` וה־`.dl` כמו שהם.
- OPA: קובץ Rego אמיתי, ולצידו evaluator תואם בפייתון, עם בדיקה שהשניים מחזירים אותה תוצאה. הקובץ `opa` 1.9.0 מותקן בקונטיינר, כך שהבדיקה תמיד רצה.
- Z3: הספרייה `z3-solver==4.15.4`.
- ממשיכים את הדפוסים של הפרויקט הקודם `AI_Hospital`:
  - רשימות סגורות ב־`naming.py`;
  - טבלת מעברים כנתונים;
  - סדר הפעולות ב־`apply`: מציאת השורה, בדיקת Temporal Monitor, ואז commit;
  - מסמך `docs/spec_corrections.md`.

## 2. היקף ה־Core

**בתוך ההיקף:**
- שלד הריפו, Docker Compose ו־migrations;
- מילון המונחים;
- טבלת המעברים וה־Guards;
- State Manager מעל Postgres;
- Escalation Coordinator;
- API לקריאה בלבד עבור ה־Case Monitor;
- בדיקות.

**מחוץ להיקף:** כל רכיב שמפיק אירועים בפועל (Classifier, Planner, Policy, Tool Executor, SLA Worker, Human Review), וה־UI. ב־Core, הרכיבים שמעריכים Guards או בודקים trace מוגדרים כממשקים (ports) בלבד. המימושים שלהם מגיעים בתת־הפרויקטים הבאים.

## 3. עיקרון מנחה: ה־State נשמר רק ב־DB

- **שורת `cases` היא המקור היחיד למצב הנוכחי של פנייה.** השרת לא מחזיק State בזיכרון בין בקשות. כל בקשה וכל אירוע קוראים את השורה מה־DB, ומעבר נחשב שקרה רק אחרי commit.
- **restart לא דורש שחזור.** הבקשה הבאה קוראת את השורה ומקבלת את המצב העדכני, וה־Case Monitor מציג אותו.
- **`audit_log` הוא ההיסטוריה המלאה** של כל פנייה: כל מעבר, כל חסימה וכל החלטה. זה גם ה־trace שה־Temporal Monitor בודק (סעיף 7 במפרט).
- **טיפול נוסף אחרי restart שייך לתת־פרויקט 3:** ניסיון ביצוע שלא הסתיים מסלים כ־`ExecutionUnknown` בלי replay, וה־SLA Worker סורק דד־ליינים מה־DB.

## 4. מבנה הריפו

```
HospitalAgent/
  docker-compose.yml       db: postgres:16, ports "54322:5432"; backend: build ./backend
  .env.example             סיסמאות ו־DATABASE_URL (ה־.env עצמו לא נכנס ל־git)
  backend/
    Dockerfile             python:3.13-slim + uv (את opa מוסיפים בתת־פרויקט 2)
    pyproject.toml         fastapi, uvicorn, sqlalchemy, psycopg[binary], alembic, pytest
    alembic/               migrations: 4 טבלאות, אינדקסים, roles
    hospital_agent/
      naming.py            רשימות סגורות
      case.py              CaseRecord: שורת cases כ־dataclass
      fsm.py               41 שורות המעבר
      guards.py            ה־Guards של סעיף 3.1, וה־ports של המעריכים החיצוניים
      state_manager.py     apply(): טרנזקציה, Audit, נעילה אופטימית
      escalation.py        Escalation Coordinator
      db.py                הגדרת הטבלאות (SQLAlchemy Core, בלי ORM) ו־engine
      api/app.py           FastAPI: /health ו־API לקריאה בלבד
    tests/
  frontend/                (תת־פרויקט 6)
  docs/spec/  docs/spec_corrections.md  docs/superpowers/specs/
```

- **גישה ל־DB:** ‏SQLAlchemy Core (בלי ORM) עם psycopg 3, סינכרוני. המפרט דורש SQL מפורש: `UPDATE ... WHERE state_version=?`, רשומות Audit שרק מוסיפים, וטרנזקציה אחת לכל מעבר. גם Z3 והמנועים סינכרוניים.
- **חיבור ל־DB:** ה־backend מתחבר ל־`db:5432` בתוך רשת ה־compose. מהמחשב המארח (psql, DBeaver) מתחברים ל־`localhost:54322`.

## 5. מילון המונחים (`naming.py`)

כל הרשימות הן `Enum` סגורים. שם שאינו ברשימה זורק שגיאה, וגם כתיב ישן נדחה בשגיאה (`DEPRECATED_EVENT_ALIASES`, כמו בפרויקט הקודם).

| רשימה | תוכן | מקור במפרט |
|---|---|---|
| `State` | 12 מצבים; `TERMINAL_STATES = {Completed, Failed}` | §2.1 |
| `Event` | 26 אירועים | §2.2 |
| `EXTERNAL_EVENTS` | ‏`REQUEST_SUBMITTED`, ‏`DOCUMENT_UPLOADED`, ‏`HUMAN_APPROVED`, ‏`HUMAN_REJECTED`, ‏`HUMAN_RESOLVED_CASE` | §13.2 |
| `EVENT_OWNER` | ל־21 האירועים הפנימיים: איזה `Component` מפיק כל אירוע | §13.2 |
| `NON_TRANSITION_EVENTS` | ‏`TOOL_EXECUTION_STARTED`, ‏`AUDIT_RECORDED`: נרשמים ב־trace בלי לשנות State | §2.2, §3 |
| `Action` | 6 פעולות, ו־`to_prolog()` / `to_pascal()` כמתאם יחיד בין שתי מוסכמות השמות | §5 |
| `EscalationKind` | 14 סוגים: ‏PatientVerificationFailed, MedicalQuestion, SafetyEscalation, ClassificationFailed, TemporalViolation, PlanningFailed, PolicyDenied, PolicyReview, RetryExhausted, NonIdempotentFailure, ExecutionUnknown, Z3Counterexample, PatientSlaExpired, DeliveryStepMissing | §3 |
| `RESUMABLE` | הסוגים ש־HUMAN_APPROVED יכול לחדש, ולכל אחד השדה שהאישור חייב לכלול: PatientVerificationFailed → `verified_identity_ref`; ‏RetryExhausted → אין שדה נוסף; PolicyReview → `plan_hash` + `current_step`; ‏Z3Counterexample ו־PatientSlaExpired → `patient_deadline` | §3, §12.4 |
| `SafetyLevel` | ‏LowRisk, MediumRisk, HighRisk, CriticalRisk | §8 |
| `Component` | רשימת הרכיבים שמפיקים אירועים, וגם `EXTERNAL` | §1, §13.2 |

## 6. טבלת המעברים (`fsm.py`) וה־Guards (`guards.py`)

### 6.1 מבנה של שורה

```python
@dataclass(frozen=True)
class Transition:
    source: State | None                      # None = Initial
    event: Event
    target: State
    guards: tuple[str, ...]                   # שמות מסעיף 3.1, למשל ("InPlan", "PlanIntact")
    escalation: EscalationSpec | None         # סוגי ההסלמה המותרים + escalated_from_state
    effects: tuple[Effect, ...]               # שינויים בשורת cases
    spec_guard: str                           # טקסט ה־Guard כפי שהוא מופיע במפרט
```

- **41 שורות.** בדיקה קוראת את הטבלה מ־`docs/spec/03-transitions-guards.md` ומשווה את `(source, event, spec_guard, target)` לכל שורה. אם המפרט משתנה, הבדיקה נכשלת.
- **כלל ההתאמה:** מתוך השורות של אותו `(state, event)`, בדיוק שורה אחת צריכה לעבור את כל ה־Guards שלה.
  - אף שורה לא עוברת: `Blocked: guard_failed`. ה־State לא משתנה ונכתבת רשומת Audit.
  - יותר משורה אחת עוברת: זו שגיאה בטבלה, והמערכת נעצרת ב־fail closed.

### 6.2 Guards

- כל Guard הוא פונקציה `(case, payload, source, ctx) -> GuardResult`, עם השם שלו מסעיף 3.1.
- **עמודת "מי מעריך" בסעיף 3.1 נאכפת בקוד:**
  - כשכתוב "X קובע · State Manager מאמת", העובדה מתקבלת רק כש־`source` הוא הרכיב X. ה־State Manager משווה אותה ל־State השמור (למשל `RequestValid`, `DocumentValid`).
  - כשכתוב "State Manager, מה־State השמור", ה־Guard מחושב רק מתוך שורת `cases` (למשל `CanAdvance`, `RetrievalStepsRemain`, `PreReadinessPhaseComplete`, `DeliveryStepPending`).
  - `WorkflowDecisionValid` נבדק מול טבלת `approvals` (ראו §8).
- **Guards שמוערכים ברכיבים שעדיין לא קיימים** (`ExecutorReverified`, `DeliveryConfirmed`, `PatientSlaExpired`) מוגדרים כ־ports. המימושים שלהם מגיעים בתת־פרויקטים 2–3.
- **ברירת מחדל שחוסמת:** אם port לא מחובר, הוא מחזיר false. באפליקציה עצמה כל port חייב להיות מחובר, אחרת היא לא עולה. מימושים שמאשרים הכל קיימים רק בתוך `tests/`.

### 6.3 Effects

| אירוע (שורה) | שינוי בשורת `cases` |
|---|---|
| `REQUEST_SUBMITTED` | יצירת השורה: `case_id`, `patient_id`, ‏`state_version=1`, מונים = 0 |
| `REQUEST_VALIDATED` | `identity_verified = true` |
| `INTENT_CLASSIFIED` | `intent`, `safety_level` |
| `PLAN_CREATED` | `ordered_steps`, ‏`plan_hash`, ‏`current_step = 1`, ‏`retry_cycle = attempt_count = 0` |
| `STEP_ADVANCED`, `DELIVERY_PLANNED` | `current_step + 1`, ‏`retry_cycle = attempt_count = 0` (תקציב הניסיונות הוא לכל צעד, D23) |
| `MISSING_INFORMATION_DETECTED` | `patient_deadline` מה־payload |
| `DOCUMENT_UPLOADED` (כש־DocumentValid מתקיים) | הוספה ל־`held_documents` |
| כל שורה שמסלימה | `escalation_kind`, `escalated_from_state` |
| `HUMAN_APPROVED` / PatientVerificationFailed | `identity_verified = true` |
| `HUMAN_APPROVED` / RetryExhausted | `retry_cycle + 1`, ‏`attempt_count = 0` |
| `HUMAN_APPROVED` / Z3Counterexample, PatientSlaExpired | `patient_deadline` חדש מתוך האישור |
| `HUMAN_APPROVED` / `HUMAN_REJECTED` / `HUMAN_RESOLVED_CASE` | צריכת האישור (`approvals.consumed_at`); חריג אחד, ראו §12 |

`plan_hash = sha256(json.dumps(ordered_steps, separators=(",", ":"), sort_keys=True))`. הנוסחה הזו מתאימה ל־`json.marshal` של OPA, ובדקנו שהיא מחזירה בדיוק את ה־hash שבדוגמה בסעיף 8.

## 7. State Manager (`state_manager.py`)

`apply(case_id, event, payload, source) -> TransitionResult`. כל קריאה רצה בטרנזקציה אחת:

1. קוראים את שורת `cases` ואת ה־`state_version` הנוכחי. ב־`REQUEST_SUBMITTED` השורה עוד לא קיימת, ומצב המקור הוא Initial.
2. **חסימות לפני הטבלה:**
   - אירוע בבעלות המערכת שה־`source` שלו אינו הבעלים → `Blocked: system_owned_event`.
   - פנייה במצב סיום → `Blocked: guard_failed` (T11, D14).
   - אירוע מתוך `NON_TRANSITION_EVENTS` → נרשם ב־Audit בלי מעבר. בפועל משתמשים בזה רק מתת־פרויקט 3.
3. מוצאים את השורה בטבלה (§6.1). אם אין שורה מתאימה, נכתבת רשומת `Blocked` בטרנזקציה נפרדת. `state_version` לא עולה.
4. **Temporal Monitor:** ה־port בודק את ה־trace הקיים יחד עם המעבר המבוקש. אם יש הפרה, המעבר נחסם (Blocked) וה־Escalation Coordinator מקבל signal מסוג `TemporalViolation`. אם ה־Monitor לא זמין, אין commit.
5. `INSERT audit_log`, ואז `UPDATE cases SET ..., state_version = state_version + 1 WHERE case_id = ? AND state_version = ?`. ולפי הצורך גם `UPDATE approvals SET consumed_at`.
6. **נעילה אופטימית:** אם ה־UPDATE עדכן אפס שורות, הטרנזקציה מתבטלת, השורה נקראת מחדש, וחוזרים לשלב 2. מספר העיבודים מחדש מוגבל ל־3. אחרי זה נזרקת שגיאה, ושום דבר לא נשמר.
7. אם כתיבת ה־Audit נכשלה, הכל מתבטל והפנייה נשארת במצב הקודם.

**Escalation Coordinator (`escalation.py`):** ‏`signal(case_id, kind, from_state, source)`. הוא מפיק `HUMAN_REVIEW_REQUIRED` עם `source = EscalationCoordinator`, רק אם שלושה תנאים מתקיימים:
- ה־`source` הוא רכיב פנימי מורשה;
- ה־`kind` נמצא ברשימת הסוגים שמותרים בשורה של המצב הנוכחי;
- `from_state` שווה ל־State הנוכחי.

אחרת נכתב `Blocked: invalid_escalation_reason`. בנוסף, `HUMAN_REVIEW_REQUIRED` מכל `source` אחר נחסם כבר בשלב 2 (`system_owned_event`).

## 8. Postgres

- **ארבע טבלאות, בדיוק לפי סעיף 18.2:** `cases`, `executions`, `audit_log`, `approvals`, כולל אותם מפתחות ואינדקסים. הן נוצרות ב־Alembic. ב־Core משתמשים בפועל רק ב־`cases`, ‏`audit_log` ו־`approvals`. הטבלה `executions` נוצרת כבר עכשיו כדי שה־schema יהיה שלם.
- **שני roles:**
  - migrations רצים עם בעל ה־DB;
  - האפליקציה מתחברת כ־`hospital_app`, שיש לו רק `SELECT, INSERT` על `audit_log`. כך ה־DB עצמו אוכף שאי אפשר לשנות או למחוק Audit.
- **`WorkflowDecisionValid`** נבדק מול `approvals` לפי:
  - `approval_id` מה־payload;
  - התאמה של `case_id` ו־`patient_id`;
  - `granted_at <= now < valid_until`;
  - `consumed_at IS NULL`;
  - `decision` שתואם לאירוע (approve ↔ HUMAN_APPROVED, reject ↔ HUMAN_REJECTED, resolve ↔ HUMAN_RESOLVED_CASE);
  - השדה הנוסף שנדרש לפי `escalation_kind`.

  **כשל** לא משנה את ה־State, ונכתבת רשומת Blocked עם הסיבה: `identity_not_established`, ‏`patient_deadline_missing`, ‏`approval_decision_mismatch` וכו'.

## 9. API לקריאה בלבד (עבור ה־Case Monitor)

| Endpoint | מחזיר |
|---|---|
| `GET /health` | תקינות, כולל חיבור ל־DB |
| `GET /cases?state=` | רשימת פניות: `case_id`, ‏`state`, ‏`escalation_kind`, ‏`updated_at` |
| `GET /cases/{case_id}` | שורת `cases` המלאה |
| `GET /cases/{case_id}/audit` | ה־trace המלא, ממוין לפי `audit_id` |

- ב־Core אין הרשאות. בתת־פרויקט 5 ה־API יוגבל לצוות דרך ה־IdP המדומה.
- `patient_id` לא מופיע ב־URL, כדי שלא ייכנס ל־Application Log (סעיף 12.3).

## 10. בדיקות ומתי ה־Core גמור

**הרצה:** הבדיקות רצות בקונטיינר, מול DB בדיקות נפרד שמוקם באותו Postgres:

```bash
docker compose run --rm backend pytest
```

| קבוצה | מה נבדק |
|---|---|
| התאמה למפרט | 41 המעברים מול `docs/spec/03`; ‏26 האירועים ו־12 המצבים מול `docs/spec/02`; ‏6 הפעולות מול `docs/spec/05`; בעלות האירועים מול סעיף 13.2 |
| בדיקות D | D14 (מצב סיום סופג), D15 (שני `TOOL_TRANSIENT_FAILURE` במקביל, רק אחד נשמר), D20 (`CASE_RESOLVED` בלי DeliveryConfirmed), D25 (מסמך לא תקין נשאר ב־AwaitingPatientInput), D32 (`READINESS_PASSED` כשמסמכי החובה לא מכוסים), D34, D35 |
| שלמות | `guard_failed` כותב Audit ולא משנה State; ‏`system_owned_event`; ‏`invalid_escalation_reason`; rollback כשכתיבת ה־Audit נכשלת; גרסה ישנה מעובדת מחדש; ה־role ‏`hospital_app` לא יכול לעשות UPDATE או DELETE על `audit_log`; State נשמר אחרי engine חדש (סימולציה של restart) |
| תרחישים | בדיקה מזינה את רצף האירועים של כל תרחיש מסעיף 15, עם ה־`source` הנכון לכל אירוע. היא מוודאת את מסלול ה־States ואת מצב הסיום. ספירת שורות ה־Audit (35, 4, 54) נבדקת רק בתת־פרויקט 3 |

**הגדרת "גמור":**
- `docker compose up` מעלה את `db` (בפורט 54322) ואת `backend`, ה־migrations רצים, ו־`GET /health` מחזיר 200.
- כל הבדיקות עוברות.
- `CLAUDE.md` מעודכן עם הפקודות.

## 11. תיקונים למפרט (נכנסים ל־`docs/spec_corrections.md`)

1. **מספר הטבלאות:** סעיף 1 מונה 3 טבלאות ב־Postgres (`cases`, ‏`executions`, ‏`audit_log`), אבל סעיף 18.2 מגדיר 4 (עם `approvals`). אנחנו הולכים לפי סעיף 18.2.

## 12. פרשנויות שדורשות אישור

1. **מתי אישור PolicyReview נצרך:** במעבר `HUMAN_APPROVED` מ־PolicyReview, האישור **לא נצרך**. הוא נשאר פתוח כ־`PolicyReviewOverrideValid`, ונצרך בהחלטת ה־Policy הבאה (תת־פרויקט 2). הסיבה: ה־`approval_envelope` של OPA דורש `consumed_at == null`, וסעיף 3.1 כותב שה־override "נצרך בהחלטת Policy הבאה". ייתכן שהמסמך הנלווה מכריע אחרת.
2. **עיבוד מחדש אחרי גרסה ישנה:** המפרט כותב שהאירוע "מעובד מחדש", ולא מגביל כמה פעמים. קבענו עד 3 פעמים, ואז fail closed.
