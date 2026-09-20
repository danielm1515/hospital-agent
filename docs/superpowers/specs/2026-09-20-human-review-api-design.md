# תת־פרויקט 5: Human Review Service ו־API לכתיבה — מסמך Design

**תאריך:** 2026-09-20
**סטטוס:** נכתב במצב אוטונומי (הוראת המשתמש מ־2026-09-20, `CLAUDE.md` → *Autonomous mode*). כל ההחלטות נלקחו לפי ההמלצה ומתועדות בסעיף 8.
**תלוי ב:** תת־פרויקטים 1–4, שכבר מוזגו ל־main.
**מקור אמת:** `docs/spec/`: ‏§1 (UI Component, Session Service, Human Review Service), §3 ו־§3.1 (אירועי אדם, `WorkflowDecisionValid`), §12.4–§12.5 (אישורים), §13.2 (בעלות על אירועים), §18.3 (IdP מדומה), §18.4 (מחיקת מידע), D24. בנוסף הסעיף "Hand-off to sub-project 5" ב־`CLAUDE.md`.

## 1. היקף

- **IdP מדומה** (§18.3): רשימת משתמשים קבועה, כניסה וטוקן חתום.
- **Session Service:** הגשת פנייה, זיהוי המטופל, העלאת מסמך. הוא כותב ל־Data Log לפני האירוע, ומעיר את ה־Orchestrator.
- **Human Review Service** (§1, §12.4–§12.5): תור ההסלמות, הקשר שמוצג לעובד, והכרעה (approve, resolve או reject). ההכרעה יוצרת רשומת `WorkflowDecision` ושולחת את אירוע האדם.
- **API לכתיבה:** ממשק למטופל וממשק לצוות. ה־endpoints הקיימים של Case Monitor עוברים מאחורי אימות צוות.
- **חוזה API מתועד** (`docs/api.md`), שתת־פרויקט 6 (UI) בונה עליו.

**מחוץ להיקף:**
- ‏UI: תת־פרויקט 6.
- ‏D33: תת־פרויקט 7.
- ‏ContentApproval דרך ה־UI: אף אחד משלושת התרחישים של §0 לא צריך אותו. הודעה רפואית נחסמת כ־`PolicyDenied` (D8), והעובד סוגר את הפנייה ב־resolve. ראו החלטה 6.

## 2. מבנה

| יחידה | תפקיד |
|---|---|
| `hospital_agent/auth.py` | ה־IdP המדומה: `DEMO_USERS`, ‏`issue_token` / `verify_token` (HMAC-SHA256 עם `AUTH_SECRET`), ו־`Principal(user_id, role, patient_id)`. |
| `hospital_agent/session.py` | ‏`SessionService`: `submit_request`, `validate`, `upload_document` ו־`patient_view`. |
| `hospital_agent/human_review.py` | ‏`HumanReviewService`: `queue`, `context` (כולל `shown_context_ref`) ו־`decide`. |
| `hospital_agent/api/deps.py` | ‏FastAPI dependencies: `current_principal`, ‏`require_patient`, ‏`require_staff`. |
| `hospital_agent/api/routes_auth.py`, `routes_patient.py`, `routes_staff.py` | ה־routers. |
| `hospital_agent/api/app.py` | מחבר את ה־routers ואת CORS, ומקבל `orchestrator` מוזרק (לבדיקות). |
| `hospital_agent/scripted.py` | נשאר רק כעטיפה דקה שמפעילה את `SessionService` ו־`HumanReviewService`, כדי ש־`obs.golden` והבדיקות ישתמשו באותו קוד. |

## 3. IdP ואימות (§18.3)

- **המשתמשים הקבועים:**

  | user_id | תפקיד | הערה |
  |---|---|---|
  | `P-10041` | patient | מזוהה, המטופל של התרחישים |
  | `P-20000` | patient | מזוהה |
  | `P-30000` | patient | הזיהוי נכשל, למסלול `PatientVerificationFailed` |
  | `coordinator_nurse` | clinical_staff | |
  | `admin_coordinator` | admin_staff | |

  שמות הצוות זהים ל־`rules.pl` (§10).
