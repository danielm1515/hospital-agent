"""Build Hospital_Agent_Clean_v2.docx: the spec updated to the system as it was built.

The original Hospital_Agent_Clean.docx stays the binding demo spec and is only read here
(docs/spec/ is still generated from it by scripts/spec_to_md.py). This script copies it,
edits the paragraphs and table cells the built system differs from, each with a marker
that names its row in docs/spec_corrections.md, adds a front "version 2" block, a measured
verification section (15.1) and a final chapter (19) for sub-projects 9-18, then reopens
the result and checks it.

Never edited: the §3 transition table (41 rows) and every code listing (the §4 diagrams,
Rego §8, Z3 §9, Prolog §10, Datalog §11, the §15 traces). A correction that concerns them
is a note paragraph after them.

Usage (from repo root):  python scripts/build_spec_v2.py   (requires python-docx)
"""
import copy
import re
import sys
from pathlib import Path

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Hospital_Agent_Clean.docx"
OUT = ROOT / "Hospital_Agent_Clean_v2.docx"

NAVY = "1F3864"  # the original's table-header fill, reused for the version-2 markers
CODE_FONTS = {"Consolas", "Courier New"}
CHAPTER_TITLE = "19. תוספות מעבר לאפיון הדמו (תתי-פרויקטים 9–18)"
VERIFY_TITLE = "15.1 תוצאות אימות מדודות (גרסה 2)"
FRONT_TITLE = "גרסה 2 – מסמך מעודכן לפי המערכת שנבנתה"

# A Latin segment (rendered as an LTR run, like the original's split runs), or `forced` LTR.
LATIN = re.compile(
    r"`[^`]+`"
    r"|[A-Za-z][A-Za-z0-9_\-./:=+#@&*<>|{}\[\]\"'%,;?!$~^ ]*[A-Za-z0-9_\]}\"'%.?=/*>]"
    r"|[A-Za-z]"
)


def mark(rows) -> str:
    rows = str(rows)
    word = "תיקונים" if ("," in rows or "–" in rows) else "תיקון"
    return f" (עודכן בגרסה 2 – {word} מס' {rows})"


# ---------------------------------------------------------------- runs and paragraphs

def segments(text):
    """Split text into (chunk, is_latin) pieces; backticks force one LTR run."""
    out, pos = [], 0
    for m in LATIN.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        chunk = m.group(0)
        if chunk.startswith("`"):
            chunk = chunk[1:-1]
        out.append((chunk, True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return [(t, lat) for t, lat in out if t]


def mk_rpr(sz=22, bold=False, color=None, latin=False):
    rpr = OxmlElement("w:rPr")
    f = OxmlElement("w:rFonts")
    for a in ("w:ascii", "w:eastAsia", "w:hAnsi", "w:cs"):
        f.set(qn(a), "David")
    rpr.append(f)
    if bold:
        rpr.append(OxmlElement("w:b"))
        rpr.append(OxmlElement("w:bCs"))
    if color:
        c = OxmlElement("w:color")
        c.set(qn("w:val"), color)
        rpr.append(c)
    if sz:
        s = OxmlElement("w:sz")
        s.set(qn("w:val"), str(sz))
        rpr.append(s)
        s = OxmlElement("w:szCs")
        s.set(qn("w:val"), str(sz))
        rpr.append(s)
    r = OxmlElement("w:rtl")
    if latin:
        r.set(qn("w:val"), "0")
    rpr.append(r)
    return rpr


def mk_runs(text, sz=22, bold=False, color=None):
    runs = []
    for chunk, latin in segments(text):
        r = OxmlElement("w:r")
        r.append(mk_rpr(sz, bold, color, latin))
        t = OxmlElement("w:t")
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t.text = chunk
        r.append(t)
        runs.append(r)
    return runs


def para_sz(p_el, default=22):
    """The font size of the paragraph's last run, so appended text matches it."""
    for r in reversed(p_el.findall(qn("w:r"))):
        s = r.find(qn("w:rPr") + "/" + qn("w:sz"))
        if s is not None:
            return int(s.get(qn("w:val")))
    return default


def append_text(p_el, text, marker=None):
    """Append text (plain) and an optional marker (navy) to an existing paragraph."""
    sz = para_sz(p_el)
    for r in mk_runs(text, sz):
        p_el.append(r)
    if marker:
        for r in mk_runs(marker, sz, color=NAVY):
            p_el.append(r)


def mk_par(text="", style=None, prefix=None, keep_next=False, sz=22):
    """A new body paragraph set up like the original's (bidi, David, rtl runs)."""
    p = OxmlElement("w:p")
    ppr = OxmlElement("w:pPr")
    if style:
        ps = OxmlElement("w:pStyle")
        ps.set(qn("w:val"), style)
        ppr.append(ps)
    if keep_next or style in ("Heading1", "Heading2"):
        ppr.append(OxmlElement("w:keepNext"))
    if style in ("Heading1", "Heading2"):
        ppr.append(OxmlElement("w:keepLines"))
    ppr.append(OxmlElement("w:bidi"))
    sp = OxmlElement("w:spacing")
    if style == "Heading1":
        sp.set(qn("w:before"), "320")
        sp.set(qn("w:after"), "140")
    elif style == "Heading2":
        sp.set(qn("w:before"), "220")
        sp.set(qn("w:after"), "100")
    else:
        sp.set(qn("w:after"), "100")
    ppr.append(sp)
    p.append(ppr)
    if style == "Heading1":
        for r in mk_runs(text, 26, bold=True, color="000000"):
            p.append(r)
        return p
    if style == "Heading2":
        for r in mk_runs(text, 22, bold=True, color="000000"):
            p.append(r)
        return p
    if prefix:
        for r in mk_runs(prefix, sz, bold=True, color=NAVY):
            p.append(r)
    for r in mk_runs(text, sz):
        p.append(r)
    return p


def note(rows, text):
    return mk_par(text, prefix=f"הערת גרסה 2 (תיקונים {rows}): " if ("," in rows or "–" in rows)
                  else f"הערת גרסה 2 (תיקון {rows}): ")


def spacer():
    p = OxmlElement("w:p")
    ppr = OxmlElement("w:pPr")
    ppr.append(OxmlElement("w:bidi"))
    sp = OxmlElement("w:spacing")
    sp.set(qn("w:after"), "80")
    ppr.append(sp)
    p.append(ppr)
    return p


def insert_after(anchor, *new):
    for el in new:
        anchor.addnext(el)
        anchor = el
    return anchor


# ---------------------------------------------------------------- tables

def cell_text(tc) -> str:
    return "".join(t.text or "" for t in tc.iter(qn("w:t"))).replace("‎", "").replace("‏", "").strip()


def logical_cells(tr):
    """Cells in reading order: the original stores RTL tables right-to-left in XML."""
    return list(reversed(tr.findall(qn("w:tc"))))


def row_by_key(tbl, key, startswith=False):
    for tr in tbl.findall(qn("w:tr")):
        first = cell_text(logical_cells(tr)[0])
        if first == key or (startswith and first.startswith(key)):
            return tr
    raise KeyError(f"no table row keyed {key!r}")


def append_cell(tbl, key, col, text, marker, startswith=False):
    tc = logical_cells(row_by_key(tbl, key, startswith))[col]
    append_text(tc.findall(qn("w:p"))[-1], text, marker)


def set_cell(tc, text, sz=18, bold=False, color=None):
    ps = tc.findall(qn("w:p"))
    for extra in ps[1:]:
        tc.remove(extra)
    p = ps[0]
    for r in p.findall(qn("w:r")):
        p.remove(r)
    for r in mk_runs(text, sz, bold, color):
        p.append(r)


def add_row(tbl, values):
    """Append a data row styled like the table's last row; values in reading order."""
    last = tbl.findall(qn("w:tr"))[-1]
    tr = copy.deepcopy(last)
    for tc, v in zip(logical_cells(tr), values):
        set_cell(tc, v)
    last.addnext(tr)
    return tr


def mk_table(headers, rows, widths):
    """A new table in the original's style (borders, navy header, David 9pt); reading order."""
    w_all = sum(widths)
    tbl = OxmlElement("w:tbl")
    tblpr = etree.SubElement(tbl, qn("w:tblPr"))
    tw = etree.SubElement(tblpr, qn("w:tblW"))
    tw.set(qn("w:w"), str(w_all))
    tw.set(qn("w:type"), "dxa")
    borders = etree.SubElement(tblpr, qn("w:tblBorders"))
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = etree.SubElement(borders, qn(f"w:{side}"))
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), "4")
        b.set(qn("w:space"), "0")
        b.set(qn("w:color"), "auto")
    mar = etree.SubElement(tblpr, qn("w:tblCellMar"))
    for side in ("left", "right"):
        m = etree.SubElement(mar, qn(f"w:{side}"))
        m.set(qn("w:w"), "10")
        m.set(qn("w:type"), "dxa")
    look = etree.SubElement(tblpr, qn("w:tblLook"))
    for k, v in (("val", "04A0"), ("firstRow", "1"), ("lastRow", "0"), ("firstColumn", "1"),
                 ("lastColumn", "0"), ("noHBand", "0"), ("noVBand", "1")):
        look.set(qn(f"w:{k}"), v)
    grid = etree.SubElement(tbl, qn("w:tblGrid"))
    for w in reversed(widths):
        g = etree.SubElement(grid, qn("w:gridCol"))
        g.set(qn("w:w"), str(w))

    def row(values, header):
        tr = etree.SubElement(tbl, qn("w:tr"))
        trpr = etree.SubElement(tr, qn("w:trPr"))
        if header:
            etree.SubElement(trpr, qn("w:tblHeader"))
        etree.SubElement(trpr, qn("w:cantSplit"))
        for v, w in reversed(list(zip(values, widths))):
            tc = etree.SubElement(tr, qn("w:tc"))
            tcpr = etree.SubElement(tc, qn("w:tcPr"))
            tcw = etree.SubElement(tcpr, qn("w:tcW"))
            tcw.set(qn("w:w"), str(w))
            tcw.set(qn("w:type"), "dxa")
            if header:
                shd = etree.SubElement(tcpr, qn("w:shd"))
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:color"), "auto")
                shd.set(qn("w:fill"), NAVY)
            tcmar = etree.SubElement(tcpr, qn("w:tcMar"))
            for side, mw in (("top", "50"), ("left", "70"), ("bottom", "50"), ("right", "70")):
                m = etree.SubElement(tcmar, qn(f"w:{side}"))
                m.set(qn("w:w"), mw)
                m.set(qn("w:type"), "dxa")
            p = etree.SubElement(tc, qn("w:p"))
            ppr = etree.SubElement(p, qn("w:pPr"))
            etree.SubElement(ppr, qn("w:bidi"))
            jc = etree.SubElement(ppr, qn("w:jc"))
            jc.set(qn("w:val"), "right")
            for r in mk_runs(v, 18, bold=header, color="FFFFFF" if header else None):
                p.append(r)

    row(headers, True)
    for r in rows:
        row(r, False)
    return tbl


