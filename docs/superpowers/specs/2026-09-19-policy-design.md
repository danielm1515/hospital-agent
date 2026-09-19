# תת־פרויקט 2: Policy — מסמך Design

**תאריך:** 2026-09-19
**סטטוס:** אושר בשיחה, ממתין לסקירת המסמך
**תלוי ב:** תת־פרויקט 1 (Core), שכבר מוזג ל־main
**מקור אמת:** `Hospital_Agent_Clean.docx`, בהמרה ל־`docs/spec/`. הסעיפים הרלוונטיים הם §3.1, §6–§11, §12.4, §13 ו־§14.

## 1. היקף

תת־הפרויקט בונה את ה־Policy Service (סעיף 1 במפרט), על כל חמשת המנועים שלו:

- OPA;
- Prolog;
- Datalog;
- Z3, גם לבדיקת המוכנות (§9.1) וגם לבדיקת העקביות בין השכבות (§9.2);
- Temporal Monitor.

בנוסף הוא בונה את ה־Readiness Check, ומחבר את כל אלה ל־Core.

**מחוץ להיקף:**

| מה | באיזה תת־פרויקט |
|---|---|
| Tool Executor, `ExecutorReverified`, רישום `TOOL_EXECUTION_STARTED` ו־`AUDIT_RECORDED` | 3 |
| Classifier, Planner, Response Evaluator | 4 |
| Human Review Service וה־API לכתיבה | 5 |
| UI | 6 |

עד שהרכיבים האלה ייבנו, `tests/driver.py` ממלא את מקומם.

**החלטות שהמשתמש קיבל:**

- **OPA:** קובץ ה־Rego האמיתי מכריע בזמן ריצה, דרך הקובץ `opa` 1.9.0 שרץ כתהליך באותו קונטיינר. לצידו יש evaluator בפייתון שרץ **רק בבדיקות**, ובודק שהשניים מחזירים אותה תוצאה.
- **Prolog ו־Datalog:** מנועים בפייתון, מבוססים על המנועים מהפרויקט הקודם `AI_Hospital` ומורחבים. הם מריצים את קבצי ה־`.pl` וה־`.dl` של המפרט כמו שהם כתובים.
- **Z3:** הספרייה `z3-solver==4.15.4`.
- **ביצוע:** יחידה אחת, בתוכנית אחת.

## 2. מבנה

```
backend/hospital_agent/policy/
  __init__.py
  policy.rego                  §8 כמו שהוא (package hospital_agent.policy)
  data/minimized_fields.json   נוצר מ־flows.dl על ידי build_minimized.py; לא עורכים ידנית
  data/approved_instruction_sources.json   Approved Source Registry (נתוני דמו)
  opa_runner.py                מריץ opa eval עם timeout; כל כשל מחזיר Deny
  flows.dl                     §11 כמו שהוא
  datalog.py                   מנוע bottom-up עם שלילה מרובדת (מהפרויקט הקודם)
  build_minimized.py           flows.dl → minimized_fields.json (python -m ...)
  rules.pl                     §10 בלי העובדות של CASE-482
  prolog.py                    מנוע SLD (מהפרויקט הקודם), מורחב
  approvals.py                 Approval Validator: ContentApprovalValid (§12.4)
  service.py                   Policy Service: מאחד OPA ו־Prolog להכרעה ולאירוע
  readiness.py                 Z3 §9.1, ו־Readiness Check שמפיק אירוע או signal
  consistency.py               Z3 §9.2: 7 תכונות ב־9 שאילתות (python -m ...)
  temporal.py                  Temporal Monitor: T1–T12 על ה־trace
backend/hospital_agent/wiring.py   factory ל־State Manager עם ה־Temporal Monitor האמיתי
backend/tests/opa_reference.py     evaluator בפייתון לאותם חוקים (בדיקות בלבד)
backend/tests/fixtures/            העובדות של CASE-482 מ־§10, לשחזור השאילתות של המפרט
```

