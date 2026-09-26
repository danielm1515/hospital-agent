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
# catalog addition) falls back to the English value it was given - never invented.
DEPARTMENT_LABELS: dict[str, str] = {
    "Cardiology": "קרדיולוגיה",
    "Dermatology": "עור",
    "Neurology": "נוירולוגיה",
    "Ophthalmology": "עיניים",
    "Orthopedics": "אורתופדיה",
}


def department_label(department: str) -> str:
    return DEPARTMENT_LABELS.get(department, department)


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
# name the exam and the department alongside the appointment.
TEMPLATE_WITH_EXAM = (
    "התור שלך ל{exam} ({department}) נקבע ל־{date} בשעה {time} (שעון ישראל). "
    "המסמכים הנדרשים: {documents} - כולם התקבלו. "
    "הוראות ההכנה המאושרות ({source_id}, גרסה {version}) מופיעות בפנייה זו."
)

TEMPLATE_WITH_EXAM_NO_DOCUMENTS = (
    "התור שלך ל{exam} ({department}) נקבע ל־{date} בשעה {time} (שעון ישראל). "
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
        department = department_label(case.department)
        if not case.required_documents:
            text = TEMPLATE_WITH_EXAM_NO_DOCUMENTS.format(
                exam=case.exam_type_label, department=department, date=date, time=time,
                source_id=source.source_id, version=source.version,
            )
        else:
            text = TEMPLATE_WITH_EXAM.format(
                exam=case.exam_type_label, department=department, date=date, time=time,
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
