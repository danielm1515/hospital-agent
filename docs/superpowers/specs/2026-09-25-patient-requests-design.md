# תת־פרויקט 15: בקשות מהמטופל — שאלת הבהרה, בקשת מסמך והודעת סיום — מסמך Design

תאריך: 2026-09-25. בקשה מפורשת של הבעלים, מחוץ לתרחישי §0 — כמו תת־פרויקטים 9 ו־14, נרשמת ב־`docs/spec_corrections.md` שורות 83–88.

בשונה מתת־פרויקט 14 (קריאה בלבד), כאן **משתנה מכונת המצבים**: מצב חדש, שני אירועים חדשים, שלוש שורות מעבר וחוק זמני חדש. כל התוספות מסומנות כ**הרחבה** ונשמרות בנפרד מרשימות ה־spec, שנוצרות מה־docx ונבדקות מולו שורה־שורה (§4).

## 1. הדרישה, כפי שהוקלטה

היום, מול פנייה שהוסלמה, לאיש הצוות יש רק סגירה ודחייה (ואישור־והמשך לחמשת הסוגים שניתן לחדש מהם). הבעלים ביקש:

- **שאלת הבהרה** למטופל ("האם התכוונת ל…"), והמטופל עונה.
- **בקשת מסמך** מסוים (למשל בדיקת שתן) מהמסך של האחות והמנהל, והמטופל מעלה אותו.
- **הודעת סיום** למטופל בסגירה או בדחייה — היום המטופל רואה "נסגרה" בלי שום הסבר.
- **אחרי מענה אנושי ה־AI לא מתערב.**

החלטות הבעלים בשיחה:

| שאלה | תשובה |
|---|---|
| מי כותב את ההודעה | שני המסלולים: **תבניות** — כל איש צוות; **טקסט חופשי** — רק `clinical_staff`, עם `ContentApproval` |
| אחרי הודעה אנושית, האם הפנייה חוזרת ל־AI | **לעולם לא** — אישור־והמשך נחסם לתמיד בפנייה הזו |
| הדדליין לתשובה | ברירת מחדל 24 שעות, איש הצוות יכול לשנות |
| המטופל לא ענה עד הדדליין | הפנייה **חוזרת לתור הצוות**, מסומנת; שום סגירה בלי אדם |
| הודעת סיום | כן, באותם כללים (תבנית / טקסט חופשי קליני), אופציונלית |
| הגישה | מצב חדש ואירועים ב־FSM (גישה 1, §3) |

## 2. מה קיים היום