- **סיסמה:** לכל משתמשי הדמו סיסמה אחת, `DEMO_PASSWORD`, עם ברירת המחדל `demo`. זה IdP מדומה, והדבר מתועד.
- **טוקן:** ‏`base64url(json{sub, role, exp}).hmac`, בחתימת `AUTH_SECRET` (ברירת מחדל לפיתוח, נקבע ב־`.env`), עם תוקף של 8 שעות. אין סשן בשרת.
- **מי קובע את הזהות:** השרת קובע את `reviewer_id` ואת `reviewer_role` מהטוקן, והלקוח לא יכול לשלוח אותם (§18.3). אותו דבר לגבי `patient_id` של המטופל.

## 4. Session Service

- **`submit_request(principal, text)`:**
  1. שולח `REQUEST_SUBMITTED` (מקור External) עם ה־`patient_id` מהטוקן.
  2. רושם את `request_text` ב־Data Log.
  3. שולח `REQUEST_VALIDATED` (מקור Session Service) עם `identity_verified` מתוך ה־IdP. למטופל שהזיהוי שלו נכשל נשלח `PATIENT_VERIFICATION_FAILED` במקום.
  4. מעיר את ה־Orchestrator.
- **`upload_document(principal, case_id, document)`:**
  - הפנייה חייבת להיות של המטופל. אחרת התשובה היא 404, בלי לחשוף שהפנייה קיימת.
  - נרשם ב־Data Log, וה־`content_hash` נכנס ל־payload.
  - אם המסמך לא העביר את הפנייה ל־Classifying, הוא נמחק ב־tombstone.
  - בסוף מעיר את ה־Orchestrator.
- **`patient_view(case)`:** מה שהמטופל רואה.
  - `status` הוא אחד מ: `received`, `in_progress`, `needs_document`, `in_review`, `completed`, `closed`.
  - ב־`needs_document`: ‏`missing_document_ids` ו־`missing_document_request_template_id="missing-document-v1"` (D24).
  - ב־`completed`: טקסט ההודעה שנמסרה, מה־Data Log.
  - `history` (נוסף 2026-09-20 לבקשת המשתמש, אחרי שציר הזמן במסך לא איפשר להבין מה קרה ומתי): כל שינוי סטטוס לפי הסדר, עם הזמן שבו הפנייה נכנסה אליו. נגזר משורות ה־Transition של ה־Audit דרך אותה פונקציית מיפוי של `status`, ולכן מכיל אך ורק את אותם שישה ערכים מופשטים — לא State, לא אירוע ולא סיבה.
  - המטופל לא רואה אף פעם סוג הסלמה, סיבות או Audit.

## 5. Human Review Service (§12.4–§12.5)

- **`queue()`:** הפניות ב־AwaitingHumanReview. לכל אחת: `escalation_kind`, ‏`escalated_from_state`, הסיבות (`policy_reasons` של שורת ההסלמה), ו־`allowed_decisions`.
  - `approve` מופיע רק ב־5 הסוגים שאפשר לחדש (טבלת §3).
  - `resolve` ו־`reject` מופיעים תמיד.
  - אם ה־approve דורש שדה, השדה מופיע ב־`required_fields`.
- **`context(case_id)`:** מה שמוצג לעובד.
  - תוכן ה־Data Log שלא נמחק: הפנייה, מסמכים שהתקבלו, ההוראות וההודעה היוצאת.
  - ה־trace מה־Audit.
  - `shown_context_ref = sha256(JSON קנוני של ההקשר)`.
