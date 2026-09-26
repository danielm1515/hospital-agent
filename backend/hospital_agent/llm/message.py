"""The patient's status message (LLM design decision 8): a fixed template, filled only with
facts the State holds - the appointment time CheckAppointment returned (shown in Israel time),
the exam type and department when the case has them (sub-project 18, design D10), the
documents CheckDocuments listed and the patient uploaded, and the approved instruction
source. The LLM never writes it; the Response Evaluator still classifies it (INV-11).
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from ..case import CaseRecord
from ..documents import document_label
from ..policy.service import InstructionSource

# An appointment is kept in UTC (timestamptz) and is local to the hospital: the patient reads it
# in Israel time (sub-project 10, design §2.8).
CLINIC_TZ = ZoneInfo("Asia/Jerusalem")

# Sub-project 18 (design D10): the Hebrew department label, matching the appointment-service's
# own catalog (app/catalog.py DEPARTMENTS). A department this version does not know (a future
# catalog addition) is dropped from the message entirely (fix round 1, M4) - never shown in
# English.
DEPARTMENT_LABELS: dict[str, str] = {
    "Cardiology": "קרדיולוגיה",
    "Dermatology": "עור",
    "Neurology": "נוירולוגיה",
    "Ophthalmology": "עיניים",
    "Orthopedics": "אורתופדיה",
}


def department_label(department: str) -> str | None:
    """The Hebrew label, or `None` for a department this version does not know - fix round 1
    (M4): an unknown department is dropped from the message entirely, never shown in English."""
    return DEPARTMENT_LABELS.get(department)


# Fix round 1 (M3): "ל" attaches straight onto a Hebrew word ("למבחן מאמץ"), but attached
# straight onto a non-Hebrew label it reads wrong ("לEEG") - such a label gets a maqaf instead
# ("ל־EEG"). א-ת is the Hebrew letter block (including the final forms, e.g. ך/ם/ן/ף/ץ).
def _exam_clause(label: str) -> str:
    if label and "א" <= label[0] <= "ת":
        return f"ל{label}"
    return f"ל־{label}"


# Fix round 1 (M4): "" when the department is not one this version's catalog knows (never the
# English value); " (<label>)" otherwise, leading space included so the template needs none.
def _department_clause(department: str) -> str:
    label = department_label(department)
    return f" ({label})" if label is not None else ""


# Kept as a single format string (rather than split further) because tests/test_d33.py formats
# it directly to reproduce the labelled set's status-template message. Used when the case has
# no exam type (the mock path) - the original structure, with the D10 wording change at the end.
TEMPLATE = (
    "התור שלך נקבע ל־{date} בשעה {time} (שעון ישראל). "
    "המסמכים הנדרשים: {documents} - כולם התקבלו. "
    "הוראות ההכנה המאושרות ({source_id}, גרסה {version}) מופיעות בפנייה זו."
)

# Used when `required_documents` is empty: the documents sentence is left out entirely rather
# than rendered with nothing after the colon.
TEMPLATE_NO_DOCUMENTS = (
    "התור שלך נקבע ל־{date} בשעה {time} (שעון ישראל). "
    "הוראות ההכנה המאושרות ({source_id}, גרסה {version}) מופיעות בפנייה זו."
)

# Sub-project 18 (design D10): the case has an exam type (a real appointment-service answer) -
# name the exam and the department alongside the appointment. `exam_clause` already carries its
# own leading "ל"/"ל־" (see `_exam_clause`), and `department_clause` its own leading space and
# parentheses, or "" (see `_department_clause`, fix round 1 M4) - the template adds neither.
TEMPLATE_WITH_EXAM = (
    "התור שלך {exam_clause}{department_clause} נקבע ל־{date} בשעה {time} (שעון ישראל). "
    "המסמכים הנדרשים: {documents} - כולם התקבלו. "
    "הוראות ההכנה המאושרות ({source_id}, גרסה {version}) מופיעות בפנייה זו."
)

TEMPLATE_WITH_EXAM_NO_DOCUMENTS = (
    "התור שלך {exam_clause}{department_clause} נקבע ל־{date} בשעה {time} (שעון ישראל). "
    "הוראות ההכנה המאושרות ({source_id}, גרסה {version}) מופיעות בפנייה זו."
)

# Sub-project 18 (design D10): the owner's multi-appointment follow-up. Appended (with a
# leading space) only when the case has more than one upcoming appointment - never when there
# is at most one, since choosing was then never even offered (design D5).
ADDITIONAL_APPOINTMENTS_NOT_CHOSEN = "יש לך תורים נוספים - אפשר לפתוח פנייה על תור מסוים."
ADDITIONAL_APPOINTMENTS_CHOSEN = "אם התכוונת לתור אחר, אפשר לפתוח פנייה חדשה ולבחור אותו."


def status_message(case: CaseRecord, source: InstructionSource) -> str:
    if case.appointment_at is None or case.required_documents is None:
        raise ValueError("the status message needs the appointment time and the document list")
    local = case.appointment_at.astimezone(CLINIC_TZ)
    date, time = local.strftime("%d/%m/%Y"), local.strftime("%H:%M")
    has_exam = case.exam_type_label is not None and case.department is not None
    if has_exam:
        exam_clause = _exam_clause(case.exam_type_label)
        department_clause = _department_clause(case.department)
        if not case.required_documents:
            text = TEMPLATE_WITH_EXAM_NO_DOCUMENTS.format(
                exam_clause=exam_clause, department_clause=department_clause, date=date, time=time,
                source_id=source.source_id, version=source.version,
            )
        else:
            text = TEMPLATE_WITH_EXAM.format(
                exam_clause=exam_clause, department_clause=department_clause, date=date, time=time,
                documents=", ".join(document_label(code) for code in case.required_documents),
                source_id=source.source_id, version=source.version,
            )
    elif not case.required_documents:
        text = TEMPLATE_NO_DOCUMENTS.format(date=date, time=time, source_id=source.source_id,
                                            version=source.version)
    else:
        text = TEMPLATE.format(
            date=date,
            time=time,
            documents=", ".join(document_label(code) for code in case.required_documents),
            source_id=source.source_id,
            version=source.version,
        )
    if case.upcoming_count is not None and case.upcoming_count > 1:
        sentence = ADDITIONAL_APPOINTMENTS_NOT_CHOSEN if case.appointment_id is None else ADDITIONAL_APPOINTMENTS_CHOSEN
        text = f"{text} {sentence}"
    return text