- **מ־`AwaitingHumanReview` יוצאים שלושה מעברים** ([fsm.py](../../../backend/hospital_agent/fsm.py)): `HUMAN_APPROVED` (רק לסוגים ב־`RESUMABLE`, חזרה למסלול ה־AI), `HUMAN_RESOLVED_CASE` ← `Completed`, `HUMAN_REJECTED` ← `Failed`.
- **החלטה אנושית** מאושרת ע״י ה־guard `WorkflowDecisionValid` מול שורת `approvals` מסוג `WorkflowDecision` ([guards.py](../../../backend/hospital_agent/guards.py)): תפקיד, `shown_context_ref`, תוקף, `escalation_kind` זהה לזה של הפנייה, ו־`decision` שמתאים לאירוע (`DECISION_FOR_EVENT`).
- **התשובה הקלינית** (תת־פרויקט 8, `HumanReviewService.answer()`): רק `clinical_staff`, רק `MedicalQuestion`. הטקסט נרשם ב־Data Log, `ContentApproval` נקשר ל־`content_hash`, והפנייה נסגרת ב־`HUMAN_RESOLVED_CASE`. ה־State Manager מאמת את האישור (`_clinical_answer_approval`, קשור במכוון ל־`MedicalQuestion`) ומנצל אותו באותה טרנזקציה. המטופל רואה את הטקסט רק כשקיים אישור שנוצל.
- **מה המטופל רואה בסגירה או בדחייה:** `closed`, בלי הסבר ([session.py](../../../backend/hospital_agent/session.py), `_STATUS`).
- **T10:** `G((DOCUMENT_UPLOADED & DocumentValid) -> Classifying)` — מסמך תקין שהועלה **חייב** לחזור ל־`Classifying`, כלומר ל־AI.
- **SLA Worker** ([execution/sla.py](../../../backend/hospital_agent/execution/sla.py)) סורק פניות ב־`AwaitingPatientInput` שה־`patient_deadline` שלהן עבר, ופולט `TIMEOUT_EXPIRED` בשם `SlaWorker`. ה־guard `PatientSlaExpired` קשיח על `AwaitingPatientInput`, והשורה הקיימת קובעת `escalation_kind = PatientSlaExpired` — סוג שניתן לחדש ממנו.
- **ה־Orchestrator** פועל רק על `ACTIVE_STATES` (`Classifying`, `Classified`, `Planning`, `AssessingReadiness`, `Ready`). `AwaitingHumanReview` ו־`AwaitingPatientInput` לא ברשימה.
- **רשימות סגורות מול ה־spec:** `TRANSITIONS` נבדק שורה־שורה מול `docs/spec/03` (`len == 41`); `State` ו־`Event` מול `docs/spec/02` (`len == 12`, `len == 26`); `EXTERNAL_EVENTS` מול קבוצה מילולית של חמישה; שמות ה־guards ב־PascalCase מול עמודת ה־guards של `docs/spec/03`. `docs/spec/` נוצר מה־docx ואסור לערוך אותו ידנית.
- **לא מושפעים:** הוכחת §9.2 (`policy/consistency.py`) מופשטת לגמרי ולא קוראת `State` / `Event` / `TRANSITIONS`; OPA / Prolog / Datalog רואים רק פעולות שה־Planner הציע, אף פעם לא אירועים אנושיים או של מטופל; אף טסט לא משווה את T1–T12 למסמך, ואף טסט לא סופר אותם.

## 3. הגישה

**מצב חדש ואירועים ב־FSM**, כהרחבה מסומנת (§4). כל הודעה למטופל וכל תשובה שלו הן מעבר שנרשם ב־Audit, והמצב אומר את האמת: `AwaitingPatientReply`.

נפסלו:
- **שימוש חוזר ב־`AwaitingPatientInput` עם דגל** — היציאה ממנו מובילה ל־`Classifying`, ו־T10 מכריח את זה. כל guard וכל חוק היו צריכים להתפצל לפי הדגל, בדיוק בשכבה שהכי חשוב שתהיה פשוטה.
- **הודעות "בצד", בלי שינוי ב־FSM** — תקשורת עם מטופל שלא נרשמת ב־Audit היא חור ב־§12; המצב היה משקר ("ממתינה לאדם" כשהיא ממתינה למטופל); ואף אחד לא אוכף את הדדליין.

## 4. דפוס ההרחבה

הרשימות של ה־spec נשארות **בדיוק** כמו היום. כל תוספת נושאת סימון מפורש:

| מה | איפה | הטסט של ה־spec |
|---|---|---|
| `State.AWAITING_PATIENT_REPLY` | באותו `StrEnum`, ובנוסף ב־`naming.EXTENSION_STATES` | משווה את `[s for s in State if s not in EXTENSION_STATES]` ל־`docs/spec/02` ו־`== 12` |
| `Event.PATIENT_REPLY_REQUESTED`, `Event.PATIENT_REPLY_SUBMITTED` | באותו `StrEnum`, ובנוסף ב־`naming.EXTENSION_EVENTS` | משווה את החלק שמחוץ להרחבה ל־`docs/spec/02` ו־`== 26`; `EXTERNAL_EVENTS` נבדק כ־(חמשת ה־spec) ∪ `EXTENSION_EVENTS` |
| שלוש שורות מעבר | `fsm.EXTENSION_TRANSITIONS`, טבלה נפרדת | `TRANSITIONS` נשאר 41 שורות; `resolve()` מחפש בשתי הטבלאות |
| guards חדשים | שמות ב־snake_case (`reply_request_valid`, `patient_reply_valid`), כמו תנאי הפרוזה הקיימים | הטסט בודק רק שמות PascalCase מול ה־spec |
| T13 | `policy/temporal.RULES`, אחרי T12 | אין טסט השוואה ל־§6 |

