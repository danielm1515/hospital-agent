# תת־פרויקט 3: Execution — מסמך Design

**תאריך:** 2026-09-19
**סטטוס:** אושר בשיחה, ממתין לסקירת המסמך
**תלוי ב:** תת־פרויקטים 1 (Core) ו־2 (Policy), שכבר מוזגו ל־main
**מקור אמת:** `Hospital_Agent_Clean.docx`, בהמרה ל־`docs/spec/`. הסעיפים הרלוונטיים הם §3.1 (ExecutorReverified, AttemptsAvailable, IsIdempotent, DeliveryConfirmed, PatientSlaExpired), §6.1, §12.1–§12.4, §13.2, §14, §15, §16 ו־§18.2. בנוסף, הסעיף "Hand-off to sub-project 3" ב־`CLAUDE.md`.

## 1. היקף

תת־הפרויקט בונה את הצד של הביצוע:

- **Tool Executor:** מקבל החלטות Policy, מאמת אותן מחדש (`ExecutorReverified`), רושם את זוג ההתחלה, קורא למערכת החיצונית ורושם את התוצאה.
- **Retry Manager.**
- **המערכות החיצוניות המדומות.**
- **SLA / Timer Worker.**
- **התאוששות אחרי restart.**
- **`python3 -m obs.golden`.**

**מחוץ להיקף:**

| מה | באיזה תת־פרויקט |
|---|---|
| Classifier, Planner, Response Evaluator ו־Agent Orchestrator אמיתיים עם LLM | 4 |
| Human Review Service וה־API לכתיבה | 5 |
| UI | 6 |

עד שהם ייבנו, רכיבי דמו מתוסרטים ממלאים את מקומם.

