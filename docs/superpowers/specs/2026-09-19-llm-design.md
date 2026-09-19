# תת־פרויקט 4: LLM — מסמך Design

**תאריך:** 2026-09-19
**סטטוס:** אושר בשיחה, ממתין לסקירת המסמך
**תלוי ב:** תת־פרויקטים 1 (Core), 2 (Policy) ו־3 (Execution), שכבר מוזגו ל־main
**מקור אמת:** `Hospital_Agent_Clean.docx`, בהמרה ל־`docs/spec/`. הסעיפים הרלוונטיים הם §1 (רכיבים), §2.2 (`ACTION_PROPOSED` = "Planner הציע צעד. הצעה בלבד"), §3 (הערות על סיווג מחדש), §3.1 (`PlanComplete`, `InPlan`, `MessageEvaluated`, `SystemEscalationRequired`), §5, §6.5 (TCB), §12.3 (Data Log), §13.2, §14, §15, §16 (D1, D2, D9, D21, D26) ו־§18.5. בנוסף, הסעיף "Hand-off to sub-project 4" ב־`CLAUDE.md`.

## 1. היקף

תת־הפרויקט בונה את ארבע קריאות ה־LLM של §18.5 ואת הרכיבים שמפעילים אותן:

- **Classifier Service:** ‏Intent Classifier ו־Safety Classifier.
- **Planner Service:** בונה את התוכנית, מציע כל צעד, ומכיל את ה־Response Evaluator.
- **Model Selector:** בחירת הספק והמודל.
- **Agent Orchestrator:** מקדם פניות ברקע וממשיך אחרי restart.
- **Data Log** (§12.3): המקום שבו נשמר תוכן הפנייה, המסמכים, ההוראות וההודעות.
- **תבנית הודעת הסטטוס** ל־`SendStatusUpdate`.

**מחוץ להיקף:**

| מה | באיזה תת־פרויקט |
|---|---|
| Session Service, Human Review Service וה־API לכתיבה | 5 |
| UI | 6 |
| D33: סט הערכה ומדידת recall של ה־Response Evaluator | 7 |

עד שהם ייבנו, `scripted.py` ממשיך לשחק רק את ה־Session Service (הגשה, זיהוי, העלאת מסמך) ואת העובדים.