אותו enum ולא enum נפרד: כל הקוד (FSM, State Manager, Audit, ה־API) עובד עם `State` ו־`Event` בלי המרות. טסט חדש (`test_extension.py`) מקבע את ההרחבה עצמה: בדיוק מצב אחד, שני אירועים, שלוש שורות, וכל אחת מתועדת כאן.

## 5. מכונת המצבים

### 5.1 המצב והאירועים

- **`AwaitingPatientReply`** — הצוות ביקש משהו מהמטופל וממתין לתשובה. לא ב־`ACTIVE_STATES`, לא ב־`TERMINAL_STATES`.
- **`PATIENT_REPLY_REQUESTED`** — אירוע חיצוני (איש צוות). מאושר ב־`WorkflowDecision` עם `decision = "request"`.
- **`PATIENT_REPLY_SUBMITTED`** — אירוע חיצוני (המטופל, דרך ה־Session Service). **לא** `DOCUMENT_UPLOADED`, גם כשהתשובה היא מסמך: T10 היה מכריח חזרה ל־`Classifying`.
- **`TIMEOUT_EXPIRED`** — הקיים, בשם `SlaWorker`, ממקור חדש.

שמות לפי §17: `AwaitingPatientReply` ב־PascalCase; האירועים UPPER_SNAKE_CASE, Object_Verb.

### 5.2 שורות ההרחבה

| # | מ־ | אירוע | guards | אל | effects |
|---|---|---|---|---|---|
| X1 | `AwaitingHumanReview` | `PATIENT_REPLY_REQUESTED` | `WorkflowDecisionValid` (`decision = request`), `reply_request_valid` | `AwaitingPatientReply` | `RECORD_REPLY_REQUEST`, `CONSUME_APPROVAL` |
| X2 | `AwaitingPatientReply` | `PATIENT_REPLY_SUBMITTED` | `patient_reply_valid` | `AwaitingHumanReview` | `CLEAR_REPLY_REQUEST` |
| X3 | `AwaitingPatientReply` | `TIMEOUT_EXPIRED` | `PatientSlaExpired` (מורחב, §8) | `AwaitingHumanReview` | `CLEAR_REPLY_REQUEST` |

- **אף שורה לא משנה את `escalation_kind`.** זה מכוון: X3 **לא** קובע `PatientSlaExpired` כמו השורה הקיימת, כי זה סוג שניתן לחדש ממנו, ו־`HUMAN_APPROVED` עליו מוביל ל־`AwaitingPatientInput` ומשם ל־AI. הפנייה חוזרת לתור עם ההסלמה המקורית שלה.
- `RECORD_REPLY_REQUEST` כותב על הפנייה: `human_engaged = true` (לתמיד), `reply_kind` (`question` / `document`), `requested_document` (קוד מהקטלוג, או NULL), `patient_deadline`.
- `CLEAR_REPLY_REQUEST` מאפס `patient_deadline`, `reply_kind` ו־`requested_document`. `human_engaged` לא מתאפס לעולם.
- הודעת סיום לא צריכה שורה חדשה: היא נוסעת על `HUMAN_RESOLVED_CASE` / `HUMAN_REJECTED` הקיימים, ב־payload (§7.4).

### 5.3 ה־guards

- **`WorkflowDecisionValid` (קיים, מורחב):** `DECISION_FOR_EVENT` מקבל `PATIENT_REPLY_REQUESTED → "request"`. **ובנוסף, הידוק ל־`HUMAN_APPROVED`:** אם `case.human_engaged`, ה־guard נכשל עם הסיבה `human_engaged`. זה הידוק fail-closed של guard קיים — לא נוסף תנאי לשורות ה־spec, ושום שורה לא נוספה.
- **`reply_request_valid` (חדש):** `reply_kind` חוקי; לבקשת מסמך — `requested_document` בקטלוג; הדדליין בעתיד, עד 7 ימים, ולא אחרי `appointment_at` כשהוא ידוע. אישור התוכן של טקסט חופשי נבדק ומנוצל ב־State Manager, כמו התשובה הקלינית (§7.3). האם document-service מוגדר — תצורת אפליקציה, ולכן נבדק ב־`HumanReviewService` ולא ב־guard, שהוא חלק מבסיס האמון (§9).
- **`patient_reply_valid` (חדש):** `ctx.source is Component.SESSION_SERVICE` (כמו `DocumentValid`); סוג התשובה תואם ל־`reply_kind`; לבקשת מסמך — המסמך התקבל ב־document-service והסוג שלו הוא `requested_document`.