## 3. OPA

- **`policy.rego`:** הטקסט של §8 מילה במילה.
- **נתונים:** OPA מקבל את שני קובצי ה־JSON בתיקיית `data/`. כל אחד מהם יושב תחת המפתח `hospital_agent`, ו־OPA ממזג אותם.
- **`opa_runner.evaluate(input) -> {"result", "reasons"}`:** מריץ `opa eval --format json` על השאילתה `data.hospital_agent.policy.decision`, עם timeout של 5 שניות. כל מקרה חריג מחזיר Deny:

| מה קרה | תוצאה |
|---|---|
| הקובץ `opa` לא קיים, קוד יציאה שונה מאפס, timeout, או JSON פגום | `Deny`, `policy_engine_unavailable` (§14) |
| `result` שאינו אחד משלושת הערכים המוכרים | `Deny`, `no_matching_rule` |

- **Dockerfile:** מוריד את `opa_linux_amd64_static` בגרסה 1.9.0 ובודק את ה־SHA256 שלו.
- **evaluator בפייתון (`tests/opa_reference.py`):**
  - מממש את אותם חוקים, באותו סדר קדימויות.
  - הזמן מוזרק כפרמטר, ולכן אוסף ה־inputs משתמש בחלונות תוקף רחוקים מ"עכשיו".
  - בדיקה אחת מריצה את OPA האמיתי ואת ה־evaluator על כל ה־inputs, ומוודאת שהם מחזירים בדיוק אותה תוצאה ואותן סיבות.
  - אוסף ה־inputs כולל את טבלת §8, את בדיקות ה־D של OPA, ו־input חריג לכל חוק `deny`.

## 4. Datalog

- **`datalog.py`:** המנוע מהפרויקט הקודם, עם שלוש התאמות:
  - מתעלם מהנחיות `:- table`, כי ב־bottom-up אין בהן צורך;
  - תומך בהערות `%`;
  - מחזיר relations.
- **`flows.dl`:** §11 כמו שהוא.
- **בדיקת שאילתות:** ארבע השאילתות של §11 מחזירות את התשובות שהמפרט מתעד.
- **`build_minimized.py`:** מייצא את `minimized/2` ואת `external/1` ל־`data/minimized_fields.json`, כולל יעדים שאין להם אף שדה מותר (`instruction_system: []`). הקובץ שנכתב חייב להיות זהה לדוגמה ב־§11.
- **בדיקת סחיפה:** הקובץ שבריפו חייב להיות שווה לייצוא טרי מ־`flows.dl`.
- **אכיפה בזמן ריצה:** OPA עושה אותה, דרך החוק `field_not_minimized`. ה־Datalog מגדיר, ו־OPA אוכף.

## 5. Prolog

**`prolog.py`:** המנוע מהפרויקט הקודם (SLD עם `\+` ו־`!`), מורחב בדיוק במה שהתוכנית של §10 צריכה:
- ההנחיה `:- dynamic` (לא עושה כלום);
- רשימות `[a,b]`;
- `memberchk/2`;
- `atom/1`;
- `\==/2`;
- `retract/1`;
- `assertz/1`, לטעינת עובדות של בקשה;
- אטומים במירכאות;
- הערות `%`.

**`rules.pl`:** §10 בלי שבע העובדות של CASE-482, שעוברות ל־`tests/fixtures/`. בדיקה משחזרת את כל שאילתות §10 מול עובדות הדוגמה, וכל אחת חייבת להחזיר את התשובה שמתועדת במפרט.

**הערכה בזמן ריצה:** לכל בקשה נוצר engine חדש ומבודד.
- הוא נטען מ־`rules.pl`, ואחריו מהעובדות של הבקשה:
  - `case_identity_verified`, `case_patient`, `case_step`, `case_execution`;
  - `current_execution`, `current_patient`, `current_content_hash`;
  - `outgoing_message_evaluated`, `outgoing_medical_content`.
