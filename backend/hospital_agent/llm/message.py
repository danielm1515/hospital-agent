"""The patient's status message (LLM design decision 8): a fixed template, filled only with
facts the State holds - the appointment time CheckAppointment returned, the documents
CheckDocuments listed and the patient uploaded, and the approved instruction source. The
LLM never writes it; the Response Evaluator still classifies it (INV-11).
"""
from __future__ import annotations

from ..case import CaseRecord
from ..policy.service import InstructionSource

TEMPLATE = (
    "התור שלך נקבע ל־{date} בשעה {time} (UTC). "
    "המסמכים הנדרשים: {documents} - כולם התקבלו. "
    "הוראות ההכנה המאושרות ({source_id}, גרסה {version}) מצורפות להודעה זו."
)


def status_message(case: CaseRecord, source: InstructionSource) -> str:
    if case.appointment_at is None or case.required_documents is None:
        raise ValueError("the status message needs the appointment time and the document list")
    return TEMPLATE.format(
        date=case.appointment_at.strftime("%d/%m/%Y"),
        time=case.appointment_at.strftime("%H:%M"),
        documents=", ".join(case.required_documents),
        source_id=source.source_id,
        version=source.version,
    )