## 6. "לעולם לא ל־AI" — שלוש שכבות

1. **ה־guard:** `WorkflowDecisionValid` חוסם `HUMAN_APPROVED` כש־`human_engaged` (§5.3). ב־API, `decide()` מסרב עוד לפני שנכתב משהו (`409 human_engaged`), ו־`allowed_decisions` של פנייה כזו לא מכיל `approve`.
2. **חוק זמני T13 (הרחבה, past-time כמו האחרים):**
   ```text
   T13  G(AgentState -> ¬ O PATIENT_REPLY_REQUESTED)
   ```
   `AgentState` = `state_after` באחד המצבים שבהם רכיב AI או ה־Tool Executor פועלים: `Classifying`, `Classified`, `Planning`, `RetrievingData`, `Delivering`, `AssessingReadiness`, `Ready`. ה־Monitor בודק לפני כל commit; הפרה נחסמת ומוסלמת כ־`TemporalViolation`, כמו כל חוק. החוק קורא רק את `event` ו־`state_after` שכבר בשורות ה־Audit — אין ראיה חדשה לרשום.
   **השכבה הראשונה היא ה־guard, ו־T13 הוא רשת ביטחון.** ה־prototype הראה ש־T13 לבדו חוסם את `HUMAN_APPROVED` כנדרש, אבל אז גם ניסיון ההסלמה `TemporalViolation` נחסם (אין שורה `AwaitingHumanReview → HUMAN_REVIEW_REQUIRED`), ונשארות שתי שורות `Blocked`. זה בטוח — הפנייה נשארת ב־`AwaitingHumanReview` — אבל לא סירוב נקי. ה־guard מסרב לפני כל זה, עם הסיבה `human_engaged`.
3. **במבנה:** אף שורה (spec או הרחבה) לא יוצאת מ־`AwaitingPatientReply` למצב AI; `AwaitingPatientReply` לא ב־`ACTIVE_STATES`, כך שה־Orchestrator לא נוגע בו; תשובת המטופל נשמרת בסוג Data Log חדש (`patient_reply`) שה־Classifier לא קורא.

## 7. ההודעות

### 7.1 תבניות

מקור האמת הוא ה־backend (`hospital_agent/patient_messages.py`), וה־API מגיש אותן ל־UI (`GET /api/staff/message-templates`). כל תבנית היא טקסט קבוע ומאושר מראש; פרמטר, כשיש, נבחר **מרשימה סגורה** — אין בהן טקסט חופשי, ולכן אין בהן תוכן רפואי ואין צורך באישור תוכן.

| id | מטרה | טקסט | פרמטר |
|---|---|---|---|
| `clarify_general` | שאלה | לא הצלחנו להבין את פנייתך. נשמח אם תפרט/י במה נוכל לעזור. | — |
| `clarify_did_you_mean` | שאלה | האם התכוונת ל{topic}? נשמח לאישור או לפירוט. | `topic` ∈ {`appointment_time`: מועד התור, `required_documents`: המסמכים הנדרשים לתור, `preparation`: הוראות ההכנה לתור} |
| `clarify_appointment` | שאלה | האם הפנייה נוגעת לתור קיים? אם כן, נא לציין את התאריך או את המחלקה. | — |
| `document_request` | מסמך | נא להעלות את המסמך: {document}. | `document` = תווית הקטלוג של `requested_document` |
| `close_handled` | סיום | פנייתך טופלה על ידי הצוות. | — |
| `close_out_of_scope` | סיום | פנייתך אינה בתחום שהמערכת מטפלת בו. לשאלות אחרות ניתן לפנות למוקד. | — |
| `close_no_reply` | סיום | לא התקבלה תשובה בזמן, ולכן הפנייה נסגרה. אפשר לפתוח פנייה חדשה בכל עת. | — |

הטקסטים ניתנים לעריכה ע״י הבעלים; השינוי הוא בקובץ אחד.

### 7.2 מי שולח מה

