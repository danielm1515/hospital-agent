# תת־פרויקט 14: מסך מדדי מערכת למנהלים — מסמך Design

תאריך: 2026-09-24. בקשה מפורשת של הבעלים, מחוץ לתרחישי §0 — כמו תת־פרויקט 9, נרשמת ב־`docs/spec_corrections.md` שורה 82.

תכונה של **קריאה בלבד**: אין בה כתיבה ל־DB מעבר למיגרציית אינדקסים, ואין בה נגיעה ב־FSM, ב־guards, בנתיב הכתיבה של ה־Audit, ב־OPA / Datalog / Z3 / Temporal Monitor או ב־LLM.

## 1. הדרישה, כפי שהוקלטה

"למנהל מערכת אמור להיות מסך Monitor שמשקף את מדדי המערכת" — עם הדוגמאות: כמה פניות בוטלו, כמה טופלו, כמה הועברו לגורם אנושי, כמה ניסיונות כלי היו, כמה קריאות לכלים חיצוניים הצליחו, כמה פניות עמדו ב־SLA, latency ממוצע לקריאה לכלי חיצוני, וכשלים של כלים חיצוניים כמו timeouts.

החלטות הבעלים בשיחה:

| שאלה | תשובה |
|---|---|
| אילו קבוצות מדדים | כל החמש: A זרימת פניות, B עומס על אנשים, C כלים חיצוניים, D SLA מטופל, E מדיניות |
| מי רואה | `admin_staff` בלבד |
| טווח זמן | טווח תאריכים חופשי |
| "בוטלו" — אין ביטול במערכת | להציג "נדחו ע״י צוות" |
| איך מחשבים | אגרגציית SQL לפי דרישה מהטבלאות הקיימות (גישה 1) |

## 2. מה קיים היום

- **`audit_log`** (§12.2) — כל מעבר, כל התחלת וסיום ביצוע, כל חסימה. `record_type` ∈ `Transition` / `ExecutionStarted` / `ExecutionSucceeded` / `ExecutionFailed` / `ExecutionUnknown` / `Blocked`; `event`, `state_before`, `state_after`, `policy_result`, `policy_reasons`, `guards` (JSONB), `attempt_number`, `retry_cycle`, `outcome`, `recorded_at`.
- **`executions`** — `action`, `status` (`started` / `succeeded` / `failed` / `unknown`), `started_at`, `finished_at` ([repository.py:260](../../../backend/hospital_agent/repository.py)), `attempt_number`, `retry_cycle`.
- **`cases`** — `state`, `intent`, `safety_level`, `escalation_kind` (הערך האחרון בלבד — נדרס בהסלמה חוזרת), `patient_deadline`, `created_at`.
- **`approvals`** — `decision`, `escalation_kind`, `reviewer_role`, `granted_at`.
- סיבת כשל של כלי חיצוני נכתבת ל־`policy_reasons` של שורת ה־outcome ([state_manager.py](../../../backend/hospital_agent/state_manager.py), `policy_reasons=[outcome.reason]`).
- ה־escalation_kind **אינו** נכתב לשורת ה־Transition של ה־Audit. בשורות הסלמה קבועות (`_fixed` ב־fsm.py) הוא נגזר מ־(event, state_before); בשורות `HUMAN_REVIEW_REQUIRED` הוא בא מה־payload ולא נשמר ב־Audit.
- המסלול היחיד ל־`Failed` הוא `AwaitingHumanReview --HUMAN_REJECTED--> Failed` ([fsm.py:196](../../../backend/hospital_agent/fsm.py)). לכן `Failed` שווה בדיוק "נדחו ע״י צוות".
- **הרשאות:** ב־[deps.py](../../../backend/hospital_agent/api/deps.py) יש רק `require_patient` / `require_staff`. התפקיד `admin_staff` קיים ב־[auth.py](../../../backend/hospital_agent/auth.py) (`admin_coordinator`), ואף route לא מגביל לפיו.
- אין שכבת אגרגציה. ב־`backend/obs/` יש רק `golden.py`.
- **אינדקסים:** אין על `audit_log.recorded_at`, `cases.created_at` או `executions.started_at`. אינדקסים נוצרים רק במיגרציות, לא ב־`db.py`. המיגרציה האחרונה היא 0004.
- **frontend:** התלויות היחידות הן `react`, `react-dom`, `react-router-dom`. אין ספריית גרפים.

## 3. הגישה

אגרגציית SQL לפי דרישה, מעל הטבלאות הקיימות, בטרנזקציית קריאה אחת.

היתרון המכריע: ה־`audit_log` הוא כבר מקור האמת של §12. מדד שנגזר ממנו **לא יכול לסתור את ה־Audit**.