# ---------------------------------------------------------------- content

CHANGE_LOG = [
    ("מודל הנתונים (§1, §12.2, §12.3, §18.2)",
     "שש טבלאות במקום ארבע: approvals, data_log ו־patients; עמודות קישור ב־executions, "
     "action ו־outcome ב־audit_log ועמודות התור ב־cases (מיגרציות 0002 עד 0007).",
     "1, 2, 19, 29, 65–68, 83, 92"),
    ("מכונת המצבים (§2, §3, §3.1, §13.2)",
     "41 שורות הטבלה ללא שינוי. נוספה הרחבה מסומנת: AwaitingPatientReply, שני אירועים ושלוש "
     "שורות. הבהרות על צריכת אישורים, עיבוד מחדש, הסלמות ופורמטים נתמכים.",
     "1–9, 12, 17, 45, 83–85"),
    ("Policy, OPA ו־Z3 (§8, §9)",
     "הקוד ללא שינוי. מקור ההוראות נלקח מהפנייה, ורישום המקורות כולל את קטלוג הבדיקות; "
     "הדוגמה הנגדית נשמרת ב־policy_reasons; דד־ליין של 24 שעות למטופל.",
     "10, 11, 13–16, 18, 90, 91"),
    ("ביצוע ומערכות חיצוניות (§5, §12.2, §14)",
     "ExecutorReverified, recovery ושדות תוצאה מותרים. מערכת התורים ומערכת המסמכים האמיתיות "
     "מופעלות בתצורה בלבד; הבדיקות ותרחישי §0 נשארים על ה־Mock.",
     "20–27, 72–81, 90, 92"),
    ("LLM (§6.5, §18.5)",
     "ללא temperature (reasoning_effort=none); ספירת כשלים; מה ה־Planner רואה; תבנית הודעה "
     "קבועה בשעון ישראל שמציינת את הבדיקה ומפנה לפנייה.",
     "28, 30–38, 74, 93"),
    ("בקרה אנושית ואישורים (§12.4, §12.5, §18.3)",
     "IdP מדומה עם token חתום; shown_context_ref; תשובה קלינית עם ContentApproval; בקשות צוות "
     "למטופל; הנימוק של העובד נשאר פנימי.",
     "39–46, 58–64, 86–88"),
    ("ממשק המשתמש (§1)",
     "Vite + React + TypeScript, polling, העלאת מסמך, מסך מדדים, רשימת תורים ובחירת תור.",
     "47–52, 82, 89, 92"),
    ("D33 ואימות (§6.5, §15, §16)",
     "סף recall של 0.95, ונמדד 1.0000 (24/24) במודל החי; ה־golden traces 35/4/54 מופקים "
     "מהמערכת. התוצאות בסעיף 15.1.",
     "22, 24, 53–57, 74, 93"),
    ("תשתית (§18)",
     "Postgres מנוהל (AWS RDS) דרך תצורה בלבד, בלי שינוי בסכמה או בקוד.",
     "69–71"),
    ("תוספות מעבר לדמו (פרק 19)",
     "תתי־פרויקטים 9–18: מרשם מטופלים, שירות תורים, מסמכים, מדדים, בקשות צוות, רשימת תורים, "
     "תיקוני צוות, סוגי בדיקות והוראות הכנה.",
     "65–93"),
]