| | תבנית | טקסט חופשי |
|---|---|---|
| שאלת הבהרה | כל איש צוות | רק `clinical_staff`, עם `ContentApproval` |
| בקשת מסמך | כל איש צוות (תבנית `document_request` בלבד) | — |
| הודעת סיום (resolve / reject) | כל איש צוות | רק `clinical_staff`, עם `ContentApproval` |

### 7.3 טקסט חופשי קליני

אותו מנגנון כמו התשובה הקלינית: הטקסט נרשם ב־Data Log, נכתבת שורת `executions` (`action = AnswerClinicalQuestion`, `status = succeeded`, `medical_content_flag = true`), ו־`ContentApproval` נקשר ל־`execution_id` + `action` + `content_hash`. האישור מועבר ב־payload כ־**`message_approval_id`** — מפתח נפרד מ־`content_approval_id` של התשובה הקלינית, כדי ששני הצרכנים לא יתערבבו (ההערה ב־state_manager מזהירה מזה במפורש). מאמת נפרד, `_patient_message_approval`, בודק הכל כמו `_clinical_answer_approval` **חוץ מ**הדרישה ל־`MedicalQuestion`, ומנצל את האישור באותה טרנזקציה. `_clinical_answer_approval` לא משתנה.

`AnswerClinicalQuestion` הוא הפעולה האנושית היחידה ב־§5 שמוסרת תוכן למטופל; שימוש חוזר בה חוסך פעולה חדשה ברשימה סגורה נוספת.

### 7.4 הודעת סיום

אופציונלית, על `resolve` ו־`reject` בלבד (`approve` ממשיך ל־AI ולא נושא הודעה; `approve` עם הודעה ← `409 message_not_allowed`). ההודעה נרשמת ב־Data Log, וה־`content_hash` שלה נכתב לשורת ה־Transition. התשובה הקלינית הקיימת נשארת כמו שהיא — מקרה פרטי של `resolve` עם טקסט חופשי ב־`MedicalQuestion`.

### 7.5 מה המטופל רואה

הודעת צוות מוצגת **רק** אם: ה־`content_hash` שלה נמצא בשורת Transition שנכנסה (committed) של הפנייה, **וגם** אחד מאלה: היא טקסט של תבנית (בדיקה דטרמיניסטית מול הקבוצה הסופית של תבניות × פרמטרים), או שקיים `ContentApproval` מנוצל לאותו `content_hash`. כל טקסט אחר לא מוצג — fail-closed, הצד הקורא של T6.

אותו כלל fail-closed חל גם על צד הקריאה של **תשובת המטופל** (`_patient_replies`): התשובה מוצגת
verbatim רק כשהוכח שהיא עונה לשאלה - יש `PATIENT_REPLY_REQUESTED` קודם עם הודעת צוות שעדיין
קיימת (לא tombstoned) ושאינה תבנית `document_request`. אחרת, אם צורתה כמו שורת-רפרנס של מסמך
("הועלה המסמך: ..."), מוצגת רק תווית סוג המסמך (גנרית כשהסוג אינו מוכר) - לעולם לא שורת הרפרנס
המקורית; טקסט שאינו מוכח וגם אינו בצורת שורת-רפרנס עדיין מוצג verbatim (אין ממה להסתיר אותו).

## 8. דדליין ו־SLA

- ברירת מחדל: `min(עכשיו + 24 שעות, appointment_at)` כשהתור ידוע, אחרת עכשיו + 24 שעות. איש הצוות יכול לשנות.
- אכיפה (ב־`reply_request_valid`, ובנוסף בבדיקת הקלט של ה־API): דדליין מפורש חייב להיות בעתיד, עד 7 ימים קדימה, ולא אחרי `appointment_at` כשהוא ידוע — אחרת `409 invalid_deadline`.
- כשה־`appointment_at` הידוע כבר עבר, הבקשה נדחית מיד, לפני חישוב הדדליין (גם ברירת המחדל לא יכולה להיות חוקית) — `409 appointment_passed`, קוד נפרד מ־`invalid_deadline` כדי שהצוות לא יחשוב שהזין דדליין שגוי.
- `repository.expired_patient_deadlines()` סורק גם את `AwaitingPatientReply`; `PatientSlaExpired` מקבל כל אחד משני המצבים. ה־SLA Worker לא משתנה.
- פנייה שחזרה בדדליין מסומנת בתור (`returned_by = reply_timeout`).