נפסלו:
- **טבלת סיכומים יומית** — percentiles אינם ניתנים לחיבור (אי אפשר לגזור p95 של שבוע משבעה p95 יומיים), טווח חופשי מתנגש ברזולוציה יומית, ומצב כשל חדש (snapshot ישן).
- **Prometheus + Grafana** — counters מתחילים מאפס וההיסטוריה שב־Audit נזרקת; קונטיינרים חדשים ומערכת הרשאות נפרדת; המסך לא "בתוך המערכת".

## 4. המדדים

### 4.0 סמנטיקת החלון

- החלון הוא `[from, to)` — `from` כלול, `to` לא. שני הקצוות timezone-aware.
- **מדדי קוהורט** (A): הפניות **שנפתחו** בחלון (`cases.created_at ∈ [from, to)`), במצבן **הנוכחי**. עונים על "מה קרה לפניות של השבוע הזה".
- **מדדי אירוע** (B, C, D, E): האירועים **שקרו** בחלון (`audit_log.recorded_at`, `executions.started_at`). עונים על "כמה עבודה הייתה השבוע". כשמדד אירוע עוקב אחרי תוצאה (B3, D2), התוצאה נספרת גם אם קרתה אחרי `to` — החלון קובע מי נכנס, לא מתי הסתיים.
- המסך מציין ליד כל קבוצה איזו מהשתיים היא, כדי שמספרים מקבוצות שונות לא יושוו זה לזה בטעות.
- percentile על קבוצה ריקה הוא `null`, לא `0`.

### 4.1 A — זרימת פניות (קוהורט)

| מזהה | מדד | מקור |
|---|---|---|
| A1 | פניות שנפתחו | `count(cases)` |
| A2 | לפי מצב נוכחי: הושלמו (`Completed`), נדחו ע״י צוות (`Failed`), ממתינות לאדם (`AwaitingHumanReview`), ממתינות למטופל (`AwaitingPatientInput`), בטיפול (כל השאר) | `cases.state` |
| A3 | פילוח `intent` × `safety_level` (`null` = טרם סווגה) | `cases.intent`, `cases.safety_level` |
| A4 | זמן מקצה לקצה לפנייה שהושלמה — p50 / p95, בנפרד להשלמה אוטומטית (`CASE_RESOLVED`) ולסגירה אנושית (`HUMAN_RESOLVED_CASE`) | `cases.created_at` ← `recorded_at` של שורת ה־Transition עם `state_after = Completed` |

### 4.2 B — עומס על אנשים (אירוע)

| מזהה | מדד | מקור |
|---|---|---|
| B1 | כניסות ל־AwaitingHumanReview | שורות `Transition` עם `state_after = AwaitingHumanReview` |
| B2 | הסלמות לפי סוג | פתוחות: `cases.escalation_kind` כש־`state = AwaitingHumanReview`. הוכרעו: `approvals.escalation_kind` |
| B3 | זמן עד החלטה אנושית — p50 / p95 / מקסימום | לכל כניסה ל־AwaitingHumanReview, שורת ה־Transition **הבאה** של אותה פנייה עם `state_before = AwaitingHumanReview` (`LEAD` לפי `audit_id`). רק כניסות שהוכרעו |
| B4 | החלטות: אושרו (`HUMAN_APPROVED`) / נסגרו (`HUMAN_RESOLVED_CASE`) / נדחו ע״י צוות (`HUMAN_REJECTED`) | שורות `Transition` |
| B5 | תור פתוח **עכשיו**: גודל, וגיל הפנייה הוותיקה ביותר | `cases`, לא תלוי בחלון |

### 4.3 C — כלים חיצוניים (אירוע, `executions.started_at`)

| מזהה | מדד | מקור |
|---|---|---|
| C1 | קריאות לפי `action` × `status` (`succeeded` / `failed` / `unknown` / `started` שלא הסתיימה) | `executions` |
| C2 | אחוז הצלחה לכל `action` | C1 |
| C3 | latency לכל `action` — p50 / p95 / מקסימום, רק שורות שהסתיימו | `finished_at - started_at` |
| C4 | כשלים: `TOOL_TRANSIENT_FAILURE`, `RETRY_EXHAUSTED`, `ExecutionUnknown`, ופילוח סיבות (`timeout` / `unavailable` / ...) | `audit_log.event`, `record_type`, `policy_reasons` של שורות ה־outcome |
| C5 | ניסיונות חוזרים: קריאות עם `attempt_number > 1` | `executions` |
| C6 | מקור מוגדר: `appointments` ו־`documents` — `mock` או השירות האמיתי | `app.state`, כמו `/health`. תצורה בלבד — **לא** ping חי |

ה־latency ב־C3 מודד את כל קריאת ה־Tool Executor, לא HTTP נקי. ל־appointment-service יש `latency_ms` משלו — מקור שני, מחוץ להיקף (§13).

### 4.4 D — SLA מטופל (אירוע: כניסות ל־AwaitingPatientInput בחלון)

