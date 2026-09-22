"""The patient's status message (LLM design decision 8): a fixed template, filled only with
facts the State holds - the appointment time CheckAppointment returned (shown in Israel time),
the documents CheckDocuments listed and the patient uploaded, and the approved instruction source.
The LLM never writes it; the Response Evaluator still classifies it (INV-11).
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from ..case import CaseRecord
from ..policy.service import InstructionSource

# An appointment is kept in UTC (timestamptz) and is local to the hospital: the patient reads it
# in Israel time (sub-project 10, design §2.8).
CLINIC_TZ = ZoneInfo("Asia/Jerusalem")

TEMPLATE = (
    "התור שלך נקבע ל־{date} בשעה {time} (שעון ישראל). "
    "המסמכים הנדרשים: {documents} - כולם התקבלו. "
    "הוראות ההכנה המאושרות ({source_id}, גרסה {version}) זמינות לעיון באזור האישי."
)


def status_message(case: CaseRecord, source: InstructionSource) -> str:
    if case.appointment_at is None or case.required_documents is None:
        raise ValueError("the status message needs the appointment time and the document list")
    local = case.appointment_at.astimezone(CLINIC_TZ)
    return TEMPLATE.format(
        date=local.strftime("%d/%m/%Y"),
        time=local.strftime("%H:%M"),
        documents=", ".join(case.required_documents),
        source_id=source.source_id,
        version=source.version,
    )