## 9. תשובת המטופל

- **לשאלה:** טקסט, 1–2000 תווים. נרשם ב־Data Log (`patient_reply`); `PATIENT_REPLY_SUBMITTED` עם ה־`content_hash`.
- **לבקשת מסמך:** PDF, דרך ה־intake הקיים של document-service (תת־פרויקט 13: קריא, של המטופל, בתוקף, לא כפילות). מתקבל כתשובה רק אם הסוג שסווג הוא `requested_document` (גם `DUPLICATE_DOCUMENT` מאותו סוג — המסמך כבר אצלנו). ה־PDF נשמר רק ב־S3, כמו היום; ב־Data Log נרשם תיאור (`document_type`, `document_ref`). מסמך שנדחה או מסוג אחר — אין אירוע, המטופל רואה את התוצאה ויכול לנסות שוב עד הדדליין.
- בקשת מסמך אפשרית רק כש־document-service מוגדר; אחרת `409 document_service_not_configured` כבר בשלב הבקשה.

## 10. ה־API

**צוות:**
- `GET /api/staff/message-templates` — התבניות (id, מטרה, טקסט, פרמטר ואפשרויותיו).
- `POST /api/staff/cases/{id}/request` — `{kind: question|document, template_id?, param?, text?, document_type?, deadline?, reason, shown_context_ref}`. `404 case_not_found`; `409 not_in_review`; `409 context_changed` כמו בהחלטה; `403 clinical_staff_only` לטקסט חופשי שלא מצוות קליני; `409 reason_required`; `409 invalid_request`; `409 message_required`; `409 invalid_template`; `409 unexpected_param`; `409 invalid_param`; `409 document_service_not_configured`; `409 invalid_deadline`; `409 appointment_passed` (§8); `422 invalid_body`.
- `POST /api/staff/cases/{id}/decision` — שדה אופציונלי חדש `message: {template_id, param?} | {text}` ל־`resolve` / `reject`, מאומת באותם כללים (אותם קודים כמו למעלה, `403 clinical_staff_only` ל־`text` שלא מ־`clinical_staff`); שני קודים משלה: `409 message_not_allowed` (הודעה עם `approve`), `409 human_engaged` (`approve` בפנייה שכבר נשלחה בה בקשה - `allowed_decisions` כבר משמיט אותו).
- `ReviewItem`: שדות חדשים `human_engaged` ו־`returned_by` (`patient_reply` | `reply_timeout` | null); `allowed_decisions` בלי `approve` כש־`human_engaged`.

**מטופל:**
- סטטוס חדש `needs_reply` (ל־`AwaitingPatientReply`), ושדות חדשים `reply_request` (`kind`, `message`, `document_type`, `deadline`) ו־`conversation` (הודעות צוות לפי §7.5 ותשובות המטופל, לפי הסדר). ל־`closed` מתווסף `message` כשיש הודעת סיום.
- `POST /api/patient/requests/{id}/reply` — `{text}`. `404 case_not_found`; `409 not_waiting_for_reply`; `409 reply_kind_mismatch`; `422 reply_too_long` (מעבר לגבול שה־schema כבר אוכף); `409 reply_not_accepted` לכל סירוב פנימי אחר (לא מוחזר למטופל כפי שהוא, §12.3).
- `POST /api/patient/requests/{id}/reply/file` — multipart PDF, בדיוק כמו ההעלאה של תת־פרויקט 13 (`404 file_upload_not_enabled` כש־document-service לא מוגדר, `413 too_large`, `422 invalid_body`); `404 case_not_found`; `409 not_waiting_for_reply`; `409 reply_kind_mismatch`; `503 document_service_unavailable`. מסמך שהתקבל אך אינו מהסוג המבוקש אינו שגיאת HTTP - אין אירוע, ותוצאת ההעלאה (`UploadResult`) מדווחת כרגיל כדי שהמטופל ינסה שוב עד הדדליין (§9).

כל השינויים ב־API הם תוספות; `docs/api.md` מתעדכן.