CHAPTER = [
    ("19.1 תת־פרויקט 9 – מרשם המטופלים", [
        "נוספה טבלה שישית, patients: מזהה, שם מלא וטלפון בפורמט E.164. מיגרציה 0004 ממלאת אותה "
        "בשלושת מטופלי הדמו. הטבלה נועדה למערכות אחרות, כדי שמערכת התורים תדע אילו מטופלים "
        "קיימים. שום רכיב בזרימת הסוכן אינו קורא ממנה, ו־DEMO_USERS נשאר ה־IdP של סעיף 18.3. "
        "בדיקה נכשלת אם הרשימה והטבלה אינן מסכימות. (תיקונים 65, 66, 68)",
        "מערכות אחרות קוראות דרך התפקיד hospital_reader: ‏SELECT על patients בלבד, בלי כתיבה, "
        "בלי טבלאות זמניות ועם CONNECTION LIMIT 5. תפקיד קיים בעל הרשאות־על נדחה (fail-closed). "
        "שירות התורים בודק כל מטופל מול המרשם: מטופל שאינו מוכר מקבל 404 patient_not_found, "
        "ומרשם שאינו זמין מחזיר 503. (תיקון 67)",
    ]),
    ("19.2 תת־פרויקט 10 – חיבור CheckAppointment לשירות התורים", [
        "כשמוגדרים APPOINTMENT_SERVICE_URL ו־APPOINTMENT_API_KEY, הפעולה CheckAppointment פונה "
        "לשירות התורים של הבעלים (GET /api/v1/patients/{patient_id}/appointment). בלי הגדרה נשאר "
        "MockGateway, ולכן הבדיקות, שלושת התרחישים וה־golden traces אינם משתנים. כתובת בלי "
        "מפתח, או כתובת שאינה `http(s)`, נדחית ו־Agent Orchestrator אינו עולה. (תיקון 72)",
        "מיפוי התשובות סגור. תור מתוזמן ועתידי הוא ok. ‏found=false, מטופל לא מוכר או הרשאה "
        "שגויה הם error, שמסלים כ־NonIdempotentFailure (סגירה או דחייה בלבד). אי־זמינות ו־timeout "
        "הם כשל זמני עם retry, וכל השאר הוא invalid_response. תור שבוטל או שעבר מטופל כמו תור "
        "חסר, כדי שלא יאושר למטופל כתור קרוב. ל־Audit נכנס קוד קבוע בלבד. redirect אינו נעקב, "
        "כדי שהמפתח לא יישלח לכתובת אחרת. תבנית ההודעה עברה לשעון ישראל. (תיקונים 73–76)",
    ]),
    ("19.3 תתי־פרויקטים 11–13 – מסמכים נדרשים ומערכת המסמכים", [
        "11: שירות התורים שומר לכל תור את סוגי המסמכים הנדרשים מקטלוג משותף (CBC, "
        "COAGULATION_TESTS, ECG, URINALYSIS, PREOP_SUMMARY), ו־CheckAppointment מחזיר אותם "
        "כ־required_documents. זו עובדה של מערכת התורים, ולכן השדה עבר מ־CheckDocuments "
        "ל־CheckAppointment. תשובה בלי רשימה תקינה היא invalid_response, והסוכן לעולם אינו "
        "מניח שאין צורך במסמכים. (תיקון 77)",
        "12: פרויקט נפרד, document-service, קולט את קובץ המטופל ובודק שהוא קריא, שאינו כפול, "
        "שה־LLM מסווג אותו כסוג מהקטלוג, שהוא שייך למטופל ושהוא בתוקף. רק מסמך שהתקבל נשמר, "
        "בדלי S3 פרטי. רשימת המסמכים של המטופל מחושבת מחדש בכל קריאה, כך שמסמך שפג תוקפו יוצא "
        "ממנה. 13: הפעולה CheckDocuments קוראת את הרשימה עם patient_id בלבד ומחזירה "
        "held_documents. (תיקון 78)",
        "ההעלאה עצמה עוברת דרך Session Service ולא דרך Tool Executor. זה חריג מתועד: זו פעולת "
        "המטופל ולא צעד בתוכנית, ולכן היא אינה מוצעת, אינה נבדקת ב־Policy ואינה חוזרת. מסמך "
        "שהתקבל נכנס ל־DOCUMENT_UPLOADED עם סוג הקטלוג כ־document_id ומזהה השירות כ־document_ref, "
        "והסוכן אינו קורא את תוכנו. מסמך שנדחה, שאינו נדרש או שכבר קיים אינו מייצר אירוע, "
        "והמטופל רואה את הסיבה בעברית. (תיקונים 79–81)",
    ]),
    ("19.4 תת־פרויקט 14 – מסך מדדים למנהל", [
        "המסלול GET /api/admin/metrics?from=&to= פתוח ל־admin_staff בלבד (require_admin), לחלון "
        "של עד 90 יום. הוא מחשב חמש קבוצות מדדים: זרימת פניות, עומס על הצוות, כלים חיצוניים, "
        "SLA מטופל ו־Policy. המדדים מחושבים מהטבלאות audit_log, executions, cases ו־approvals "
        "בטרנזקציה אחת מסוג REPEATABLE READ READ ONLY, כך שכל המספרים באים מאותו snapshot ואף "
        "פעם לא מוחזרת תשובה חלקית. המסך הוא `/staff/metrics`.",
        "המסך לקריאה בלבד. הכתיבה היחידה היא מיגרציה 0005, שמוסיפה שלושה אינדקסים. ה־FSM, "
        "ה־guards, נתיב הכתיבה של ה־Audit, מנועי ה־Policy וה־LLM לא השתנו. \"בוטלו\" מוצג "
        "כ\"נדחו על ידי הצוות\", כי HUMAN_REJECTED הוא הדרך היחידה ל־Failed. (תיקון 82)",
    ]),
    ("19.5 תת־פרויקט 15 – בקשות הצוות מהמטופל", [
        "איש צוות יכול לבקש מהמטופל הבהרה או מסמך אחד מהקטלוג, ולצרף הודעת סיום לסגירה או "
        "לדחייה. כל איש צוות יכול להשתמש בתבניות קבועות. טקסט חופשי מותר רק ל־clinical_staff, "
        "והוא כבול ל־ContentApproval ‏(message_approval_id), בדיוק כמו התשובה הקלינית. המטופל "
        "רואה הודעת צוות רק כשה־hash שלה נמצא בשורת Transition שנשמרה, וגם ההודעה היא תבנית או "
        "שה־ContentApproval שלה נוצל. (תיקונים 86, 88)",
        "זו התוספת היחידה למכונת המצבים, והיא מסומנת כהרחבה נפרדת: המצב AwaitingPatientReply, "
        "האירועים PATIENT_REPLY_REQUESTED ו־PATIENT_REPLY_SUBMITTED ושלוש שורות "
        "ב־fsm.EXTENSION_TRANSITIONS. הרשימות של סעיפים 2 ו־3 והבדיקות שלהן לא השתנו. תשובת "
        "המטופל, גם כשהיא מסמך, היא PATIENT_REPLY_SUBMITTED ולא DOCUMENT_UPLOADED, כדי ש־T10 לא "
        "יחזיר את הפנייה לסוכן. אם המטופל לא ענה, TIMEOUT_EXPIRED מחזיר את הפנייה לתור הצוות "
        "עם סוג ההסלמה המקורי. מיגרציה 0006 מוסיפה שלוש עמודות ל־cases ומרחיבה שני אילוצי "
        "CHECK. (תיקונים 83, 85, 87)",
        "פנייה שאיש צוות כתב בה למטופל לא חוזרת לסוכן לעולם. שלוש שכבות מבטיחות זאת: "
        "WorkflowDecisionValid דוחה HUMAN_APPROVED עם human_engaged; חוק T13 ב־Temporal Monitor, "
        "`G(AgentState -> ¬ O PATIENT_REPLY_REQUESTED)`; ואף שורה אינה מובילה "
        "מ־AwaitingPatientReply למצב שבו פועל הסוכן. (תיקון 84)",
    ]),
    ("19.6 תת־פרויקט 16 – רשימת התורים", [
        "המטופל ואיש הצוות רואים את תורי המטופל בטווח תאריכים: ברירת המחדל היא היום ועוד 30 "
        "יום, והמקסימום 366 יום. המסלולים הם GET /api/patient/appointments "
        "ו־GET /api/staff/cases/{case_id}/appointments, והרכיב המשותף AppointmentsPanel מוצג "
        "במסך \"הפניות שלי\" ובמסך הסקירה. שירות התורים מחזיר את כל התורים בטווח, מתוזמנים "
        "ומבוטלים, עד 100 תורים, עם הדגל truncated.",
        "הקריאה נעשית ב־appointment_list.py. זה חריג מתועד שני, לצד ההעלאה: קריאה בלבד, מחוץ "
        "ל־FSM. היא אינה מוצעת, אינה נבדקת ב־Policy, אינה אירוע ואינה נשמרת ב־Audit או ב־Data "
        "Log. כל שורה נבדקת (המטופל הנכון, זמן עם אזור זמן), ותשובה פגומה נדחית כולה כ־503 "
        "appointments_unavailable. בלי הגדרה המסלול מחזיר 404 appointments_not_enabled. אין "
        "רשימת Mock, כדי שלא יוצגו למטופל תורים מומצאים. (תיקון 89)",
    ]),
    ("19.7 תת־פרויקט 17 – תיקוני צוות", [
        "נראות ה־LLM: כל ניסיון נרשם כשורת קוד אחת, בלי תוכן (llm/telemetry.py). שגיאת ספק "
        "קבועה – הרשאה, מודל שאינו קיים או insufficient_quota – אינה נשלחת שוב, ומסלימה כמו "
        "קודם. `/health` מחזיר `llm: ok|error|unknown`, והמסלול GET /api/staff/system-status "
        "מזין באנר לצוות.",
        "קליטת מסמכים: כשל ספק ב־document-service מחזיר 503 classifier_unavailable ואינו נחשב "
        "\"קובץ לא קריא\". כל דחייה נושאת קוד reason קבוע, שהופך לסיבה מדויקת בעברית למטופל. "
        "תמונות JPEG ו־PNG וקובצי PDF סרוקים נקראים בקריאת vision. אם המודל אינו קורא תמונות, "
        "הקובץ נדחה (fail-closed).",
        "מסכי הצוות: כל רשימה היא קריאה אחת עם pagination ‏(`{items, next_cursor}`). ה־Case "
        "Monitor מסנן לפי חמש קבוצות המצבים של state_groups.STATE_GROUPS. תור ההסלמות ממוין "
        "לפי הכניסה האחרונה ל־AwaitingHumanReview, בשאילתת SQL אחת. רכיב טעינה אחד משמש את "
        "כל המסכים, והודעת ההחלטה נעלמת מעצמה. אף מנגנון בטיחות לא נחלש.",
    ]),
    ("19.8 תת־פרויקט 18 – סוגי בדיקות, הוראות הכנה לכל תור ובחירת התור", [
        "לכל תור בשירות התורים יש סוג בדיקה מקטלוג של 13 סוגים בחמש מחלקות. לכל סוג יש תווית "
        "בעברית, מסמכים מוצעים והוראת הכנה אחת, INSTR-<CODE>. הטקסטים הם טיוטות דמו שממתינות "
        "לאישור קליני. LoadInstructions קורא את הטקסט "
        "מ־GET /api/v1/instructions/{source_id}?version=, בלי אף שדה מטופל (§11), ובודק שהתשובה "
        "היא בדיוק המקור והגרסה שנתבקשו. בלי הגדרה נשאר מקור ה־Mock, וה־golden traces נשארים "
        "35/4/54. (תיקון 90)",
        "D15: בבדיקת ה־Safety החיה סווג INSTR-ORTHO-INJECTION כ־HighRisk ב־3 מתוך 3 ריצות, "
        "ו־INSTR-NEURO-EEG ב־1 מתוך 3; 11 הטקסטים האחרים היו MediumRisk ב־3 מתוך 3. שני הטקסטים "
        "נוסחו מחדש כטקסט לוגיסטי בלבד: שאלות על תרופות מופנות לרופא המפנה, ורשימת התרופות "
        "נמסרת בקבלה. שניהם עברו לגרסה 2 בקטלוג וב־Approved Source Registry. גרסה 1 של שניהם "
        "כבר אינה ברישום, ולכן היא נדחית (fail-closed). אחרי השינוי שניהם MediumRisk ב־3 מתוך 3 "
        "(8 מתוך 8 כולל ריצות המועמדים), בלי שום שינוי במסווג או ב־prompts.",
        "המטופל בוחר ב\"פנייה חדשה\" לאיזה תור מתוזמן הפנייה מתייחסת. appointment_id נשמר "
        "בפנייה פעם אחת, ב־REQUEST_SUBMITTED, ו־CheckAppointment שולח אותו רק כשנבחר תור. תור "
        "שאינו של המטופל, שבוטל או שעבר הוא not_found, ואף פעם לא מוחלף בתור אחר. המקור ש־OPA "
        "בודק ושמופיע בהודעה נלקח מהפנייה (מיגרציה 0007), ולא מה־Planner או מקבוע. פנייה בלי "
        "מקור נדחית. ההודעה מציינת את הבדיקה והמחלקה ומפנה לפנייה עצמה. (תיקונים 91–93)",
        "המטופל רואה את הטקסט המאושר במסך הפנייה שהושלמה. פאנל התורים טוען טקסט רק דרך "
        "GET /api/patient/instructions/{source_id}?version= (לצוות: `/api/staff/instructions/...`), "
        "אחרי בדיקה מול Approved Source Registry דרך OPA עצמו. מקור שאינו מאושר מחזיר 404 "
        "instruction_not_approved, ו־OPA שאינו זמין מחזיר 503 instructions_unavailable. מסך "
        "הפנייה מתעדכן ב־polling כל 5 שניות, אבל לא בסטטוסים completed ו־closed. (תיקון 89)",
    ]),
]