- העובדה `content_approval_valid/4` נטענת **רק אחרי** ש־`approvals.content_approval_valid(...)` בדק את האישור במלואו (§10: "עובדות אישור נטענות רק אחרי בדיקת Approval Validator המלאה").
- השחקן של פעולה אוטומטית הוא תמיד `patient_agent`.
- התוצאה היא `(allowed, explanation)`, כאשר `explanation` בא מ־`explain/4`.

**`approvals.content_approval_valid(approval, case, execution_id, action, content_hash, now)`:** בודק את כל התנאים של §12.4 ו־§12.5:
- הסוג הוא ContentApproval;
- התפקיד הוא `clinical_staff`;
- הפנייה, המטופל, `execution_id`, הפעולה ו־`content_hash` תואמים;
- האישור בתוקף ולא נוצל.

## 6. Policy Service (`service.py`)

**`PolicyRequest`:** הנתונים שמגיעים מרכיבים מהימנים (§8: "שדות ההודעה והאישורים נטענים מרכיבים מהימנים"):
- `execution_id`, `attempt_count`;
- `proposed_action`: ‏`action`, ‏`from_step`, ‏`target_system`, ‏`patient_fields`;
- `outgoing_message`: ‏`evaluated`, ‏`medical_content_flag`, ‏`content_hash`, או None;
- `approval`, `instruction_source`;
- `patient_verification_status`.

**`decide(case, request) -> PolicyDecision`:**
1. בונה את ה־input של OPA.
   - מ־State השמור: `case_id`, `patient_id`, `identity_verified`, `safety_level`, `intent`, `plan` (`current_step`, `plan_hash`, `ordered_steps`).
   - ‏`max_attempts` מגיע מ־`MAX_ATTEMPTS`.
   - ‏`policy_review_override` נטען מטבלת approvals: אישור WorkflowDecision פתוח מסוג PolicyReview, כבול ל־`plan_hash` ול־`current_step` של הפנייה.
2. מריץ את OPA.
3. מריץ את Prolog.
4. מאחד את התוצאות:

| מצב | הכרעה |
|---|---|
| OPA מחזיר Deny | Deny, עם הסיבות של OPA |
| OPA מחזיר RequireHumanReview | RequireHumanReview |
| OPA מאשר ו־Prolog מאשר | Allow |
| OPA מאשר ו־Prolog חוסם | Deny, עם `prolog:<reason>` מתוך `explain/4` |
| Prolog נכשל | Deny, `policy_engine_unavailable` |

5. מצמיד `decision_token` חדש (uuid).

**`decision_event(decision) -> (Event, payload)`:**
- ‏Allow → `POLICY_ALLOWED`, Deny → `POLICY_DENIED`, RequireHumanReview → `POLICY_HUMAN_REVIEW_REQUIRED`.
- ה־payload כולל `action`, `policy_result`, `policy_reasons`, `decision_token` ו־`execution_id`. אם השתמשנו ב־override, הוא כולל גם `policy_review_override_id`.
- האירוע נשלח עם `source=PolicyService`.

**שינוי ב־Core (`state_manager.py`):** כשאירוע `POLICY_*` נושא `policy_review_override_id`, האישור מסומן כמשומש **באותה טרנזקציה של המעבר**, יהיה אשר יהיה המעבר (החלטה 4). אם האישור כבר נוצל, מתקבל `_StaleVersion` והאירוע מעובד מחדש. בכל עיבוד מחדש הצריכה שוב נכשלת, ולכן בסופו של דבר נזרק `ReprocessLimitExceeded`, כלומר fail closed.

**`rule_version`:** ‏`StateManager` מקבל פרמטר `rule_version`. ה־factory ב־`wiring.py` מעביר את `transitions-v1+policy-<12 תווים ראשונים של sha256 של policy.rego, rules.pl ו־flows.dl>`.

