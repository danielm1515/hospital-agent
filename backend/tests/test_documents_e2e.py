"""Sub-project 13 end to end: required documents from the appointment system, held ones from the
document system, and the existing readiness flow deciding (design §5.4)."""
import json

from hospital_agent.execution.appointment_service import AppointmentServiceGateway
from hospital_agent.execution.document_service import DocumentServiceGateway
from hospital_agent.execution.http import HttpResponse
from hospital_agent.llm.orchestrator import Orchestrator
from hospital_agent.llm.provider import FakeProvider
from hospital_agent.naming import State
from hospital_agent.scripted import ScriptedAgents
from tests.test_orchestrator import _until


# Task 4 (D7/D8): CheckAppointment must resolve an approved instruction source (an approved
# registry entry, policy/data/approved_instruction_sources.json) or LoadInstructions is denied
# unapproved_instruction_source before ever reaching the instruction system - this fixture's
# appointment answer carries one, and the same transport answers the instructions endpoint too.
INSTRUCTION_SOURCE_ID, INSTRUCTION_VERSION = "INSTR-CARD-VISIT", "1"
INSTRUCTION_TITLE, INSTRUCTION_TEXT_BODY = "הכנה לביקור במרפאה קרדיולוגית", "רשימת תרופות מעודכנת."


def appointment(required):
    appointment_body = {"found": True, "appointment": {"appointment_at": "2030-10-03T10:30:00+03:00",
                                                        "status": "Scheduled", "required_documents": required,
                                                        "instruction": {"source_id": INSTRUCTION_SOURCE_ID,
                                                                       "version": INSTRUCTION_VERSION}}}
    instruction_body = {"source_id": INSTRUCTION_SOURCE_ID, "version": INSTRUCTION_VERSION,
                        "title": INSTRUCTION_TITLE, "text": INSTRUCTION_TEXT_BODY}

    def transport(url, headers, timeout):
        body = instruction_body if "/instructions/" in url else appointment_body
        return HttpResponse(200, json.dumps(body).encode())
    return transport


class Documents:
    def __init__(self, *types):
        self.types = list(types)

    def __call__(self, method, url, headers, body, timeout):
        docs = [{"document_id": f"DOC-{i}", "document_type": t, "document_date": "2030-09-15", "result": "ACCEPTED",
                 "valid_until": "2030-12-14", "uploaded_at": "2030-09-20T10:00:00+00:00"} for i, t in enumerate(self.types)]
        return HttpResponse(200, json.dumps({"documents": docs}).encode())


def agent_with(sm, app_engine, required, documents):
    gateway = AppointmentServiceGateway("http://appointments.test", "k", transport=appointment(required),
                                        fallback=DocumentServiceGateway("http://docs.test", "k", transport=documents))
    patient = ScriptedAgents(sm, app_engine)
    return patient, Orchestrator(sm, FakeProvider(), gateway)


def test_a_missing_required_document_is_asked_for(sm, app_engine):
    patient, agent = agent_with(sm, app_engine, ["CBC", "COAGULATION_TESTS", "ECG"], Documents("CBC"))
    try:
        patient.submit(); patient.validate()
        _until(agent, patient, State.AWAITING_PATIENT_INPUT)
        case = sm.load(patient.case_id)
        assert case.required_documents == ["CBC", "COAGULATION_TESTS", "ECG"]
        assert case.held_documents == ["CBC"]
    finally:
        agent.close()


def test_everything_held_goes_straight_on(sm, app_engine):
    patient, agent = agent_with(sm, app_engine, ["CBC", "ECG"], Documents("ECG", "CBC", "URINALYSIS"))
    try:
        patient.submit(); patient.validate()
        _until(agent, patient, State.COMPLETED)
    finally:
        agent.close()


def test_an_appointment_that_needs_nothing_needs_no_upload(sm, app_engine):
    patient, agent = agent_with(sm, app_engine, [], Documents())
    try:
        patient.submit(); patient.validate()
        _until(agent, patient, State.COMPLETED)
    finally:
        agent.close()