**החלטות המשתמש בשיחה:**
- הודעת הסטטוס נבנית מ**תבנית קבועה** שמתמלאת מעובדות ה־State. ה־LLM לא מנסח אותה.
- ה־Orchestrator רץ **ברקע** וממשיך פניות אחרי restart לפי ה־State השמור ב־DB.
- **ה־Planner מציע כל צעד** (גישה א'), לפי לשון האפיון: §1 "מפרק לצעדים, מציע צעד", §2.2 "Planner הציע צעד", והכלל של `InPlan` "שלוש דחיות רצופות על אותו צעד מסלימות PlanningFailed".
- **המודל הוא `gpt-5.6-luna` בלי temperature** (ראו החלטה 1 בסעיף 9).

## 2. מבנה

חבילה חדשה, `backend/hospital_agent/llm/`, עם יחידה לכל רכיב:

| יחידה | תפקיד |
|---|---|
| `provider.py` | הממשק `LLMProvider.complete(call, prompt, user_input, schema) -> dict`. ‏`OpenAIProvider` קורא ל־OpenAI. ‏`FakeProvider` מחזיר תשובות דטרמיניסטיות מתסריט, לבדיקות ול־`obs.golden`. כל תשובה שלא ניתן להשתמש בה מעלה `LLMUnusable`. |
| `model_selector.py` | ה־Model Selector של §1. בונה ספק לפי ההגדרות (`OPENAI_API_KEY`, `OPENAI_MODEL`) ומחזיר את חלק ה־LLM של `rule_version`. |
| `prompts/*.md` + `schemas.py` | ארבעה prompts נפרדים (intent, safety, planner, evaluator) וה־JSON Schemas שלהם. ה־enums נגזרים מ־`naming.py`. |
| `classifier.py` | ‏Classifier Service. |
| `planner.py` | ‏Planner Service: `plan()` ו־`propose()`. |
| `evaluator.py` | ‏Response Evaluator, שרץ כ־process נפרד. |
| `message.py` | תבנית הודעת הסטטוס. |
| `orchestrator.py` | ‏Agent Orchestrator. |
| `hospital_agent/data_log.py` + migration `0003` | ה־Data Log. |

## 3. הספק והמודל

- **‏`OpenAIProvider`:**
  - הספריה הרשמית `openai`, דרך Chat Completions, עם `model=gpt-5.6-luna` ו־`reasoning_effort="none"`.
  - `response_format` מסוג `json_schema`, עם `strict: true`.
  - timeout קבוע.
  - **אין `temperature`:** המודל מחזיר שגיאה על כל ערך מלבד 1 (נבדק מול ה־API ב־2026-09-19).
- **מה נחשב תשובה לא שמישה (`LLMUnusable`):**
  - שגיאת רשת או API, או timeout;
  - JSON לא תקין;
  - פלט שלא עובר את ה־Schema (הבדיקה נעשית שוב בצד שלנו, גם כש־strict פעיל);
  - ערך מחוץ ל־enum.
- **הגדרות:**
  - ‏`OPENAI_API_KEY` ו־`OPENAI_MODEL` (ברירת מחדל `gpt-5.6-luna`) נקראים מ־`.env`, שמוחרג מ־git.
  - ‏docker-compose מעביר אותם ל־backend.
  - ה־key לא נרשם בשום לוג.
- **בלי key:** השרת עולה, אבל ה־Orchestrator לא מופעל ו־`/health` מדווח על כך. אין מעבר שקט ל־`FakeProvider`: הוא קיים רק בבדיקות וב־`obs.golden`.
- **‏`rule_version`** (§12.2, §18.5):
  - הפורמט: `<table>+policy-<hash>+llm-<model>-<hash of the four prompts>`.
  - `build_state_manager(engine, llm_version=...)` מקבל את החלק של ה־LLM מה־Model Selector.
  - ‏`FakeProvider` נרשם כ־`llm-fake-<hash>`.

## 4. ארבע הקריאות

כל קריאה מקבלת prompt משלה ו־Schema משלה, ורואה רק את מה שהיא צריכה.

| קריאה | קלט | פלט (Schema) |
|---|---|---|
| Intent | טקסט הפנייה ומסמכים שהועלו, מה־Data Log | `intent` ∈ {`AppointmentPreparation`, `MedicalQuestion`, `Unsupported`} |
| Safety | אותו קלט, או תוכן שנשלף | `safety_level` ∈ {`LowRisk`, `MediumRisk`, `HighRisk`, `CriticalRisk`} |
| Planner: תוכנית | `intent` וטקסט הפנייה | `plan_complete: bool`, `ordered_steps: [{step, action}]`, כש־action ∈ ארבע הפעולות האוטומטיות |
| Planner: הצעה | `ordered_steps`, `current_step` והאירוע האחרון, בלי טקסט הפנייה | `action` ∈ ארבע הפעולות האוטומטיות, `from_step: int` |
| Response Evaluator | טקסט ההודעה היוצאת בלבד | `medical_content_flag: bool` |

- **‏`AppointmentPreparation`:** המסלול התפעולי של שלושת התרחישים.
- **‏`Unsupported`:** מוביל ל־`plan_complete=false`, ולכן ל־`PlanningFailed` (§3.1 `PlanComplete`: "כל כוונה מכוסה בצעד מסודר").
- **ה־LLM לא מספק עובדות:** אף Schema לא כולל `approved`, `valid` או `evaluated`, ולא עובדות על תורים, מסמכים או הוראות. `outgoing_message.evaluated=True` נקבע רק בקוד של ה־Evaluator, אחרי שהקריאה שלו הצליחה (§3.1 `MessageEvaluated`).
- **ה־Evaluator כ־process נפרד** (§6.5: "רצה כתהליך נפרד בתוך Planner Service"):
  - `evaluator.py` מריץ את הקריאה ב־process נפרד של Python, דרך `ProcessPoolExecutor` עם worker אחד.
  - ה־worker מקבל רק את טקסט ההודעה, ואין לו גישה ל־prompt של ה־Planner או ל־State.

## 5. מהלך הפנייה

ה־Orchestrator הוא thread אחד ברקע.
- **סריקה:** הוא סורק ב־DB פניות במצבים Classifying, Classified, Planning, AssessingReadiness ו־Ready, ומקדם כל פנייה צעד אחד בכל פעם, לפי ה־State השמור.
- **בלי זיכרון משלו:** אחרי restart הוא פשוט ממשיך. קריאות LLM לא משנות דבר מחוץ למערכת, ולכן מותר להריץ אותן שוב.
- **השהיה:** הוא מתעורר מיד כשפנייה חדשה נרשמת, וגם כל `ORCHESTRATOR_INTERVAL_SECONDS`.
- **שעון:** הוא משתמש בשעון של ה־State Manager.

| State | מה ה־Orchestrator מפעיל |
|---|---|
| Classifying | ‏Intent ו־Safety במקביל. הסיווג נסגר רק כששתיהן חזרו (D21). הקלט הוא טקסט הפנייה וכל מסמך שהועלה (D9, §3: "התוכן עובר סיווג בטיחות נפרד"). אחרי הסיווג, לפי §14: אם ה־intent הוא MedicalQuestion, ‏`MEDICAL_QUESTION_DETECTED`. אם הסיכון High או Critical, ‏signal של `SafetyEscalation` (D26). אחרת `INTENT_CLASSIFIED`. |
| Classified | ‏Planner: תוכנית. אם `plan_complete` הוא true, ‏`PLAN_CREATED`. אחרת signal של `PlanningFailed`. |
| Planning | אם האירוע האחרון שנרשם הוא `DATA_RETRIEVED`, ה־Orchestrator שולח `STEP_ADVANCED`. אחרת: Planner: הצעה, ‏`ACTION_PROPOSED`, החלטה של ה־Policy Service, ובאישור ה־Tool Executor מבצע. |
| AssessingReadiness | ה־Readiness Check (Z3), שכבר קיים. |
| Ready | ‏`DELIVERY_PLANNED`. |

**בקשת Policy לשליחת הודעה (`SendStatusUpdate`):**
1. `message.py` בונה את הטקסט מהתבנית, רק מעובדות ב־State: `appointment_at`, המסמכים החסרים, ומזהי ההוראות.
2. הטקסט נשמר ב־Data Log (`outgoing_message`).
3. ה־Evaluator מסווג אותו.
4. `OutgoingMessage(evaluated=True, medical_content_flag=…, content_hash=sha256(text))` נשלח לבקשת ה־Policy.
5. הודעה שסומנה רפואית נחסמת ב־OPA עד ContentApproval. זה המסלול של D8, שכבר קיים.

**בדיקה חוזרת של תוכן שנשלף** (§3: "Safety Classifier בודק מחדש תוכן שנשלף... עליית סיכון יכולה לחייב PolicyReview בצעד הבא"):
- ‏`LoadInstructions` מחזיר גם `instruction_text`.
- ה־Tool Executor שומר את הטקסט ב־Data Log (`instructions`) ומעביר אותו ל־Safety Classifier דרך hook, לפני `DATA_RETRIEVED`. ה־hook מוזרק ל־`ToolExecutor`.
- `safety_level` נכנס ל־payload של `DATA_RETRIEVED`. הטקסט עצמו לא נכנס, ו־`RESULT_FIELDS` לא משתנה עבורו.
- ה־effect `RECORD_RETRIEVAL` **רק מעלה** את `safety_level`, לעולם לא מוריד אותו. אותו כלל חל גם על `RECORD_CLASSIFICATION`: סיווג מחדש אחרי העלאת מסמך לא מבטל עלייה בסיכון שנמצאה בתוכן שנשלף (נמצא בבניית אב־הטיפוס).
- כשהרמה High או Critical, OPA מחזיר RequireHumanReview בצעד הבא, כלומר `PolicyReview`. זה כבר קיים ב־`policy.rego`.
- אם בדיקת הבטיחות נכשלת שלוש פעמים, התוצאה היא `ExecutionUnknown`, כי ה־Tool Executor הוא מי ששולח את ה־signal.

## 6. כשלים (§14)

- **תשובה לא שמישה:**
  - ה־Orchestrator מנסה שוב מיד, עד שלוש קריאות ברצף.
  - אחרי השלישית הוא שולח signal: מ־Classifying ‏`ClassificationFailed`, ומ־Classified או מ־Planning ‏`PlanningFailed`.
  - גם כשל של ה־Evaluator הוא `PlanningFailed`, כי ה־Evaluator הוא חלק מ־Planner Service.
  - הספירה נשמרת בזיכרון של ניסיון אחד. אחרי restart היא מתחילה מחדש, ובכל מקרה הפנייה מוסלמת בסוף.
- **דחיית `InPlan`:**
  - ה־State Manager רושם שורת Blocked על `ACTION_PROPOSED`.
  - ה־Orchestrator סופר ב־Audit את הדחיות הרצופות לצעד הנוכחי מאז המעבר האחרון שנרשם. בשלישית הוא שולח signal של `PlanningFailed` (§3.1 `InPlan`).
  - הספירה נעשית מה־Audit, ולכן היא שורדת restart.
- **פעולה של אדם, או פעולה לא מוכרת:** נדחית כבר ב־enum של ה־Schema.
- **ה־Policy דוחה או מבקש אדם:** המעבר שכבר קיים ב־§3 חל. ה־Orchestrator רק ממשיך מה־State החדש.
- **קריסה באמצע:** ה־Orchestrator ממשיך מה־State. קריאות שיצאו מטופלות על ידי ה־recovery של תת־פרויקט 3.
- **חריגה בתוך ה־Orchestrator:** נרשמת בלוג בלי תוכן ובלי `patient_id` (§12.3). הפנייה נשארת ב־State שלה, וה־thread ממשיך לפנייה הבאה.

## 7. Data Log (migration `0003`)

- **הטבלה `data_log`:**
  - העמודות: `entry_id` (PK), `case_id` (FK), `patient_id`, `kind`, `content` (nullable), `content_hash`, `created_at`, `deleted_at`.
  - ‏`kind` ∈ {`request_text`, `uploaded_document`, `instructions`, `outgoing_message`}.
  - אינדקס על (`case_id`, `kind`).
- **מחיקה (§12.3, §18.4):** tombstone. `content` מתאפס ו־`deleted_at` מתמלא, אבל `content_hash` נשאר. ל־`hospital_app` יש `SELECT, INSERT, UPDATE` בלי `DELETE`.
- **Audit:** ממשיך לשמור רק `content_hash`, שהוא ההפניה ל־Data Log.
- **מי כותב:**
  - ‏Session Service: `request_text` ו־`uploaded_document`. בינתיים זה `scripted.py`.
  - ‏Tool Executor: `instructions`.
  - ה־Orchestrator: `outgoing_message`.

## 8. בדיקות ומתי זה גמור

- **יחידה:**
  - ה־Schemas: פלט תקין ופלט שנדחה.
  - סדר העדיפויות של הסיווג, בכל השילובים.
  - התבנית.
  - tombstone ב־Data Log.
  - `safety_level` שרק עולה.
  - ‏`OpenAIProvider` מול HTTP מדומה: צורת הבקשה, בלי temperature, עם `reasoning_effort` ו־`json_schema` strict; שגיאה, timeout ו־JSON לא תקין.
  - ‏`rule_version`.
- **בדיקות D, עם `FakeProvider`:**
  - ‏D1: פנייה מלאה דרך ה־Orchestrator עד `Completed`.
  - ‏D2: שאלה רפואית.
  - ‏D9: מסמך עם טקסט מסוכן.
  - ‏D21: ‏Intent חזר ו־Safety עדיין ממתין. הפנייה נשארת ב־Classifying, וה־Planner לא הופעל.
  - ‏D26: ‏CriticalRisk לא רפואי.
- **כשלים:**
  - שלוש תשובות לא שמישות מובילות ל־`ClassificationFailed` ול־`PlanningFailed`.
  - שלוש דחיות `InPlan` מובילות ל־`PlanningFailed`.
  - ‏`plan_complete=false`.
  - כשל של ה־Evaluator.
  - הודעה שסומנה רפואית נדחית ב־OPA בלי ContentApproval: `POLICY_DENIED` עם `medical_answer_attempt`, כמו D8, והפנייה מוסלמת כ־`PolicyDenied`.
  - תוכן שנשלף עם סיכון גבוה מוביל ל־`PolicyReview` בצעד הבא.
- **Restart:** ה־Orchestrator נעצר באמצע פנייה, ו־Orchestrator חדש מסיים אותה.
- **Golden:**
  - `obs.golden` מריץ את שלושת התרחישים דרך ה־Orchestrator האמיתי, עם `FakeProvider`.
  - שורות ה־trace וספירת השורות 35/4/54 חייבות להישאר זהות.
- **מול המודל האמיתי:** בדיקת עשן אחת מול `gpt-5.6-luna`, שמסווגת ומתכננת פנייה אחת. היא רצה רק עם `RUN_LIVE_LLM=1`, ולא בחבילה הרגילה.

**הגדרת "גמור":**
- `docker compose run --rm backend pytest` עובר.
- `python -m obs.golden` מדפיס `audit rows: 35 / 4 / 54`.
- בדיקת העשן החיה עוברת פעם אחת.
- `CLAUDE.md` ו־`docs/spec_corrections.md` מעודכנים, כולל סעיף העברה לתת־פרויקט 5 והסרת "Still open: model ID".

## 9. החלטות (אושרו על ידי המשתמש)

1. **`gpt-5.6-luna` בלי temperature:** ‏§18.5 דורש temperature 0, אבל המודל תומך רק בערך ברירת המחדל. במקום זה: `reasoning_effort="none"` ו־JSON Schema strict. השחזוריות של ריצה אמיתית נמוכה יותר. הבדיקות וה־golden traces רצים על `FakeProvider`, והשכבות הדטרמיניסטיות ממשיכות להכריע.
2. **Data Log כטבלה חמישית ב־Postgres**, בנוסף לארבע של §18.2.
3. **בדיקה חוזרת של תוכן שנשלף:** `safety_level` ב־payload של `DATA_RETRIEVED`, ורמת הסיכון רק עולה (§3 משאיר את התנאים המדויקים למסמך הנלווה).
4. **ה־intents של הדמו:** `AppointmentPreparation`, `MedicalQuestion`, `Unsupported`.
5. **כל תשובה לא שמישה נספרת** לשלושת הכשלים של §14, כולל שגיאת רשת ו־timeout, ולא רק כשל Schema.
6. **ה־Response Evaluator רץ ב־process נפרד** (§6.5).
7. **ה־Orchestrator רץ ברקע** וממשיך מה־State השמור ב־DB.
8. **הודעת הסטטוס נבנית מתבנית קבועה**, רק מעובדות ה־State.

כל ההחלטות ייכנסו ל־`docs/spec_corrections.md`.