## 11. מסד הנתונים — מיגרציה 0006

- על `cases`: `human_engaged BOOLEAN NOT NULL DEFAULT false`, `reply_kind TEXT NULL`, `requested_document TEXT NULL`. `db.py` משקף אותן (`test_schema.py` בודק), ו־`CaseRecord` מקבל שלושה שדות עם ברירות מחדל. ל־`hospital_app` כבר יש `UPDATE` על `cases`.
- **שני CHECK constraints מורחבים** (נמצא ב־prototype — בלעדיהם ה־DB עצמו דוחה את הכתיבה): `ck_approvals_decision` מקבל גם `'request'`, ו־`ck_data_log_kind` מקבל גם `'staff_message'` ו־`'patient_reply'`. כל אחד נמחק ונוצר מחדש באותה מיגרציה; ה־downgrade מחזיר את הרשימה המצומצמת המקורית - אבל בתור `NOT VALID` (§18.4: הפרויקט לעולם לא מוחק נתונים, אז ה־downgrade לא רשאי לדחות ריצה רק כי כבר יש שורות עם הערכים החדשים; `NOT VALID` עדיין חוסם כתיבה חדשה של ערך שהוסר, ופשוט מדלג על בדיקת השורות הקיימות).
- **ה־downgrade מאבד מידע**: הוא גם מפיל את שלושת העמודות עצמן (`human_engaged`, `reply_kind`, `requested_document`). זו פעולת אופרטור בלבד ואינה חלק מזרימת עבודה רגילה - היא מוחקת את הסימון "לעולם לא חוזר ל־AI" מכל פנייה שאיש צוות אי־פעם כתב אליה, ואת צורתה של כל בקשה עדיין פתוחה. אין להריץ אותה כשיש פניות ב־`AwaitingPatientReply` או עם `human_engaged = true`.
- Additive בלבד; תרוץ על ה־RDS בעלייה הבאה.

## 12. ה־UI

- **מסך הסקירה של הצוות:** פאנל "בקשה מהמטופל" — סוג (שאלה / מסמך), תבנית ופרמטר, טקסט חופשי (מוצג רק ל־`clinical_staff`), סוג מסמך מהקטלוג, דדליין (`datetime-local`, עם `min`/`max` של היום ועד 7 ימים קדימה). ה־UI משאיר את השדה ריק כברירת מחדל ואינו ממלא אותו מראש - כשריק, אין `deadline` בבקשה כלל וברירת המחדל של השרת (§8) חלה. ל־resolve / reject — הודעת סיום אופציונלית, באותם כללים. כשהפנייה `human_engaged`, כפתור האישור לא מוצג (השרת לא מחזיר אותו).
- **תור ההסלמות:** סימון "תשובת מטופל התקבלה" / "לא נענתה בזמן" לפי `returned_by`.
- **מסך המטופל:** `needs_reply` — הודעת הצוות, הדדליין, ותיבת תשובה או בוחר PDF (הרכיב הקיים של תת־פרויקט 13); שרשור השיחה; הודעת סיום ב־`closed`.
- הטקסטים של התבניות מגיעים מהשרת; ה־UI לא מחזיק עותק.

## 13. מדדים (תת־פרויקט 14)

- A2: אריח חדש "ממתינות לתשובת מטופל" (`AwaitingPatientReply`); בלי זה הן נבלעות ב"בטיפול".
- B4: `PATIENT_REPLY_REQUESTED` נספר עם החלטות הצוות.
- B3: היציאה הראשונה מ־`AwaitingHumanReview` יכולה להיות עכשיו בקשה; המדד הופך ל"זמן עד הפעולה האנושית הראשונה", ומסמך ה־design של תת־פרויקט 14 מתעדכן בהתאם.

## 14. פרטיות (§12.3)

- תשובת המטופל ותוכן ההודעות נשמרים ב־Data Log בלבד (ניתנים למחיקה עם tombstone); ה־Audit מחזיק `content_hash` בלבד.
- המטופל לא רואה את ה־`escalation_kind`, את סיבת הבקשה (`reason`) או כל קוד פנימי; רק את ההודעה, הדדליין וסוג המסמך המבוקש.
- ה־Application Log לא מכיל תוכן הודעות, תשובות או `patient_id`.