- **`decide(principal, case_id, decision, reason, shown_context_ref, verified_identity_ref?, patient_deadline?)`:**
  1. מחשב מחדש את ההקשר. אם ה־ref לא תואם, התשובה היא 409 `context_changed`: העובד ראה משהו אחר ממה שקיים עכשיו.
  2. יוצר `WorkflowDecision`:
     - `reviewer_id` ו־`reviewer_role` מהטוקן;
     - `granted_at=now` ו־`valid_until=now+1h`;
     - `escalation_kind` מה־case;
     - ב־PolicyReview גם `plan_hash` ו־`current_step`.
  3. שולח `HUMAN_APPROVED`, ‏`HUMAN_RESOLVED_CASE` או `HUMAN_REJECTED` (מקור External) עם ה־`approval_id`.
  4. אם האירוע נחסם, ההכרעה נדחית עם הסיבה (למשל `patient_deadline_missing`). הרשומה נשארת לא מנוצלת, בתוקף קצר.
  5. אחרי `HUMAN_APPROVED` על `PatientVerificationFailed` הפנייה חוזרת ל־Received, ו־Session Service שולח שוב `REQUEST_VALIDATED` מטקסט הפנייה שב־Data Log.
  6. בסוף מעיר את ה־Orchestrator.
- **מחיקת תוכן (§18.4):** ‏`tombstone(principal, case_id, entry_id)`, לצוות בלבד. ה־Audit לא משתנה.

## 6. ה־API

כל הנתיבים תחת `/api`. ‏`/health` נשאר ציבורי.

| Method | Path | מי | תיאור |
|---|---|---|---|
| POST | `/api/auth/login` | כולם | `{user_id, password}` → `{token, role, user_id, display_name}` |
| GET | `/api/auth/me` | מחובר | הזהות מהטוקן |
| POST | `/api/patient/requests` | patient | `{text}` → ‏`patient_view` |
| GET | `/api/patient/requests` | patient | הפניות שלי |
| GET | `/api/patient/requests/{case_id}` | patient | ‏`patient_view` |
| POST | `/api/patient/requests/{case_id}/documents` | patient | `{document_id, format, content}` → ‏`patient_view` |
| GET | `/api/staff/cases` (`?state=`) | staff | רשימה (הייתה `/cases`) |
| GET | `/api/staff/cases/{case_id}` | staff | פרטים (הייתה `/cases/{id}`) |
| GET | `/api/staff/cases/{case_id}/audit` | staff | Audit (הייתה `/cases/{id}/audit`) |
| GET | `/api/staff/reviews` | staff | תור ההסלמות |
| GET | `/api/staff/cases/{case_id}/context` | staff | ההקשר ו־`shown_context_ref` |
| POST | `/api/staff/cases/{case_id}/decision` | staff | ההכרעה |
| DELETE | `/api/staff/cases/{case_id}/data/{entry_id}` | staff | ‏tombstone |

- **‏CORS:** מתיר את `http://localhost:5173` ו־`http://127.0.0.1:5173` (שרת הפיתוח של ה־UI), ואת `CORS_ORIGINS` מההגדרות.
- **שגיאות:** 401 בלי טוקן תקף, 403 כשהתפקיד לא מתאים, 404 כשהפנייה לא קיימת או לא של המטופל, 409 כשאירוע נחסם או כשההקשר השתנה. הגוף תמיד `{"detail": "<code>"}`.

## 7. בדיקות

- **יחידה:**
  - ‏`auth`: חתימה, תוקף, זיוף וסיסמה שגויה.
  - ‏`patient_view`: כל State מול ה־status.
  - ‏`allowed_decisions`: לכל סוג הסלמה.
  - ‏`shown_context_ref`: דטרמיניסטי.
- **API מקצה לקצה** (TestClient, engine מוזרק, `Orchestrator` עם `FakeProvider` שהבדיקה מריצה):
  - תרחיש 1: מטופל מגיש, מקבל `needs_document` עם `blood_test`, מעלה, ומקבל `completed` עם ההודעה.
  - תרחיש 2: שאלה רפואית מופיעה בתור, והעובד סוגר ב־resolve.
  - תרחיש 3: כשל טכני מופיע בתור, העובד מאשר, והפנייה מגיעה ל־`completed`.
  - ‏`P-30000`: הזיהוי נכשל, העובד מאשר עם `verified_identity_ref`, והפנייה ממשיכה.
