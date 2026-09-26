"""Sub-project 10 through the real Tool Executor and State Manager (design §4)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from hospital_agent import data_log
from hospital_agent.execution.appointment_service import AppointmentServiceGateway, HttpResponse
from hospital_agent.naming import EscalationKind, State
from tests.driver import Driver

AT = "2026-10-03T10:30:00+03:00"
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)  # before AT, fixed so the test never expires


class ScriptedTransport:
    def __init__(self, *answers: HttpResponse):
        self.answers, self.urls = list(answers), []

    def __call__(self, url, headers, timeout):
        self.urls.append(url)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def answer(status, body):
    return HttpResponse(status, json.dumps(body).encode())


def driver(sm, app_engine, *answers):
    transport = ScriptedTransport(*answers)
    gw = AppointmentServiceGateway("http://appointments.test", "k", transport=transport, clock=lambda: NOW)
    d = Driver(sm, app_engine, gateway=gw)
    d.to_classified()
    d.plan()
    return d, transport


def reasons(d):
    return [reason for row in d.trace() for reason in (row.policy_reasons or [])]


def test_a_found_appointment_is_the_one_the_case_keeps(sm, app_engine):
    d, transport = driver(sm, app_engine, answer(200, {"found": True,
                                                       "appointment": {"appointment_at": AT, "status": "Scheduled",
                                                                       "required_documents": ["referral", "blood_test"]}}))
    d.run_step()
    assert d.case.appointment_at == datetime(2026, 10, 3, 10, 30, tzinfo=timezone(timedelta(hours=3)))
    assert transport.urls == [f"http://appointments.test/api/v1/patients/{d.patient_id}/appointment"]
    assert d.state is State.PLANNING


@pytest.mark.parametrize("reply, reason", [
    (answer(200, {"found": False, "appointment": None}), "tool:error:not_found"),
    (answer(404, {"error": "patient_not_found"}), "tool:error:patient_not_found"),
    (answer(401, {"error": "unauthorized"}), "tool:error:unauthorized"),
    (answer(200, {"found": True, "appointment": {"appointment_at": "2026-10-03T10:30:00"}}),
     "tool:error:invalid_response"),
])
def test_an_answer_without_a_usable_appointment_goes_to_a_human(sm, app_engine, reply, reason):
    d, transport = driver(sm, app_engine, reply)
    d.run_step()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.NON_IDEMPOTENT_FAILURE)
    assert reason in reasons(d)
    assert len(transport.urls) == 1  # never retried
    assert d.case.appointment_at is None


def test_an_unavailable_service_is_retried_three_times_then_a_human_decides(sm, app_engine):
    d, transport = driver(sm, app_engine, answer(503, {"error": "patient_registry_unavailable"}))
    for _ in range(3):
        d.run_step()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.RETRY_EXHAUSTED)
    assert len(transport.urls) == 3
    assert reasons(d).count("tool:transient_failure:unavailable") >= 1


def test_a_service_that_recovers_within_the_budget_is_used(sm, app_engine):
    d, transport = driver(sm, app_engine, answer(504, {"error": "timeout"}),
                          answer(200, {"found": True,
                                       "appointment": {"appointment_at": AT, "status": "Scheduled",
                                                       "required_documents": ["referral", "blood_test"]}}))
    d.run_step()
    d.run_step()
    assert d.case.appointment_at is not None and d.state is State.PLANNING
    assert len(transport.urls) == 2


# --- Task 4: LoadInstructions against the same appointment-service (design D8) --------------

INSTRUCTION_ANSWER = {"source_id": "INSTR-CARD-STRESS", "version": "1", "title": "הכנה למבחן מאמץ",
                      "text": "יש לצום כ-3 שעות לפני הבדיקה. שתייה מותרת."}


def test_the_cases_own_source_flows_through_the_policy_to_the_gateway_to_the_data_log(sm, app_engine):
    """The case's instruction_source_id/version (set by CheckAppointment's exam type, D6) is
    what the Orchestrator's Driver equivalent passes to the Policy Service (D7) and what the
    Tool Executor sends to the instruction system (D8) - end to end, over the same fake
    appointment-service, the exact title+text the service answered end up in the Data Log."""
    d, transport = driver(sm, app_engine,
                          answer(200, {"found": True,
                                       "appointment": {"appointment_at": AT, "status": "Scheduled",
                                                       "required_documents": [],
                                                       "instruction": {"source_id": "INSTR-CARD-STRESS",
                                                                      "version": "1"}}}),
                          answer(200, INSTRUCTION_ANSWER))
    d.run_step()  # CheckAppointment: stores instruction_source_id/version on the case
    assert (d.case.instruction_source_id, d.case.instruction_version) == ("INSTR-CARD-STRESS", "1")
    d.advance()
    d.run_step()  # CheckDocuments (the fallback mock; nothing is required)
    d.advance()
    request = d.request()  # the request the real Orchestrator would build for this step
    assert request.instruction_source.source_id == "INSTR-CARD-STRESS"
    assert request.instruction_source.version == "1"
    assert request.proposed_action.patient_fields == ()
    d.run_step()  # LoadInstructions: the real Policy Service, then the real instruction-system call
    assert d.state is State.ASSESSING_READINESS
    assert transport.urls[-1] == "http://appointments.test/api/v1/instructions/INSTR-CARD-STRESS?version=1"
    with app_engine.connect() as conn:
        [entry] = data_log.entries(conn, d.case_id, data_log.DataKind.INSTRUCTIONS)
    assert entry.content == f"{INSTRUCTION_ANSWER['title']}\n{INSTRUCTION_ANSWER['text']}"