**החלטת המשתמש:** ההחלטה של ה־Policy נשמרת בשורת `executions` (גישה א'). ה־Tool Executor משווה אליה לפני כל קריאה.

## 2. מבנה

```
backend/hospital_agent/execution/
  __init__.py
  gateway.py       ToolGateway (ממשק) + MockGateway: תורים, מסמכים, הוראות, ערוץ מטופל
  executor.py      ToolExecutor: executor_reverified (ה־port), start, call, finish
  retry.py         Retry Manager: after_failure(case, idempotent) -> אירוע או הסלמה
  sla.py           SlaWorker: tick(now), ו־thread רקע באפליקציה
  recovery.py      recover(state_manager): started -> unknown + הסלמה ExecutionUnknown
backend/hospital_agent/scripted.py   רכיבי דמו מתוסרטים: Session, Classifier, Planner, Orchestrator, סוקרים
backend/obs/__init__.py, backend/obs/golden.py   python3 -m obs.golden
backend/alembic/versions/0002_execution_binding.py
```

`tests/driver.py` עובר להשתמש ב־`scripted.py` וב־Tool Executor, כדי שהבדיקות ו־`obs.golden` ירוצו על אותו קוד.

## 3. מהלך ביצוע אחד

### 3.1 קבלת ההחלטה (`POLICY_ALLOWED`)

**ה־Policy Service** מצרף ל־payload של כל החלטה את מה שהיא חושבה עליו:
- `decided_state_version`, ‏`plan_hash`, ‏`current_step`;
- ה־`approval_id` של ContentApproval, אם יש.

**`ToolExecutor.executor_reverified(ctx)`** הוא המימוש האמיתי של ה־port `GuardPorts.executor_reverified`. הוא מחזיר true רק אם כל התנאים האלה מתקיימים:
- ה־`decision_token` וה־`execution_id` לא ריקים;
- ה־`execution_id` עוד לא קיים בטבלת `executions`;
- ‏`decided_state_version == case.state_version`: שום דבר לא השתנה מאז ההחלטה;
- ‏`plan_hash == case.plan_hash == compute_plan_hash(case.ordered_steps)`;
- ‏`current_step == case.current_step`;
- ‏`action == case.current_action`.

**באותה טרנזקציה של המעבר**, ה־State Manager כותב שורת `executions` (Effect חדש, `RECORD_EXECUTION_INTENT`, בשתי שורות `POLICY_ALLOWED`) עם השדות הבאים:
- `status = 'intent'`;
- `execution_id`, `decision_token`, `action`;
- `step = current_step`, `retry_cycle`, `attempt_number = attempt_count + 1`;
- `idempotency_key = "{case_id}:{step}:{retry_cycle}:{attempt_number}"`;
- `state_version`: הגרסה אחרי המעבר;
- `plan_hash`, `approval_id`, `content_hash`, `medical_content_flag`.

### 3.2 התחלה (`ToolExecutor.start`): טרנזקציה אחת

**בדיקה חוזרת מול שורת ה־`intent`.** כל התנאים חייבים להתקיים:
- הפנייה במצב RetrievingData או Delivering;
- ‏`row.state_version == case.state_version`;
- ה־`plan_hash`, הצעד והפעולה תואמים;
- ‏`idempotency_key` קיים;
- בפלט רפואי: `approvals.content_approval_valid` על האישור השמור ב־`row.approval_id`.

**כל התנאים עברו:**
1. **ניסיונות:** אם `AttemptsAvailable` לא מתקיים (נבדק על המונה **לפני** ההגדלה), ה־Retry Manager מפיק `RETRY_EXHAUSTED` ואין קריאה. אחרת:
2. **מונה:** ‏`attempt_count + 1`, בעדכון עם נעילה אופטימית.
3. **סטטוס:** ‏`status = 'started'` ו־`started_at`.
4. **צריכת אישור:** בפלט רפואי, ה־ContentApproval נצרך באותה טרנזקציה (§12.4).
5. **זוג ההתחלה:** נכתבות שתי שורות Audit עם `record_type = 'ExecutionStarted'` (החלטה 4):
   - `TOOL_EXECUTION_STARTED`, שה־`guards` שלה כוללים את הראיות של §6.1: `InPlan`, `IdentityVerified`, `PatientContextPresent`, `AttemptsAvailable`, `medical_content_flag`, ובפלט רפואי גם `ContentApprovalValid`. בנוסף `execution_id` ו־`content_hash`.
   - `AUDIT_RECORDED` לאותו `execution_id`.
6. **Temporal Monitor:** בודק את שתי השורות, אחת אחרי השנייה, לפני ה־commit. אם יש הפרה, אין commit ומתחיל מסלול ה־TemporalViolation של ה־Core.

**בדיקה נכשלה:** אין קריאה חיצונית (§14). נכתבת שורת Blocked עם `executor_reverification_failed`, השורה עוברת ל־`status = 'failed'`, והפנייה מוסלמת כ־`ExecutionUnknown` (החלטה 2).

### 3.3 הקריאה

`gateway.call(action, parameters, idempotency_key) -> ToolResult(kind, data)` רצה **מחוץ** לטרנזקציה.
- `kind` הוא `ok`, `transient_failure` או `error`.
- ה־`parameters` כוללים רק את השדות שעברו מזעור (`patient_fields` של ההחלטה).

### 3.4 התוצאה (`ToolExecutor.finish`): טרנזקציה אחת

ה־State Manager מקבל פרמטר חדש, `execution_outcome`, שנכתב באותה טרנזקציה של המעבר או ההסלמה. הוא כולל:
- עדכון `executions.status` ל־`succeeded` / `failed` / `unknown`, ו־`finished_at`;
- שורת Audit מסוג `ExecutionSucceeded` / `ExecutionFailed` / `ExecutionUnknown`. שדה `outcome` שלה הוא `success` / `failed` / `unknown`, והאירוע שלה הוא אירוע התוצאה.

| מה חזר | אירוע התוצאה |
|---|---|
| `ok` | ‏`DATA_RETRIEVED` מה־Tool Executor, עם תוצאת הכלי: `CheckAppointment` → `appointment_at`; `CheckDocuments` → `required_documents` ו־`held_documents`; `LoadInstructions` → `instruction_ids` |
| `ok` על `SendStatusUpdate` | ‏`CASE_RESOLVED` מ־Response Delivery. ‏`DeliveryConfirmed` נבדק מול השורה |
| `transient_failure` | לפי `retry.after_failure`: `TOOL_TRANSIENT_FAILURE` אם נשארו ניסיונות והפעולה אידמפוטנטית; `RETRY_EXHAUSTED` אם `attempt_count ≥ 3`; signal ‏`NonIdempotentFailure` אם הפעולה לא אידמפוטנטית |
| `error` | כמו פעולה לא אידמפוטנטית: signal ‏`NonIdempotentFailure`. אין ניסיון חוזר אוטומטי |

**ספירת השורות:** כל ביצוע מוסיף שלוש שורות: שתי שורות הזוג ושורת תוצאה. בתרחיש 1 יש 31 שורות trace ו־4 שורות תוצאה, 35 בסך הכל. בתרחיש 3 יש 47 ו־7, 54 בסך הכל. בתרחיש 2 יש 4. כל המספרים תואמים ל־§15.

## 4. Retry Manager, אידמפוטנטיות ומערכות מדומות

- **`retry.after_failure(case, idempotent) -> RetryVerdict`:** פונקציה טהורה, עם אותם כללים כמו בטבלה של §3.4. היא נבדקת לבד.
- **אידמפוטנטיות (החלטה 3):** כל ארבע הפעולות האוטומטיות מוגדרות אידמפוטנטיות (`IDEMPOTENT_ACTIONS` ב־`gateway.py`). ערוץ המטופל המדומה מתעלם מכפילויות לפי `idempotency_key`. ‏D27 בודק את המסלול הלא־אידמפוטנטי דרך הגדרה של המערכת המדומה.
- **`MockGateway`:** נתוני דמו דטרמיניסטיים (החלטה 5), עם תסריט כשלים לכל פעולה (`failures={"CheckDocuments": 2}`), וגם מצב `error` ומצב "לא אידמפוטנטי".

| פעולה | מה המערכת המדומה מחזירה |
|---|---|
| CheckAppointment | תור לקולונוסקופיה: `appointment_at = now + 96h` (ניתן להגדרה) |
| CheckDocuments | ‏`required_documents = [referral, blood_test]`, ‏`held_documents = [referral]` |
| LoadInstructions | ‏`instruction_ids = [INSTR-PREP-COLONOSCOPY v3]` |
| SendStatusUpdate | ‏`delivered = true` |

## 5. SLA Worker, התאוששות ומוכנות

- **`SlaWorker.tick(now)`:** סורק פניות במצב `AwaitingPatientInput` עם `patient_deadline <= now` (לפי האינדקס `(state, patient_deadline)` של §18.2). לכל אחת הוא מפיק `TIMEOUT_EXPIRED` עם `registered_state_version = case.state_version`, ו־`source = SlaWorker`.
  - בזכות הנעילה האופטימית, אירוע שהגרסה שלו כבר לא עדכנית פשוט לא עובר.
  - באפליקציה הוא רץ כ־thread רקע כל `SLA_INTERVAL_SECONDS`, ברירת מחדל 30. הבדיקות קוראות ל־`tick` ישירות.
- **`recovery.recover(state_manager)`:** לכל שורה עם `status = 'started'`:
  - ה־status עובר ל־`unknown`;
  - נכתבת שורת `ExecutionUnknown`;
  - נשלחת הסלמה `ExecutionUnknown` מהמצב הנוכחי (RetrievingData או Delivering), באותה טרנזקציה;
  - אין replay.

  שורות `intent` נשארות, כי אף קריאה לא יצאה. הפונקציה נקראת בעליית האפליקציה (lifespan) ובבדיקת D28.
- **מוכנות:** `ReadinessCheck.run(case_id)` מחשב את `hours_until` מ־`cases.appointment_at`, שנשמר מתוצאת `CheckAppointment`. אם אין ערך, התוצאה היא `invalid_deadline` והפנייה מוסלמת. הפרמטר `hours_until` יוצא מהממשק. זה סוגר את נקודת ההעברה מתת־פרויקט 2.

## 6. רכיבי הדמו המתוסרטים ו־`obs.golden`

- **`hospital_agent/scripted.py` (`ScriptedAgents`):** רכיבי דמו שעומדים במקום ה־Session Service, ה־Classifier, ה־Planner, ה־Orchestrator והסוקרים, עד תת־פרויקטים 4–5. הרכיבים האלה בלבד מתוסרטים; כל השאר אמיתי.
  - `submit`, `validate`, `classify`, `plan` ו־`propose` שולחים את האירוע מהבעלים שלו לפי §13.2.
  - `run_step()` מריץ צעד אחד מההצעה ועד התוצאה.
- **`python3 -m obs.golden`:**
  - מריץ את שלושת התרחישים על ה־DB של הבדיקות (`TEST_DATABASE_URL`, החלטה 6), אחרי ניקוי הטבלאות.
  - משתמש ב־Policy, Z3, ה־Monitor, ה־Tool Executor וה־MockGateway האמיתיים.
  - מדפיס כל שורה בפורמט של §15: `[State] EVENT  notes`. לכל תרחיש יש כותרת `SCENARIO N  …` ושורת סיום `final: X   audit rows: N`.
- **בדיקה:** רצף המצבים והאירועים זהה ל־§15, כולל שורות ה־STARTED/AUDIT, והמספרים הם 35/4/54. ההערות בסוף השורה הן לתצוגה בלבד, והבדיקה לא משווה אותן.

## 7. בדיקות ומתי זה גמור

- **יחידה:** ה־Retry Manager, ה־MockGateway, `executor_reverified` (כל תנאי בנפרד) ו־`SlaWorker.tick`.
- **מקצה לקצה:** שלושת התרחישים דרך ה־Tool Executor, מול §15 המלא ועם ספירת שורות ה־Audit. ה־Temporal Monitor רץ על traces אמיתיים, כולל T1, T2, T3, T4, T6, T9 ו־T12.
- **בדיקות D:**
  - D7: שלושה ניסיונות, כל אחד עם `POLICY_ALLOWED` משלו, ואז `RETRY_EXHAUSTED`. אין ניסיון רביעי.
  - D11: פלט רפואי עם ContentApproval מקצה לקצה, כולל צריכת האישור.
  - D12: שלוש רשומות Audit לשלושה ניסיונות.
  - D20: ‏`SendStatusUpdate` שנכשל, והפנייה נשארת ב־Delivering.
  - D23: המונה מתאפס בצעד חדש.
  - D27: פעולה לא אידמפוטנטית: `NonIdempotentFailure`, בלי ניסיון חוזר.
  - D28: restart: ‏`ExecutionUnknown` והסלמה, אפס replay.
- **שליטה:** replay של החלטה אחרי שהפנייה השתנתה נחסם. SLA שפג מפיק `TIMEOUT_EXPIRED`, ואירוע SLA עם גרסה ישנה נזרק.

**הגדרת "גמור":**
- `docker compose run --rm backend pytest` עובר.
- `docker compose run --rm backend python -m obs.golden` מדפיס את שלושת ה־traces עם `audit rows: 35 / 4 / 54`.
- `CLAUDE.md` ו־`docs/spec_corrections.md` מעודכנים. בפרט, סעיף ההעברה לתת־פרויקט 3 מוחלף בסעיף העברה לתת־פרויקט 4.

## 8. החלטות (אושרו על ידי המשתמש)

1. **Migration 0002 (תוספת ל־§18.2):**
   - ב־`executions` נוספות העמודות `state_version`, `plan_hash`, `approval_id`, `content_hash` ו־`medical_content_flag`.
   - ב־`cases` נוספת העמודה `appointment_at`.
2. **כשל של ExecutorReverified לפני קריאה:** אין קריאה, שורת Blocked (`executor_reverification_failed`), והסלמה כ־`ExecutionUnknown`. זה הסוג הקרוב ביותר ברשימה המותרת מ־RetrievingData ו־Delivering, ובלי זה הפנייה נתקעת.
3. **אידמפוטנטיות:** כל ארבע הפעולות האוטומטיות מוגדרות אידמפוטנטיות, וערוץ המטופל מתעלם מכפילויות לפי `idempotency_key`.
4. **סוגי שורות:** שתי שורות הזוג הן `record_type = ExecutionStarted`. שורות התוצאה (`ExecutionSucceeded/Failed/Unknown`) לא נכנסות ל־trace של ה־Temporal Monitor. זו הרחבה של החלטה 1 בתת־פרויקט 2.
5. **תוכן הדמו:**
   - תור לקולונוסקופיה בעוד 96 שעות.
   - מסמכי החובה: הפניה ובדיקת דם, כשההפניה כבר אצל המטופל.
   - ההוראות מהמקור `INSTR-PREP-COLONOSCOPY` בגרסה 3.
6. **`obs.golden` רץ על ה־DB של הבדיקות** (`hospital_test`), ולא על ה־DB הראשי.

כל ההחלטות ייכנסו ל־`docs/spec_corrections.md`.