## 15. בדיקות

- **FSM ושמות:** שלוש שורות ההרחבה; `resolve()` מוצא אותן; טסטי ה־spec (`test_naming`, `test_fsm`, `test_guards`) עוברים על החלק שמחוץ להרחבה; `test_extension.py` מקבע את ההרחבה.
- **"לעולם לא ל־AI":** `HUMAN_APPROVED` נחסם אחרי בקשה (guard ו־API); T13 חוסם כניסה למצב AI אחרי בקשה (trace סינתטי); זרימה אמיתית: בקשה ← תשובה ← ניסיון אישור נדחה ← סגירה.
- **ההודעות:** תבנית מכל איש צוות; טקסט חופשי מ־`admin_staff` נדחה; טקסט חופשי קליני דורש אישור תוכן ומנצל אותו; אישור לא תקין חוסם; המטופל רואה רק הודעות לפי §7.5.
- **דדליין ו־SLA:** ולידציה (עבר, מעבר ל־7 ימים, אחרי התור); SLA Worker מחזיר לתור בלי לשנות את `escalation_kind`.
- **תשובה:** טקסט; PDF מהסוג הנכון; סוג שגוי ודחייה לא מזיזים את הפנייה; מסמך בלי document-service נדחה בשלב הבקשה.
- **הוכחה שהליבה לא זזה:** `obs.golden` עדיין `35` / `4` / `54`, `policy.consistency` עדיין `7 abstract properties passed (9 UNSAT queries)`, `test_table_matches_spec_3_row_by_row` עדיין ירוק עם 41 שורות.
- **frontend:** הפאנל, הסתרת הטקסט החופשי ממי שאינו קליני, מסך `needs_reply`, השרשור.

## 16. מה לא משתנה

41 שורות §3, רשימות ה־spec של §2, §5, §13.2, `docs/spec/`, T1–T12, OPA / Prolog / Datalog, §9.2, ה־LLM וה־prompts, מסלול התשובה הקלינית ו־`_clinical_answer_approval`, ה־golden traces.

## 17. החלטות

1. מצב ואירועים ב־FSM, כהרחבה מסומנת (§3, §4).
2. אותו `StrEnum`, ורשימות `EXTENSION_*` שהטסטים מחסירים (§4).
3. "לעולם לא ל־AI" בשלוש שכבות: guard, T13, מבנה (§6).
4. `WorkflowDecisionValid` מוחמר ל־`HUMAN_APPROVED` במקום guard חדש בשורות ה־spec (§5.3).
5. `TIMEOUT_EXPIRED` ממוחזר, `PatientSlaExpired` מורחב, ו־X3 לא משנה את `escalation_kind` (§5.2, §8).
6. תשובת המטופל היא אירוע חדש ולא `DOCUMENT_UPLOADED`, בגלל T10 (§5.1).
7. טקסט חופשי קליני דרך `AnswerClinicalQuestion`, מפתח payload נפרד `message_approval_id` ומאמת נפרד (§7.3).
8. תבניות בשרת, עם פרמטרים מרשימות סגורות בלבד (§7.1).
9. הודעה מוצגת למטופל רק לפי §7.5, fail-closed.
10. בקשת מסמך רק כש־document-service מוגדר (§9).
11. דדליין: ברירת מחדל 24 שעות, עד 7 ימים, לא אחרי התור (§8).
12. `human_engaged` עמודה על הפנייה ולא נגזרת מה־trace — ה־guard, ה־API והמדדים קוראים אותה בזול (§11).

## 18. מחוץ להיקף

- התראה למטופל מחוץ למערכת (SMS / מייל) כשנשלחה אליו בקשה.
- כמה בקשות פתוחות במקביל באותה פנייה.
- מסמך שאינו מהקטלוג.
- החזרה יזומה של פנייה ל־AI אחרי מגע אנושי (הבעלים החליט: לעולם לא).
- שינוי ב־spec עצמו (ה־docx) — ההרחבה מתועדת כאן וב־`spec_corrections`.
- ביטול בקשה פתוחה ע״י הצוות (M7): אין מסלול לכך; הפנייה ממתינה לתשובת המטופל או לדדליין, לכל היותר 7 ימים.