| מזהה | מדד | מקור |
|---|---|---|
| D1 | בקשות מסמך מהמטופל | שורות `Transition` עם `event = MISSING_INFORMATION_DETECTED` |
| D2 | עמדו בזמן / חרגו / עדיין פתוחות | היציאה הבאה מ־AwaitingPatientInput של אותה פנייה: `DOCUMENT_UPLOADED` ← `Classifying` = עמדה; `TIMEOUT_EXPIRED` = חרגה; אין = פתוחה |
| D3 | אחוז עמידה = עמדו / (עמדו + חרגו) | D2. פתוחות לא נספרות במכנה |

### 4.5 E — מדיניות (אירוע)

| מזהה | מדד | מקור |
|---|---|---|
| E1 | החלטות Policy: `POLICY_ALLOWED` / `POLICY_DENIED` / `POLICY_HUMAN_REVIEW_REQUIRED` | `audit_log.event` |
| E2 | חסימות (`Blocked`), לפי ה־guard שנכשל | `record_type = Blocked`, מפתחות `false` ב־`guards` |
| E3 | הסלמות של השכבות הפורמליות: `TemporalViolation`, `Z3Counterexample`, `PlanningFailed` | תת־קבוצה של B2 |

## 5. ה־API

```
GET /api/admin/metrics?from=<ISO-8601>&to=<ISO-8601>
```

- **הרשאה:** `require_admin` חדש ב־`deps.py` — `principal.role == ADMIN_STAFF`, אחרת `403 admin_only`. ללא token: `401` כרגיל.
- **ולידציה:** שני הפרמטרים חובה, ISO-8601 עם אזור זמן, `from < to`, `to - from ≤ 90 ימים`. אחרת `422 invalid_range` / `422 range_too_large`.
- **טרנזקציה אחת**, `REPEATABLE READ`, `READ ONLY`, עם `statement_timeout = 5s`. כל השאילתות רואות אותו snapshot, כך שמספרים בין קבוצות מסכימים זה עם זה. חריגה מהזמן ← `503 metrics_unavailable`. **לעולם לא מספרים חלקיים.**
- **התשובה:** אובייקט אחד — `range`, `generated_at`, ו־`flow` / `human_load` / `tools` / `patient_sla` / `policy`. סכמת Pydantic ב־`schemas.py`.
- endpoint אחד ולא חמישה: המסך טוען הכול יחד, סבב אחד, snapshot אחד.

## 6. מסד הנתונים

מיגרציה `0005_metrics_indexes`, additive בלבד:

- `ix_audit_log_recorded_at` על `audit_log (recorded_at)`
- `ix_cases_created_at` על `cases (created_at)`
- `ix_executions_started_at` על `executions (started_at)`

`CREATE INDEX` רגיל — הטבלאות קטנות. **שים לב:** `.env` מפנה ל־RDS, והמיגרציות רצות בעליית ה־backend, כך שהאינדקסים ייווצרו על ה־RDS ב־restart שאחרי ה־merge. אינדקסים נוספים רק אם `EXPLAIN` בשלב התוכנית מראה צורך.

## 7. מבנה ה־backend

| קובץ | מה |
|---|---|
| `hospital_agent/metrics.py` (חדש) | פונקציות שאילתה טהורות לכל קבוצה, dataclasses, ו־`compute(conn, window, sources) -> Metrics` |
| `hospital_agent/api/routes_admin.py` (חדש) | router עם `prefix="/api/admin"`, route אחד |
| `hospital_agent/api/deps.py` | `require_admin` |
| `hospital_agent/api/schemas.py` | `MetricsResponse` וסכמות המשנה |
| `hospital_agent/api/app.py` | `include_router(routes_admin.router)` |
| `alembic/versions/0005_metrics_indexes.py` (חדש) | §6 |

## 8. ה־frontend

- route חדש `/staff/metrics` בתוך `StaffRoutes`, ופריט ניווט "מדדי מערכת" שמוצג **רק** כש־`me.role === 'admin_staff'`. זו נוחות UX בלבד — השער האמיתי הוא `require_admin` בשרת.
- דף `Metrics.tsx`: שני שדות `datetime-local` (ברירת מחדל: 7 הימים האחרונים, בשעון ישראל), כפתור "רענן", וחמישה חלקים A–E.
- **בלי ספריית גרפים** — הפרויקט לא מחזיק תלויות UI מעבר ל־react ו־router. מספרים באריחים, התפלגויות כפסים אופקיים ב־CSS. percentiles מוצגים כמספרים.
- שגיאה: `Alert` "לא הצלחנו לטעון את המדדים" + "נסה שוב". `403` ← "אין הרשאה לצפות במדדים". מצב טעינה כמו ב־`MyRequests`.
- תוויות בעברית בקובץ תוויות אחד, בדומה ל־`staff/labels.ts`.
- `api/client.ts`: `getMetrics(from, to)`. `api/types.ts`: `Metrics`.