## 7. Z3 וה־Readiness Check

**`readiness.ask_patient_is_safe(hours_until) -> Z3Verdict(safe, result, counterexample)`:**
- המודל של §9.1 כמו שהוא: `Ints`, ה־`Or` על שני סוגי המסמכים, ו־timeout של 5000ms.
- **רק `unsat` נחשב בטוח.**
- `result` יכול להיות `"unsat"`, `"sat"`, `"unknown"`, `"invalid_deadline"` או `"error"`.
- פונקציית ה־audit שהמפרט מזריק מוחלפת בהחזרת התוצאה. מי שקורא לפונקציה רושם אותה, לפי החלטה 2.

**`ReadinessCheck.run(case_id, hours_until)`:**

| מצב | מה קורה |
|---|---|
| כל מסמכי החובה קיימים (אותו חישוב כמו ה־Guard `ReadinessComplete`) | `READINESS_PASSED` |
| Z3 מחזיר `unsat` | `MISSING_INFORMATION_DETECTED` עם `z3_result: "unsat"` ו־`patient_deadline = now + 24h` (החלטה 3) |
| כל תוצאה אחרת | `EscalationCoordinator.signal(Z3Counterexample, AssessingReadiness, ReadinessCheck, reasons=[...])` |

**שינוי ב־Core (`escalation.py`):** ל־`signal()` נוסף פרמטר אופציונלי `reasons: list[str]`. הוא עובר ל־`policy_reasons` של שורת ה־`HUMAN_REVIEW_REQUIRED`, כך שהדוגמה הנגדית נשמרת ב־Audit בלי שורה נוספת (החלטה 2).

**`consistency.py`:** הסקריפט של §9.2, כפונקציה שמחזירה את תוצאות תשע השאילתות. הבדיקה דורשת `unsat` בכולן. הרצה מהשורה (`python -m hospital_agent.policy.consistency`) מדפיסה את השורה של המפרט:

```text
7 abstract properties passed (9 UNSAT queries)
```

## 8. Temporal Monitor (`temporal.py`)

**`TemporalMonitor.check(trace, candidate) -> str | None`:** מממש את ה־port `TraceMonitor` של ה־Core.

**ה־trace:** כל השורות מלבד אלה שה־`record_type` שלהן הוא `Blocked` (החלטה 1).

**פסוקים אטומיים (§6.1):**

| פסוק | מה בודקים בשורה |
|---|---|
| `Execute(e)` | ‏`event == TOOL_EXECUTION_STARTED` ו־`execution_id == e` |
| `PolicyAllowed(e)` | שורת `POLICY_ALLOWED` עם אותו `execution_id` |
| `InPlan`, `IdentityVerified`, `PatientContextPresent`, `AttemptsAvailable` | ה־JSON של ה־Guards בשורת הביצוע |
| `HumanReview` | ‏`state_after == AwaitingHumanReview` |
| `MedicalAnswer(e,h)` | שורת ביצוע עם `guards["medical_content_flag"]` ו־`content_hash == h` |
| `HumanAuthorized(e,h)` | שורה קודמת עם `guards["ContentApprovalValid"]` לאותם `e` ו־`h` |
| `ContentApprovalValid(e,h)` | ה־Guard הזה בשורת הביצוע עצמה |
| `ReadinessComplete` | ה־Guard הזה בשורה |
| `AuditRecorded(e)` | ‏`event == AUDIT_RECORDED` עם אותו `e` |
| `Classifying`, `Terminal` | ‏`state_after` |
| `DocumentValid` | ה־Guard בשורת `DOCUMENT_UPLOADED` |

**אופרטורים (§6.1):**
- ‏`Y`: השורה הקודמת. `Y` בשורה הראשונה הוא false.
- ‏`O`: עכשיו או בעבר.
- ‏`X`: השורה הבאה. בסוף ה־trace, X של מצב סיום מתקיים, כי מצב הסיום "חוזר". X של כל מצב אחר עדיין ממתין, ולא נחשב הפרה עד שתגיע השורה הבאה.

