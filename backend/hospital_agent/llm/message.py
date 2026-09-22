"""The patient's status message (LLM design decision 8): a fixed template, filled only with
facts the State holds - the appointment time CheckAppointment returned (shown in Israel time),
the documents CheckDocuments listed and the patient uploaded, and the approved instruction source.
The LLM never writes it; the Response Evaluator still classifies it (INV-11).
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from ..case import CaseRecord
from ..documents import document_label
from ..policy.service import InstructionSource

# An appointment is kept in UTC (timestamptz) and is local to the hospital: the patient reads it
# in Israel time (sub-project 10, design §2.8).
CLINIC_TZ = ZoneInfo("Asia/Jerusalem")

# Kept as a single format string (rather than split further) because tests/test_d33.py formats
# it directly to reproduce the labelled set's status-template message.
TEMPLATE = (
    "התור שלך נקבע ל־{date} בשעה {time} (שעון ישראל). "
    "המסמכים הנדרשים: {documents} - כולם התקבלו. "
    "הוראות ההכנה המאושרות ({source_id}, גרסה {version}) זמינות לעיון באזור האישי."
)

# Used when `required_documents` is empty: the documents sentence is left out entirely rather
# than rendered with nothing after the colon.
TEMPLATE_NO_DOCUMENTS = (
    "התור שלך נקבע ל־{date} בשעה {time} (שעון ישראל). "
    "הוראות ההכנה המאושרות ({source_id}, גרסה {version}) זמינות לעיון באזור האישי."
)


def status_message(case: CaseRecord, source: InstructionSource) -> str:
    if case.appointment_at is None or case.required_documents is None:
        raise ValueError("the status message needs the appointment time and the document list")
    local = case.appointment_at.astimezone(CLINIC_TZ)
    date, time = local.strftime("%d/%m/%Y"), local.strftime("%H:%M")
    if not case.required_documents:
        return TEMPLATE_NO_DOCUMENTS.format(date=date, time=time, source_id=source.source_id,
                                            version=source.version)
    return TEMPLATE.format(
        date=date,
        time=time,
        documents=", ".join(document_label(code) for code in case.required_documents),
        source_id=source.source_id,
        version=source.version,
    )