# ---------------------------------------------------------------- build

def build():
    d = docx.Document(str(SRC))
    els = list(d.element.body.iterchildren())

    def P(i, prefix):
        el = els[i]
        text = "".join(t.text or "" for t in el.iter(qn("w:t")))
        assert el.tag == qn("w:p") and text.replace("‎", "").startswith(prefix), (i, text[:60])
        return el

    def T(i, rows):
        el = els[i]
        assert el.tag == qn("w:tbl") and len(el.findall(qn("w:tr"))) == rows, i
        return el

    # -- front block, right after the title
    title = P(0, "Hospital Patient Agent")
    log = mk_table(["תחום", "סיכום", "שורות תיקון"], CHANGE_LOG, [2300, 5550, 2000])
    insert_after(
        title,
        mk_par(FRONT_TITLE, "Heading1"),
        mk_par("תאריך: 2026-09-26"),
        mk_par("גרסה זו משקפת את המערכת כפי שנבנתה בפועל: תתי־פרויקטים 1–8 מממשים את הדמו של "
               "סעיף 0, ותתי־פרויקטים 9–18 נוספו בבקשת הבעלים מעבר לו. המסמך המקורי, "
               "Hospital_Agent_Clean.docx, נשאר בריפו ללא שינוי כמפרט הדמו המחייב, וממנו נוצרת "
               "התיקייה `docs/spec`. כל שינוי כאן מפנה לשורה שלו ב־`docs/spec_corrections.md`: שינוי "
               "בהתנהגות מסומן בסוף הפסקה או התא ב\"(עודכן בגרסה 2 – תיקון מס' N)\", ופרשנות "
               "שאינה משנה התנהגות מופיעה כ\"הערת גרסה 2\"."),
        mk_par("קטעי הקוד (Rego בסעיף 8, Z3 בסעיף 9, Prolog בסעיף 10 ו־Datalog בסעיף 11), תרשימי "
               "סעיף 4, ה־traces של סעיף 15 ו־41 השורות של טבלת סעיף 3 נשארו מילה במילה, כי "
               "המימוש תואם להם אחד לאחד. תיקון שנוגע בהם מופיע כהערה אחריהם. התוספות שמעבר "
               "לדמו מרוכזות בפרק 19, והתוצאות המדודות בסעיף 15.1."),
        mk_par("טבלת השינויים:", keep_next=True),
        log,
        spacer(),
    )

    # -- §0
    append_text(P(6, "שלושת התרחישים משתמשים"),
                " בסביבת הבעלים CheckAppointment, CheckDocuments ו־LoadInstructions יכולים לפנות "
                "למערכות האמיתיות, אבל שלושת התרחישים, הבדיקות וה־golden traces רצים על ה־Mock.",
                mark("23, 72, 78, 90"))

    # -- §1 components
    comp = T(8, 11)
    for c in logical_cells(row_by_key(comp, "Postgres Database"))[1:2]:
        set_cell(c, "טבלאות cases, executions, audit_log ו־approvals לפי סעיף 18.2, ובנוסף "
                    "data_log (סעיף 12.3) ו־patients (פרק 19).")
        append_text(c.findall(qn("w:p"))[-1], "", mark("1, 29, 65"))
    append_cell(comp, "UI Component", 1,
                " מומש ב־Vite + React 18 + TypeScript ומוגש כשירות frontend ב־docker compose; "
                "המסכים מתעדכנים ב־polling. בנוסף: מסך מדדים למנהל ורשימת תורים.",
                mark("47, 50, 51, 82, 89"))
    append_cell(comp, "Tool Executor", 1,
                " שני חריגים מתועדים: Session Service מעביר את קובץ המטופל למערכת המסמכים, "
                "ומסכי התורים וההוראות קוראים ממערכת התורים לקריאה בלבד, מחוץ ל־FSM.",
                mark("79, 89"))
    append_cell(comp, "Human Review Service", 1,
                " מאפשר גם תשובה קלינית עם ContentApproval ובקשה מהמטופל (שאלה או מסמך).",
                mark("59, 83"))
    append_cell(comp, "SLA / Timer Worker", 1, " סורק גם את AwaitingPatientReply.", mark(85))

    # -- §2
    insert_after(T(14, 13), note("83",
        "שנים עשר ה־States נשארו רשימה סגורה. תת־פרויקט 15 הוסיף מחוץ לה, כהרחבה מסומנת, את "
        "AwaitingPatientReply: הצוות ביקש מהמטופל תשובה וממתין לה. המצב אינו פעיל לסוכן ואינו "
        "סופי. פירוט בסעיף 19.5."))
    insert_after(T(18, 27), note("83",
        "עשרים ושישה האירועים נשארו רשימה סגורה. ההרחבה מוסיפה שני אירועים חיצוניים: "
        "PATIENT_REPLY_REQUESTED (איש צוות, עם WorkflowDecision מסוג request) "
        "ו־PATIENT_REPLY_SUBMITTED (המטופל, דרך Session Service)."))

    # -- §3 (the 41-row table itself, element 22, is never touched)
    T(22, 42)
    append_text(P(26, "חידוש לאחר הסלמה"),
                " בפנייה שאיש צוות כתב בה למטופל, HUMAN_APPROVED נחסם לתמיד עם human_engaged, "
                "והפנייה לא חוזרת לסוכן.", mark(84))
    append_text(P(29, "כל מעבר, Audit"),
                " העיבוד מחדש מוגבל לשלוש פעמים; אחריהן ReprocessLimitExceeded ושום דבר אינו נשמר.",
                mark(2))
    append_text(P(32, "Safety Classifier בודק"),
                " safety_level רק עולה, ב־DATA_RETRIEVED ובסיווג מחדש. בדיקה חוזרת שאי אפשר לבצע "
                "מסלימה כ־ExecutionUnknown. בסיווג מחדש של מסמך שהתקבל נראית רק שורת ההפניה שלו, "
                "לא תוכנו.", mark("30, 80"))
    insert_after(
        P(33, "כשלי סיווג"),
        note("1, 3, 6, 8, 12, 17, 45",
             "41 השורות שלמעלה נשארו מילה במילה, והקוד משווה אליהן שורה אחר שורה. פרשנויות "
             "שנקבעו במימוש: אישור PolicyReview אינו נצרך ב־HUMAN_APPROVED אלא בהחלטת ה־Policy "
             "הבאה, ורק האישור שחידש את הפנייה בפועל; HUMAN_APPROVED מנקה את escalation_kind "
             "ו־escalated_from_state; Initial נשמר כ־state_before ריק; הפרה זמנית בזמן "
             "AwaitingHumanReview נחסמת בלי הסלמה נוספת; ואחרי אישור PatientVerificationFailed, "
             "Session Service שולח מחדש REQUEST_VALIDATED מהטקסט השמור."),
        note("83–85",
             "ההרחבה של תת־פרויקט 15 שמורה בנפרד מהטבלה (fsm.EXTENSION_TRANSITIONS) ומוסיפה שלוש "
             "שורות: AwaitingHumanReview עם PATIENT_REPLY_REQUESTED אל AwaitingPatientReply; "
             "AwaitingPatientReply עם PATIENT_REPLY_SUBMITTED אל AwaitingHumanReview; "
             "ו־AwaitingPatientReply עם TIMEOUT_EXPIRED אל AwaitingHumanReview. אף שורה אינה "
             "משנה את escalation_kind, ואף שורה אינה מובילה מהמצב החדש למצב שבו פועל הסוכן."),
    )

    # -- §3.1 guards
    guards = T(35, 28)
    append_cell(guards, "ApprovedSource", 1,
                " source_id ו־version נלקחים מהפנייה, לפי סוג הבדיקה של התור שנשלף, ולא "
                "מה־Planner או מקבוע; פנייה בלי מקור נדחית.", mark(91))
    append_cell(guards, "WorkflowDecisionValid", 1,
                " בנוסף, HUMAN_APPROVED נכשל עם human_engaged בפנייה שאיש צוות כתב בה למטופל, "
                "ואותו approval_id אינו יכול לחדש פנייה פעמיים.", mark("1, 84"))
    append_cell(guards, "DocumentValid", 1,
                " הפורמטים הנתמכים: pdf, jpg ו־png. כשמערכת המסמכים מוגדרת, היא מבצעת את הקליטה, "
                "ומסמך שנדחה אינו מייצר אירוע.", mark("5, 81"))
    append_cell(guards, "PatientSlaExpired", 1, " גם AwaitingPatientReply (הרחבה) נבדק באותו אופן.", mark(85))
    append_cell(guards, "ExecutorReverified", 3, " הפנייה מסלימה כ־ExecutionUnknown.", mark(20))

    # -- §5 action registry
    actions = T(86, 7)
    append_cell(actions, "CheckAppointment", 4, ". appointment_id נשלח רק אם המטופל בחר תור.", mark(92))
    append_cell(actions, "CheckAppointment", 5,
                " וגם required_documents. מול מערכת התורים גם מזהה התור, המחלקה, סוג הבדיקה "
                "ומקור ההוראות, שנשמרים בפנייה.", mark("77, 92"))
    append_cell(actions, "CheckDocuments", 5,
                " (held_documents בלבד, מהמסמכים שהתקבלו ותקפים היום)", mark("77, 78"))
    append_cell(actions, "LoadInstructions", 4, " המקור נלקח מהפנייה.", mark(91))
    append_cell(actions, "AnswerClinicalQuestion", 5,
                ". התשובה נשמרת ב־Data Log ומוצגת למטופל; היא אינה נשלחת דרך Tool Executor.",
                mark("59–64"))
    append_text(P(89, "מסמך חסר מתבקש"),
                " כשמערכת המסמכים מוגדרת, Session Service מעביר אליה את הקובץ שהמטופל מעלה. זה "
                "חריג מתועד לכלל שרק Tool Executor פונה למערכת חיצונית.", mark("49, 79"))
    insert_after(P(90, "Planner מציע רק"), note("27, 34, 35, 74, 93",
        "הודעת הסטטוס נכתבת מתבנית קבועה ומתמלאת רק בעובדות מה־State: זמן התור בשעון ישראל, "
        "סוג הבדיקה והמחלקה כשהם ידועים, המסמכים הנדרשים ומקור ההוראות המאושר. ה־Planner אינו "
        "רואה את טקסט הפנייה כשהוא מציע צעד, ותוצאה של כלי יכולה לקבוע רק את העובדות של אותה "
        "מערכת."))

    # -- §6
    insert_after(T(115, 13), note("84",
        "בהרחבה נוסף ל־Temporal Monitor החוק T13, באותו תחביר: "
        "`G(AgentState -> ¬ O PATIENT_REPLY_REQUESTED)`. AgentState הוא אחד משבעת המצבים שבהם "
        "פועל רכיב AI או Tool Executor. T1–T12 נשארו כפי שהם."))
    append_text(P(120, "T6 קושר אישור"),
                " הראיה ל־HumanAuthorized מצורפת להחלטת Policy Service ונשמרת בשדה guards של "
                "שורת POLICY_*; ראיה שמגיעה באירוע אחר אינה נקראת.", mark(14))
    append_text(P(129, "ההבטחה נוגעת"),
                " הסף שנקבע הוא `recall >= 0.95`. במדידה חיה על 48 הודעות (24 רפואיות) התקבל "
                "recall של 1.0000 (24/24). תשובה שהמעריך לא הפיק נספרת כ־false negative.",
                mark("53–57"))

    # -- §7
    append_text(P(134, "שומר את סדר האירועים"),
                " רשומות Blocked ורשומות התוצאה של ביצוע אינן חלק מה־trace.", mark("9, 22"))
    append_text(P(135, "לפני כל מעבר"), " בהרחבה נבדק גם T13.", mark(84))

    # -- §8 (Rego verbatim; note after the input/output table's paragraph)
    insert_after(P(311, "הטבלה מתארת מעברים"), note("13, 15, 16, 90, 91",
        "קוד ה־Rego שלמעלה רץ כפי שהוא ב־OPA 1.9.0. בקלט ל־OPA, attempt_count נלקח מה־State ולא "
        "מהקורא, ו־escalated_from_state של override מסוג PolicyReview הוא Planning. רישום "
        "המקורות המאושרים כולל את INSTR-PREP-COLONOSCOPY v3 של הדמו, שתי רשומות לבדיקות (אחת "
        "שפג תוקפה ואחת שעוד לא תקפה) ואת הוראת ההכנה של כל סוג בדיקה בקטלוג (INSTR-<CODE>), בגרסה "
        "שבקטלוג: גרסה 1, וגרסה 2 ל־INSTR-NEURO-EEG ול־INSTR-ORTHO-INJECTION (סעיף 19.8). "
        "מקור ההוראות נלקח מהפנייה; פנייה בלי מקור נדחית עם unapproved_instruction_source."))

    # -- §9
    append_text(P(314, "Z3 4.15.4"),
                " במימוש הפונקציה מחזירה את הפסיקה במקום לקבל audit כפרמטר, והרישום נעשה "
                "בטרנזקציית המעבר; כשל בכתיבה עדיין מונע Commit.", mark(10))
    append_text(P(354, "המקסימום החוקי"),
                " במימוש היא נשמרת ב־policy_reasons של שורת ההסלמה (z3:sat ו־z3_detail), ולא "
                "בשורה נוספת. הדד־ליין שניתן למטופל הוא 24 שעות מרגע הבקשה.", mark("10, 11"))
    append_text(P(356, "שבע תכונות מופשטות"), " התוצאה המדודה בסעיף 15.1.")

    # -- §10 (Prolog verbatim)
    insert_after(P(516, "InPlan חל על"), note("40",
        "משתמשי הצוות בדמו הם אלה שקובצי rules.pl כבר מרשים: coordinator_nurse ‏(clinical_staff) "
        "ו־admin_coordinator ‏(admin_staff). קובצי ה־Prolog רצים כפי שהם."))

    # -- §11 (Datalog verbatim)
    insert_after(P(581, "תוכן ההודעה היוצאת"), note("79, 89, 90, 92",
        "flows.dl ללא שינוי. instruction_system עדיין אינו מקבל אף שדה מטופל. "
        "ל־appointment_system נשלח appointment_id רק כשהמטופל בחר תור, כפי "
        "ש־`minimized(appointment_id, appointment_system)` מתיר. העלאת מסמך בידי המטופל וקריאת "
        "רשימת התורים למסך אינן צעדי תוכנית, ולכן הן אינן במודל. מה שמגיע ל־document_system "
        "בנתיב של Tool Executor נשאר patient_id ו־document_id."))

    # -- §12
    append_text(P(589, "Audit הוא append-only"),
                " ב־audit_log נוספו העמודות action ו־outcome; outcome ריק בשורות Transition "
                "ו־Blocked.", mark(2))
    append_text(P(592, "הקריאה החיצונית מחוץ"),
                " גם החלטה שהתקבלה ולא התחילה (intent) מסלימה ב־restart כ־ExecutionUnknown. ארבע "
                "הפעולות האוטומטיות אידמפוטנטיות.", mark("21, 26"))
    logs = T(594, 4)
    append_cell(logs, "Data Log", 2, ", הודעות יוצאות, ובהרחבה הודעות צוות ותשובות מטופל.",
                mark("29, 86"))
    append_cell(logs, "Data Log", 3,
                ". מומש כטבלה data_log: מחיקה משאירה tombstone שמנקה את content ושומר את "
                "content_hash; לאפליקציה אין הרשאת DELETE.", mark(29))
    append_cell(logs, "Application Log", 4,
                ". כשל ב־SLA Worker נרשם כסוג החריגה בלבד.", mark(25))
    insert_after(P(597, "case_id ו־patient_id מקשרים"), note("46, 58, 88",
        "המטופל רואה רק סטטוס מופשט (received, in_progress, needs_document, needs_reply, "
        "in_review, completed או closed) ואת ההודעות שנועדו לו. הוא אינו רואה escalation_kind, "
        "סיבת Policy, שורת Audit או את הנימוק הפנימי של העובד."))
    p603 = P(603, "PolicyReviewOverrideValid מאפשר")
    append_text(p603, " האישור נצרך בהחלטת ה־Policy הבאה, באותה טרנזקציה של מעבר POLICY_*, ורק "
                      "האישור שחידש את הפנייה בפועל יכול להיצרך.", mark("1, 12, 17"))
    insert_after(p603, note("18, 42, 44, 61, 86",
        "Policy Service טוען את רשומת האישור בעצמו, לפי מזהה. WorkflowDecision תקף שעה אחת. "
        "תשובה קלינית יוצרת שתי רשומות: ContentApproval על הטקסט ו־WorkflowDecision לסגירה. "
        "טקסט חופשי של הצוות למטופל כבול ל־ContentApproval באותו אופן (message_approval_id)."))
    append_text(P(608, "אישור תקף חייב"),
                " shown_context_ref הוא SHA-256 של ההקשר שהוצג לעובד; החלטה על הקשר שהשתנה "
                "נדחית עם context_changed, ושום דבר אינו נכתב.", mark(41))

    # -- §13.2
    append_text(P(618, "האירועים החיצוניים"),
                " ההרחבה מוסיפה שני אירועים חיצוניים: PATIENT_REPLY_REQUESTED מאיש צוות "
                "ו־PATIENT_REPLY_SUBMITTED מהמטופל דרך Session Service.", mark(83))
    append_text(P(620, "SLA / Timer Worker רושם"),
                " הוא סורק גם את AwaitingPatientReply; timeout שם מחזיר את הפנייה לתור הצוות "
                "בלי לשנות את escalation_kind.", mark(85))

    # -- §14 fail-closed
    fc = T(623, 29)
    append_cell(fc, "ExecutorReverified נכשל", 1, "; הפנייה מסלימה כ־ExecutionUnknown.", mark(20),
                startswith=True)
    for state, behaviour in [
        ("Agent Orchestrator עצמו נכשל",
         "שלוש שגיאות רצופות באותה פנייה מסלימות לפי סוג הכשל של ה־State." + mark(36)),
        ("כתובת מערכת התורים או המסמכים מוגדרת בלי מפתח API, או שאינה `http(s)`",
         "התצורה נדחית ו־Agent Orchestrator אינו עולה; `/health` מציין את הסיבה." + mark(72)),
        ("תשובה ממערכת התורים, המסמכים או ההוראות בצורה לא צפויה",
         "invalid_response והסלמה. תור שבוטל, שעבר או שאינו של המטופל הוא not_found ולא תחליף. "
         "ב־Audit נרשם קוד קבוע ולא הודעת השירות; redirect אינו נעקב." + mark("73, 75–78, 90")),
        ("לפנייה אין מקור הוראות מאושר",
         "Deny עם unapproved_instruction_source." + mark(91)),
        ("HUMAN_APPROVED בפנייה שאיש צוות כתב בה למטופל",
         "נדחה עם human_engaged; T13 חוסם כרשת ביטחון." + mark(84)),
        ("OPA אינו זמין בעת הצגת הוראות הכנה במסך",
         "503 instructions_unavailable. מקור שאינו מאושר: 404 instruction_not_approved." + mark(89)),
    ]:
        tr = add_row(fc, [state, behaviour])
        # the marker part of the new cell in navy, like every other marker
        tc = logical_cells(tr)[1]
        body, _, rows = behaviour.rpartition(" (עודכן בגרסה 2")
        set_cell(tc, body)
        append_text(tc.findall(qn("w:p"))[-1], "", " (עודכן בגרסה 2" + rows)

    # -- §15: note, and the measured-results section after the last trace line
    append_text(P(626, "ה־"),
                " במערכת שנבנתה ה־traces מופקים מהמערכת הרצה: python -m obs.golden מפיק 35, 4 "
                "ו־54 רשומות Audit לשלושת התרחישים, כמספרים שלהלן. רשומות התוצאה של ביצוע "
                "נכתבות באותה טרנזקציה של האירוע שאחריהן. התוצאות המדודות בסעיף 15.1.",
                mark("22, 24"))
    results = mk_table(
        ["בדיקה", "פקודה", "תוצאה מדודה"],
        [
            ["Golden traces (סעיף 15)", "`python -m obs.golden`",
             "35 / 4 / 54 רשומות Audit לתרחישים 1, 2 ו־3, כצפוי"],
            ["עקביות בין שכבות הבקרה (סעיף 9.2)", "`python -m hospital_agent.policy.consistency`",
             "7 תכונות מופשטות, 9 שאילתות UNSAT"],
            ["D33 – recall של Response Evaluator (סעיפים 6.5, 16)", "`python -m eval.d33 --live`",
             "1.0000 (24/24) במודל החי, מעל הסף 0.95; 2 false positives מתוך 24 הודעות "
             "תפעוליות; 0 תשובות לא שמישות"],
            ["חבילת הבדיקות של ה־backend", "`pytest`", "1543 passed, 1 skipped"],
        ],
        [3000, 3300, 3550],
    )
    last_trace = els[714]
    assert els[715].tag == qn("w:p") and "16." in "".join(t.text for t in els[715].iter(qn("w:t")))
    insert_after(
        last_trace,
        mk_par(VERIFY_TITLE, "Heading2"),
        mk_par("המספרים שלהלן נמדדו על המערכת שנבנתה. הפקודות רצות בקונטיינר ה־backend "
               "(`docker compose run --rm backend ...`), מול מסד הבדיקות.", keep_next=True),
        results,
        spacer(),
        note("56, 72, 74, 90, 93",
             "בלי הגדרת מערכות חיצוניות, שלושת התרחישים רצים על ה־Mock, וה־golden traces נשארים "
             "35/4/54 גם אחרי תתי־פרויקטים 9–18. מדידת D33 החיה נעשתה לפני שינויי הניסוח של "
             "תבנית ההודעה (שעון ישראל ושם הבדיקה) ולא הורצה מחדש: שינוי תווית, לא מדידה "
             "חדשה. הרצה עם FakeProvider (0.1667, 4/24) אינה המספר של המוצר ואינה מושווית לסף."),
    )

    # -- §16 tests
    tests = T(716, 36)
    append_cell(tests, "D33", 3, ". סף 0.95; נמדד 1.0000 (24/24).", mark("53, 54"))

    # -- §18
    append_text(P(728, "ארבע טבלאות ב־Postgres"),
                " במערכת שנבנתה נוספו שתי טבלאות, data_log (סעיף 12.3) ו־patients (פרק 19), "
                "ועמודות במיגרציות 0002 עד 0007, כמפורט בטבלה.", mark("19, 29, 65"))
    model = T(729, 5)
    append_cell(model, "1", 2,
                "; ובנוסף appointment_at; human_engaged, reply_kind, requested_document; "
                "appointment_id, answered_appointment_id, department, exam_type_label, "
                "instruction_source_id, instruction_version, upcoming_count.", mark("19, 83, 92"))
    append_cell(model, "2", 2,
                "; ובנוסף state_version, plan_hash, approval_id, content_hash, medical_content_flag.",
                mark(19))
    append_cell(model, "3", 2, "; ובנוסף action, outcome.", mark(2))
    for values in (
        ["5", "data_log",
         "entry_id, case_id, patient_id, kind, content, content_hash, created_at, deleted_at",
         "PK entry_id. FK case_id.",
         "request, document, instructions, outgoing ובהרחבה staff_message ו־patient_reply. "
         "tombstone מנקה את content; ל־role של האפליקציה אין DELETE." + mark(29)],
        ["6", "patients", "patient_id, full_name, phone, created_at", "PK patient_id.",
         "מרשם למערכות אחרות, לקריאה דרך hospital_reader בלבד; הסוכן אינו קורא ממנו."
         + mark("65–68")],
    ):
        tr = add_row(model, values)
        tc = logical_cells(tr)[4]
        body, _, rows = values[4].rpartition(" (עודכן בגרסה 2")
        set_cell(tc, body)
        append_text(tc.findall(qn("w:p"))[-1], "", " (עודכן בגרסה 2" + rows)
    insert_after(P(730, "כל מעבר הוא טרנזקציה אחת"), note("69–71, 82",
        "מסד הנתונים יכול לרוץ גם על Postgres מנוהל (AWS RDS), ב־TLS, דרך תצורה בלבד ובלי שינוי "
        "בסכמה או בקוד. מיגרציה 0005 מוסיפה שלושה אינדקסים לקריאות של מסך המדדים, וזו הכתיבה "
        "היחידה שלו."))
    append_text(P(732, "בדמו ה־IdP מדומה"),
                " במימוש: רשימת DEMO_USERS (שלושה מטופלים, איש צוות קליני ומנהל), סיסמת דמו "
                "משותפת ו־token חתום ב־HMAC-SHA256 לשמונה שעות. הזהות, התפקיד ו־patient_id "
                "נלקחים מה־token בלבד.", mark("39, 40, 48"))
    append_text(P(734, "מחיקת תוכן מ־Data Log"),
                " ב־data_log ה־tombstone מנקה את content ושומר את content_hash.", mark(29))
    p736 = P(736, "הדמו משתמש במודל אחד")
    temp_runs = [r for r in p736.findall(qn("w:r"))
                 if "".join(t.text or "" for t in r.iter(qn("w:t"))) == "temperature 0"]
    assert len(temp_runs) == 1
    anchor = temp_runs[0]
    for r in mk_runs("reasoning_effort=none (המודל אינו מקבל temperature 0)", 22):
        anchor.addnext(r)
        anchor = r
    p736.remove(temp_runs[0])
    append_text(p736, "", mark("28, 32"))
    note_185 = insert_after(p736, note("31–38",
        "ה־Intent Classifier מחזיר AppointmentPreparation, MedicalQuestion או Unsupported. כל "
        "תשובה לא שמישה (שגיאת API, timeout, JSON לא תקין או הפרת Schema) נספרת לשלושת הכשלים. "
        "ה־Planner רואה את טקסט הפנייה רק ביצירת התוכנית, ומקור ההוראות אינו מוצע על ידו. "
        "Response Evaluator רץ בתהליך נפרד, ותהליך שנפל מוחלף."))

    # -- chapter 19, after the last body paragraph (before sectPr)
    body_end = note_185
    chapter = [mk_par(CHAPTER_TITLE, "Heading1"),
               mk_par("תתי־פרויקטים 1–8 מממשים את הדמו של סעיפים 0–18. תתי־פרויקטים 9–18 נוספו "
                      "בבקשה מפורשת של הבעלים, מעבר להיקף הדמו, ונרשמו "
                      "ב־`docs/spec_corrections.md` (שורות 65–93). אף אחד מהם אינו משנה את "
                      "הרשימות של סעיף 2, את 41 השורות של סעיף 3, את קוד ה־Rego, ה־Prolog, "
                      "ה־Datalog וה־Z3 או את שלושת התרחישים. בלי הגדרת מערכות חיצוניות הכול "
                      "רץ על ה־Mock, וה־golden traces נשארים 35/4/54.")]
    for heading, paras in CHAPTER:
        chapter.append(mk_par(heading, "Heading2"))
        chapter.extend(mk_par(t) for t in paras)
    insert_after(body_end, *chapter)

    d.core_properties.title = "Hospital Patient Agent — מפרט הדמו, גרסה 2"
    d.core_properties.revision = (d.core_properties.revision or 1) + 1
    d.save(str(OUT))