- **הרשאות:**
  - מטופל לא רואה פנייה של מטופל אחר ולא ניגש ל־`/api/staff`.
  - צוות לא מגיש פניות.
  - החלטה עם `shown_context_ref` ישן מקבלת 409.
  - ‏approve על MedicalQuestion מקבל 409, כי זה לא transition חוקי.
- **Golden:** ‏`obs.golden` ממשיך להדפיס 35/4/54 שורות audit. ‏`scripted` עובר דרך השירותים החדשים.

## 8. החלטות (אוטונומיות, לפי ההמלצה)

1. **IdP:** רשימה קבועה, סיסמת דמו אחת (`DEMO_PASSWORD`) וטוקן HMAC. אין תלות חדשה.
2. **שמות הצוות** לקוחים מ־`rules.pl`: ‏`coordinator_nurse` ו־`admin_coordinator`.
3. **‏`shown_context_ref`** הוא hash של ההקשר שהוצג. הכרעה על הקשר שהשתנה נדחית.
4. **תוקף WorkflowDecision:** שעה. הרשומה נצרכת מיד באירוע.
5. **Case Monitor מאחורי אימות צוות:** הנתיבים הציבוריים `/cases*` מוסרים, כי חשפו `patient_id` ומצב פנייה בלי אימות.
6. **ContentApproval לא נחשף ב־API:** הוא לא נדרש בתרחישים. הודעה רפואית נסגרת ב־resolve.
7. **אימות מחדש:** אחרי אישור `PatientVerificationFailed`, ה־Session Service שולח שוב `REQUEST_VALIDATED` אוטומטית.
8. **המטופל רואה status מופשט בלבד**, בלי פרטי הסלמה.
9. **תהליך:** בגלל מצב אוטונומי התוכנית מגדירה ממשקים ובדיקות מפורטים, אבל לא קוד מלא כמו בתת־פרויקטים 3–4. המימוש נעשה על ידי subagents עם סקירה לכל משימה וסקירה כוללת בסוף.
10. **גוף שגיאה אחיד:** כל שגיאה היא `{"detail": "<code>"}` עם קוד קצר. לכן גם ה־404 של Case Monitor, שהיה `"case not found"`, הוא עכשיו `case_not_found` (משימה 3).
11. **‏`decision` ו־`reason` הם מחרוזות בסכמה**, לא enum: מי ששופט אותם הוא ה־Human Review Service, וקוד הסיבה שלו (`invalid_decision`, `reason_required`) מגיע לעובד כ־409 במקום כשגיאת סכמה. שדות זהות בגוף הבקשה (`reviewer_id`, `reviewer_role`) פשוט נעלמים (§18.3).
12. **העלאת מסמך היא טקסט** (`content`, ‏1–20000 תווים) ו־`format` מוגבל ל־`pdf`/`jpg`/`png`. אין העלאת קבצים בינריים בדמו, ואורך חסום מגן על ה־Data Log.
13. **‏Orchestrator מוזרק לא מופעל אף פעם**: ‏`create_app` שומר אותו כמו שהוא, ו־`_wake()` קורא את `app.state.orchestrator` בזמן הקריאה. כך בדיקה מריצה `run_case` בעצמה, ובשרת אמיתי אותו `_wake` מעיר את ה־Orchestrator הרץ.
14. **‏422 אחיד:** ‏`RequestValidationError` מוחזר כ־`{"detail": "invalid_body"}`. גוף ברירת המחדל של FastAPI מחזיר את הערך שנכשל (`input`) - סיסמה, טקסט פנייה או מסמך שלם - וזה מנוגד ל־§12.3. ה־UI מוודא את הטופס בעצמו לפי `docs/api.md` (סבב תיקונים 1, פריט 2).
15. **למטופל קוד אחד:** הגשה שנחסמה מחזירה `409 request_rejected`. קוד הסיבה של ה־guard הוא פנימי ונשאר בשרת (שורת ה־Blocked ב־Audit ויומן היישום) (סבב תיקונים 1, פריט 4).