**בדיקה אינקרמנטלית:** הבדיקה מחשבת את ההפרות של `trace + [candidate]` ומחזירה את **הראשונה שלא הייתה ב־trace לבד**. כך חוקי ה־X (T9, T11) נבדקים כשהשורה הבאה מגיעה, בלי לשנות את ה־port.

**הבדיקות:**
- לכל אחד מ־T1–T12 יש trace תקין ו־trace מפר.
- שש החריגות של §7 נתפסות, כל אחת על ידי החוק ש־§7 מציין.
- שלושת התרחישים עוברים עם ה־Monitor האמיתי בלי אף הפרה.

## 9. חיבור ל־Core ובדיקות

- **`wiring.build_state_manager(engine, ports)`:** יוצר State Manager עם `TemporalMonitor()` ועם `rule_version` של המדיניות. זו הדרך היחידה שקוד האפליקציה בונה State Manager.
- **`tests/driver.py`:** ‏`allow()` מוחלף ב־`decide()`, שקורא ל־`PolicyService` האמיתי עם `PolicyRequest` שה־Driver בונה לכל פעולה:

| פעולה | `target_system` | `patient_fields` | תוספות |
|---|---|---|---|
| CheckAppointment | `appointment_system` | `[patient_id]` | |
| CheckDocuments | `document_system` | `[patient_id]` | |
| LoadInstructions | `instruction_system` | `[]` | `instruction_source` מאושר |
| SendStatusUpdate | `patient_channel` | `[patient_id]` | הודעה שעברה Response Evaluator, לא רפואית |

- כש־Z3 מעורב, ה־Driver מפעיל את ה־Readiness Check האמיתי.
- **התרחישים:** 1–3 רצים עם OPA, Prolog, Z3 וה־Temporal Monitor האמיתיים, והם עדיין חייבים להתאים ל־golden traces של §15.
- **בדיקות D חדשות:** D3, D4, D5, D6, D8, D10, D11, D13, D16, D17, D18, D22, D29, D30, D31.
- **בדיקות אחידות בין מימושים:**
  - OPA האמיתי מול ה־evaluator בפייתון;
  - הקובץ `minimized_fields.json` מול ייצוא טרי מ־Datalog;
  - הפעולות ב־`to_prolog` מול הפעולות ב־`rules.pl`.

**הגדרת "גמור":**
- `docker compose run --rm backend pytest` עובר.
- ב־`docker compose run --rm backend opa version` מופיע `1.9.0`.
- `python -m hospital_agent.policy.consistency` מדפיס את השורה של §9.2.
- `CLAUDE.md` ו־`docs/spec_corrections.md` מעודכנים.

## 10. החלטות (אושרו על ידי המשתמש)

1. **ה־trace של ה־Temporal Monitor לא כולל שורות `Blocked`.** שורה חסומה היא לא מעבר, ואם היא תיכנס ל־trace היא תיצור הפרות מדומות, למשל לפי T9.
2. **הדוגמה הנגדית של Z3 נשמרת ב־`policy_reasons` של שורת ההסלמה**, ולא בשורת Audit נוספת. כך מספרי שורות ה־Audit ב־§15 (35/4/54) נשמרים: שורה ל־trace, ועוד שורת תוצאה לכל ביצוע.
3. **`patient_deadline = now + 24h`:** זה חלון ההעלאה הארוך ביותר במודל של §9.1, והמפרט לא קובע ערך. ייתכן שהמסמך הנלווה מכריע אחרת.
4. **אישור PolicyReview (override) נצרך בהחלטת ה־Policy הבאה, יהיה אשר יהיה המעבר**, ובאותה טרנזקציה.

כל ארבע ההחלטות ייכנסו ל־`docs/spec_corrections.md`.