## 9. בדיקות

### backend (pytest, על `hospital_test`)

- כל מדד על שורות seed עם זמנים ידועים ← מספרים מדויקים.
- גבולות החלון: שורה בדיוק ב־`from` נספרת, בדיוק ב־`to` לא.
- p50 / p95 על קבוצה ידועה; קבוצה ריקה ← `null`.
- B3: שתי הסלמות באותה פנייה ← שני משכים נפרדים.
- D2: עמדה / חרגה / פתוחה, כל אחת בנפרד.
- API: `401` בלי token; `403` למטופל; `403` ל־`clinical_staff`; `200` ל־`admin_staff`; `422` על טווח חסר / הפוך / לא תקין / ארוך מ־90 יום.
- **פרטיות:** התשובה לא מכילה אף `patient_id`, `case_id` או טקסט פנייה מתוך ה־seed.
- **הוכחה שלא נגענו בשכבות הפורמליות:** `obs.golden` עדיין `35` / `4` / `54`, `policy.consistency` עדיין `7 abstract properties passed (9 UNSAT queries)`, `test_fsm.py::test_table_matches_spec_3_row_by_row` ירוק.

### frontend (Vitest)

- הדף מציג את חמשת החלקים מ־fixture.
- שינוי טווח קורא ל־API עם הפרמטרים הנכונים.
- שגיאה + נסה שוב; `403` ← הודעת הרשאה.
- פריט הניווט מוסתר ל־`clinical_staff` ומוצג ל־`admin_staff`.

## 10. מה לא משתנה

טבלת §3, ה־guards, נתיב הכתיבה של ה־Audit, OPA / Datalog / Z3 / Temporal Monitor, ה־golden traces, ה־LLM וה־prompts, וכל route קיים.

## 11. פרטיות (§12.3)

- התשובה מכילה אגרגטים בלבד — אין `patient_id`, `case_id`, טקסט פנייה או תוכן מסמך.
- אין דרישת גודל קבוצה מינימלי: `admin_staff` כבר רואה כל פנייה בנפרד ב"כל הפניות" (Case Monitor), כך שאגרגט לא חושף דבר שלא נחשף.
- הצפייה לא נרשמת ב־`audit_log` — `audit_log.case_id` הוא `NOT NULL` ו־FK, ומדדים אינם על פנייה. נרשמת שורת log אפליקטיבית: `user_id` של הצופה והטווח. בלי נתוני מטופל.

## 12. החלטות

1. אגרגציה לפי דרישה ולא rollups / Prometheus (§3).
2. endpoint אחד, טרנזקציית `REPEATABLE READ READ ONLY` אחת — snapshot עקבי (§5).
3. תקרת טווח 90 יום בשרת (§5).
4. `statement_timeout` של 5 שניות ← `503`, אף פעם לא תשובה חלקית (§5).
5. "נדחו ע״י צוות" = `Failed` = `HUMAN_REJECTED`, המסלול היחיד ל־`Failed` (§2).
6. סוג הסלמה: פתוחות מ־`cases`, מוכרעות מ־`approvals`. **לאמת בשלב התוכנית** שכל החלטה אנושית (`HUMAN_APPROVED` / `HUMAN_RESOLVED_CASE` / `HUMAN_REJECTED`) כותבת שורת `approvals` עם `escalation_kind`. אם לא — B2 נגזר לשורות הקבועות מ־(event, state_before), ושורות `HUMAN_REVIEW_REQUIRED` שלא הוכרעו מסומנות "לא ידוע", לא מנוחשות.
7. אינדקסים במיגרציה בלבד, כמו הדפוס הקיים (§6).
8. קוהורט מול אירוע, מסומן במסך (§4.0).
9. percentile על קבוצה ריקה הוא `null` (§4.0).
10. בלי ספריית גרפים (§8).
11. פריט הניווט מוסתר בצד הלקוח, השער הוא `require_admin` (§8).
12. אין שורת Audit לצפייה במדדים; log אפליקטיבי במקומה (§11).

## 13. מחוץ להיקף

- **מדדי LLM** — ניסיונות חוזרים, latency, tokens. `ask()` ([provider.py](../../../backend/hospital_agent/llm/provider.py)) לא שומר אותם היום; דורש נתיב כתיבה חדש. תת־פרויקט נפרד.
- **ping חי** לשירותים החיצוניים מהמסך.
- **`latency_ms` של appointment-service** כמקור שני ל־latency.
- ייצוא CSV, התראות, רענון אוטומטי.
- **ביטול אמיתי** של פנייה ע״י מטופל — שינוי בטבלת §3.
- **"טופל תוך X דקות"** — אין SLA כזה מוגדר באפיון.