# ---------------------------------------------------------------- verify

def is_code(p) -> bool:
    fonts = {r.font.name for r in p.runs if r.text.strip()}
    return bool(fonts) and fonts <= CODE_FONTS


def xml(el) -> bytes:
    return etree.tostring(el)


def verify() -> None:
    orig, v2 = docx.Document(str(SRC)), docx.Document(str(OUT))

    def headings(doc):
        return [p for p in doc.paragraphs if p.style.name.startswith("Heading")]

    h_o, h_v = headings(orig), headings(v2)
    assert len(h_v) >= len(h_o), (len(h_o), len(h_v))

    code_o = [xml(p._p) for p in orig.paragraphs if is_code(p)]
    code_v = [xml(p._p) for p in v2.paragraphs if is_code(p)]
    assert code_o == code_v, "a code listing changed"

    def s3(doc):
        return [t for t in doc.tables if len(t.rows) == 42 and len(t.columns) == 4]

    (t_o,), (t_v,) = s3(orig), s3(v2)
    assert xml(t_o._tbl) == xml(t_v._tbl), "the §3 table changed"
    rows_o = [[c.text for c in r.cells] for r in t_o.rows]
    rows_v = [[c.text for c in r.cells] for r in t_v.rows]
    assert rows_o == rows_v and len(rows_v) - 1 == 41

    texts = [p.text for p in v2.paragraphs]
    for needed in (FRONT_TITLE, CHAPTER_TITLE, VERIFY_TITLE):
        assert needed in texts, needed
    subs = [t for t in texts if t.startswith("19.") and not t.startswith("19. ")]
    assert len(subs) == 8, subs
    markers = sum(t.count("עודכן בגרסה 2") for t in texts) + sum(
        c.text.count("עודכן בגרסה 2") for t in v2.tables for r in t.rows for c in r.cells)
    notes = sum(t.startswith("הערת גרסה 2") for t in texts)
    print(f"ok: headings {len(h_o)} -> {len(h_v)}, code paragraphs {len(code_v)} identical, "
          f"§3 table 41 rows identical, markers {markers}, notes {notes}, chapter 19 with "
          f"{len(subs)} subsections, tables {len(orig.tables)} -> {len(v2.tables)}")


if __name__ == "__main__":
    build()
    verify()
    sys.exit(0)
