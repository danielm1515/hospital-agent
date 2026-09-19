"""The Agent Orchestrator end to end on Postgres, with the deterministic FakeProvider (LLM design §5, §6, §8).

D1, D2, D9, D21 and D26 of §16, the §14 failure paths, and restart continuation.
"""
import threading
import time

import pytest

from hospital_agent import data_log, repository
from hospital_agent.execution.gateway import MockGateway
from hospital_agent.llm.orchestrator import Orchestrator
from hospital_agent.llm.provider import FakeProvider
from hospital_agent.llm.schemas import Call, LLMUnusable
from hospital_agent.naming import Component, EscalationKind, Event, SafetyLevel, State
from hospital_agent.scripted import DOCUMENT_TEXT, ScriptedAgents

MEDICAL = "Should I stop taking my blood thinner before the colonoscopy?"
UNUSABLE = [LLMUnusable("x")] * 3


@pytest.fixture
def run(sm, app_engine):
    """run(provider=None, gateway=None) -> (patient, orchestrator); orchestrators are closed afterwards."""
    made = []

    def make(provider=None, gateway=None):
        patient = ScriptedAgents(sm, app_engine)
        agent = Orchestrator(sm, provider or FakeProvider(), gateway or MockGateway())
        made.append(agent)
        return patient, agent

    yield make
    for agent in made:
        agent.close()


def events(patient: ScriptedAgents) -> list[str]:
    return [row.event for row in patient.trace() if row.record_type == "Transition"]


def escalation(patient: ScriptedAgents) -> tuple[State, EscalationKind | None]:
    case = patient.case
    return case.state, case.escalation_kind


# --- the three scenarios' paths ------------------------------------------------------------

def test_d1_an_operational_request_completes(run):
    patient, agent = run()
    patient.submit()
    patient.validate()
    assert agent.run_case(patient.case_id) is State.AWAITING_PATIENT_INPUT  # blood_test missing, Z3 unsat
    patient.upload("blood_test")
    assert agent.run_case(patient.case_id) is State.COMPLETED
    case = patient.case
    assert (case.intent, case.safety_level) == ("AppointmentPreparation", SafetyLevel.MEDIUM_RISK)
    assert events(patient).count("ACTION_PROPOSED") == 4


def test_the_outgoing_message_is_kept_and_referenced_by_hash(run, app_engine):
    patient, agent = run()
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)
    patient.upload("blood_test")
    agent.run_case(patient.case_id)
    with app_engine.connect() as conn:
        [message] = data_log.entries(conn, patient.case_id, data_log.DataKind.OUTGOING_MESSAGE)
        [instructions] = data_log.entries(conn, patient.case_id, data_log.DataKind.INSTRUCTIONS)
    assert "INSTR-PREP-COLONOSCOPY" in message.content and "Colonoscopy preparation" in instructions.content
    started = [row for row in patient.trace() if row.event == "TOOL_EXECUTION_STARTED"][-1]
    assert (started.action, started.content_hash) == ("SendStatusUpdate", message.content_hash)


def test_d2_a_medical_question_goes_to_a_human(run):
    patient, agent = run()
    patient.submit()
    patient.validate(MEDICAL)
    assert agent.run_case(patient.case_id) is State.AWAITING_HUMAN_REVIEW
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.MEDICAL_QUESTION)
    assert events(patient)[-1] == "MEDICAL_QUESTION_DETECTED"


def test_d9_a_valid_document_with_risky_text_escalates_before_the_planner(run):
    patient, agent = run()
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)
    patient.upload("blood_test", content="Results attached. Since yesterday I have chest pain and severe bleeding.")
    assert agent.run_case(patient.case_id) is State.AWAITING_HUMAN_REVIEW
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.MEDICAL_QUESTION)


def test_d26_a_non_medical_critical_risk_stops_before_the_planner(run):
    provider = FakeProvider({Call.SAFETY: [{"safety_level": "CriticalRisk"}]})
    patient, agent = run(provider)
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.SAFETY_ESCALATION)
    assert Call.PLANNER not in [call for call, _ in provider.calls]
    assert not patient.human(Event.HUMAN_APPROVED, patient.approval("approve")).committed  # resolve/reject only


class GatedSafety(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def complete(self, call, user_input, schema):
        if call is Call.SAFETY:
            assert self.release.wait(5)
        return super().complete(call, user_input, schema)


def test_d21_intent_back_before_safety_keeps_the_case_classifying(run):
    provider = GatedSafety()
    patient, agent = run(provider)
    patient.submit()
    patient.validate()
    worker = threading.Thread(target=agent.step, args=(patient.case_id,))
    worker.start()
    deadline = time.monotonic() + 5
    while Call.INTENT not in [call for call, _ in provider.calls] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert patient.state is State.CLASSIFYING
    assert Call.PLANNER not in [call for call, _ in provider.calls]
    provider.release.set()
    worker.join(5)
    assert patient.state is State.CLASSIFIED


# --- §14 failure paths -------------------------------------------------------------------------

@pytest.mark.parametrize("call, kind", [
    (Call.INTENT, EscalationKind.CLASSIFICATION_FAILED),
    (Call.SAFETY, EscalationKind.CLASSIFICATION_FAILED),
    (Call.PLANNER, EscalationKind.PLANNING_FAILED),
])
def test_three_unusable_answers_escalate(run, call, kind):
    patient, agent = run(FakeProvider({call: UNUSABLE}))
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, kind)
    assert patient.trace()[-1].policy_reasons == [f"llm_failed:{call.value}"]


def test_an_unsupported_intent_has_no_complete_plan(run):
    patient, agent = run()
    patient.submit()
    patient.validate("Where do I pay the invoice for parking?")
    agent.run_case(patient.case_id)
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.PLANNING_FAILED)
    assert patient.trace()[-1].policy_reasons == ["plan_incomplete"]


def test_three_inplan_rejections_of_one_step_escalate(run):
    plan = {"plan_complete": True, "ordered_steps": [
        {"step": 1, "action": "CheckAppointment"}, {"step": 2, "action": "CheckDocuments"},
        {"step": 3, "action": "LoadInstructions"}, {"step": 4, "action": "SendStatusUpdate"}]}
    wrong = {"action": "CheckDocuments", "from_step": 1}  # a step out of order
    patient, agent = run(FakeProvider({Call.PLANNER: [plan, wrong, wrong, wrong]}))
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.PLANNING_FAILED)
    blocked = [row for row in patient.trace() if row.record_type == "Blocked"]
    assert [row.event for row in blocked] == ["ACTION_PROPOSED"] * 3
    assert patient.trace()[-1].policy_reasons == ["blocked:3"]


def test_an_evaluator_that_fails_escalates_as_planning_failed(run):
    patient, agent = run(FakeProvider({Call.EVALUATOR: [LLMUnusable("x")] * 5}))
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)
    patient.upload("blood_test")
    agent.run_case(patient.case_id)
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.PLANNING_FAILED)
    assert patient.trace()[-1].policy_reasons == ["llm_failed:evaluator"]


def test_a_message_marked_medical_is_denied_without_content_approval(run):
    """D8's path, reached through the real Response Evaluator: Deny medical_answer_attempt."""
    patient, agent = run(FakeProvider({Call.EVALUATOR: [{"medical_content_flag": True}] * 5}))
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)
    patient.upload("blood_test")
    agent.run_case(patient.case_id)
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.POLICY_DENIED)
    assert events(patient)[-1] == "POLICY_DENIED"
    assert "medical_answer_attempt" in patient.trace()[-1].policy_reasons


def test_risky_retrieved_content_needs_a_human_at_the_next_step(run):
    # classification: MediumRisk; the re-check of the retrieved instructions: HighRisk
    provider = FakeProvider({Call.SAFETY: [{"safety_level": "MediumRisk"}, {"safety_level": "HighRisk"}]})
    gateway = MockGateway(required_documents=("referral",), held_documents=("referral",))
    patient, agent = run(provider, gateway)
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)
    assert patient.case.safety_level is SafetyLevel.HIGH_RISK
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.POLICY_REVIEW)
    assert events(patient)[-3:] == ["DELIVERY_PLANNED", "ACTION_PROPOSED", "POLICY_HUMAN_REVIEW_REQUIRED"]


def test_reclassification_never_lowers_the_risk(run):
    provider = FakeProvider({Call.SAFETY: [{"safety_level": "MediumRisk"}, {"safety_level": "HighRisk"}]})
    patient, agent = run(provider)
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)          # instructions re-checked HighRisk; then the patient is asked
    patient.upload("blood_test")
    agent.run_case(patient.case_id)          # re-classified MediumRisk - the case stays HighRisk
    assert patient.case.safety_level is SafetyLevel.HIGH_RISK
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.POLICY_REVIEW)


def test_a_content_check_that_fails_escalates_as_execution_unknown(run):
    provider = FakeProvider({Call.SAFETY: [{"safety_level": "MediumRisk"}, *UNUSABLE]})
    patient, agent = run(provider)
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.EXECUTION_UNKNOWN)
    assert patient.trace()[-1].policy_reasons == ["content_check_failed:LLMFailed"]


# --- restart, ticks, the background thread, the app ----------------------------------------

def test_a_new_orchestrator_finishes_a_case_another_one_started(run):
    patient, first = run()
    patient.submit()
    patient.validate()
    for _ in range(6):                 # classified, planned, part of step 1 - then the process "dies"
        first.step(patient.case_id)
    assert patient.state in (State.PLANNING, State.RETRIEVING_DATA)
    _, second = run()
    assert second.run_case(patient.case_id) is State.AWAITING_PATIENT_INPUT


def test_waiting_states_are_left_alone(run):
    patient, agent = run()
    patient.submit()
    assert agent.step(patient.case_id) is None  # Received: the Session Service's turn


def test_a_tick_advances_every_active_case(run, sm, app_engine):
    first, agent = run()
    first.submit()
    first.validate()
    second = ScriptedAgents(sm, app_engine, patient_id="P-20000")
    second.submit()
    second.validate(MEDICAL)
    agent.tick()
    assert (first.state, second.state) == (State.AWAITING_PATIENT_INPUT, State.AWAITING_HUMAN_REVIEW)


def test_the_background_thread_wakes_on_demand(run):
    patient, agent = run()
    stop = agent.run_in_background(interval_seconds=3600)
    try:
        patient.submit()
        patient.validate()
        agent.wake()
        deadline = time.monotonic() + 20
        while patient.state is not State.AWAITING_PATIENT_INPUT and time.monotonic() < deadline:
            time.sleep(0.05)
        assert patient.state is State.AWAITING_PATIENT_INPUT
    finally:
        stop.set()
        agent.wake()


def test_close_after_run_in_background_joins_the_thread(run):
    patient, agent = run()
    agent.run_in_background(interval_seconds=3600)
    thread = agent._background_thread
    assert thread.is_alive()
    agent.close()
    assert not thread.is_alive()


# --- fix round 1: tick() fails closed on its own errors; a rejected upload is tombstoned ---

def _raise_runtime_error() -> list[str]:
    raise RuntimeError("boom")


def test_a_tick_survives_active_case_ids_raising_once(run):
    patient, agent = run()
    patient.submit()
    patient.validate()
    assert patient.state is State.CLASSIFYING
    original = agent._active_case_ids
    agent._active_case_ids = _raise_runtime_error
    agent.tick()  # the whole tick is guarded: nothing raises out of it, nothing changes
    assert patient.state is State.CLASSIFYING
    agent._active_case_ids = original
    agent.tick()  # the next tick advances the case normally
    assert patient.state is State.AWAITING_PATIENT_INPUT


def test_a_case_whose_step_keeps_raising_escalates_after_three_ticks(run):
    patient, agent = run()
    patient.submit()
    patient.validate()
    agent.step(patient.case_id)  # Classifying -> Classified
    assert patient.state is State.CLASSIFIED

    def _fail_plan(*_args, **_kwargs):
        raise RuntimeError("boom")

    agent.planner.plan = _fail_plan
    agent.tick()
    assert patient.state is State.CLASSIFIED  # 1st failure: not escalated yet
    agent.tick()
    assert patient.state is State.CLASSIFIED  # 2nd failure: still not escalated
    agent.tick()  # 3rd failure in a row: fails closed
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.PLANNING_FAILED)
    assert patient.trace()[-1].policy_reasons == ["orchestrator_error:RuntimeError"]


def test_a_rejected_upload_is_tombstoned_and_never_reaches_the_llm(run):
    provider = FakeProvider()
    patient, agent = run(provider)
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)  # blood_test missing, Z3 unsat -> AwaitingPatientInput
    rejected = "Someone else's chart: severe complications, do not disclose."
    result = patient.upload("blood_test", content=rejected, patient_id="P-OTHER")  # D25: another patient's document
    assert result.committed and result.state_after is State.AWAITING_PATIENT_INPUT  # rejected: stays put
    patient.upload("blood_test")  # the real document
    agent.run_case(patient.case_id)
    documents = [user_input for call, user_input in provider.calls if call is Call.INTENT][-1]["documents"]
    assert rejected not in documents
    assert any(DOCUMENT_TEXT in document for document in documents)


# --- final fixes: accepted uploads only, executor errors, AssessingReadiness, injected events ---

def test_an_untombstoned_rejected_upload_never_reaches_the_llm(run, app_engine):
    """The record/tombstone race: the rejected entry is still readable, yet it is not classified."""
    provider = FakeProvider()
    patient, agent = run(provider)
    patient.submit()
    patient.validate()
    agent.run_case(patient.case_id)  # blood_test missing -> AwaitingPatientInput
    rejected = "Someone else's chart: severe complications, do not disclose."
    with app_engine.begin() as conn:  # recorded, never tombstoned
        entry = data_log.record(conn, patient.case_id, patient.patient_id, data_log.DataKind.UPLOADED_DOCUMENT,
                                rejected, patient.sm.clock())
    payload = {"document": {"document_id": "blood_test", "format": "pdf", "patient_id": "P-OTHER"},
               "content_hash": entry.content_hash}
    result = patient._emit(Event.DOCUMENT_UPLOADED, payload, Component.SESSION_SERVICE)  # D25: rejected
    assert result.state_after is State.AWAITING_PATIENT_INPUT
    patient.upload("blood_test")  # the accepted document
    agent.run_case(patient.case_id)
    [documents] = [user_input["documents"] for call, user_input in provider.calls if call is Call.INTENT][-1:]
    assert documents == [DOCUMENT_TEXT]  # the accepted upload is sent, the rejected one never
    assert all(rejected not in user_input.get("documents", ()) for _, user_input in provider.calls)


def _until(agent, patient, state: State) -> None:
    for _ in range(50):
        if patient.state is state:
            return
        agent.step(patient.case_id)
    assert patient.state is state


def test_an_executor_error_before_the_start_escalates_as_execution_unknown(run):
    patient, agent = run()
    patient.submit()
    patient.validate()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    agent.executor.execute = _boom
    agent.run_case(patient.case_id)  # POLICY_ALLOWED -> RetrievingData, then execute() raises
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.EXECUTION_UNKNOWN)
    assert patient.case.escalated_from_state is State.RETRIEVING_DATA
    assert patient.trace()[-1].policy_reasons == ["execution_error:RuntimeError"]


def test_an_executor_error_after_the_start_records_an_unknown_outcome(run, app_engine):
    patient, agent = run()
    patient.submit()
    patient.validate()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    agent.executor.finish = _boom  # the call was made; its outcome is lost
    agent.run_case(patient.case_id)
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.EXECUTION_UNKNOWN)
    execution_id = next(row.execution_id for row in patient.trace() if row.event == "TOOL_EXECUTION_STARTED")
    with app_engine.connect() as conn:
        assert repository.load_execution(conn, execution_id).status == "unknown"


def test_three_blocked_rows_in_assessing_readiness_escalate_as_z3_counterexample(run, sm):
    patient, agent = run()
    patient.submit()
    patient.validate()
    _until(agent, patient, State.ASSESSING_READINESS)
    agent.readiness.run = lambda case_id, *_: sm.record_blocked(case_id, Event.READINESS_PASSED, "guard_failed")
    agent.run_case(patient.case_id)
    assert escalation(patient) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.Z3_COUNTEREXAMPLE)
    assert patient.trace()[-1].policy_reasons == ["blocked:3"]


def test_injected_system_owned_events_never_force_an_escalation(run):
    patient, agent = run()
    patient.submit()
    patient.validate()
    agent.step(patient.case_id)
    assert patient.state is State.CLASSIFIED
    for _ in range(5):  # §13.2: each is Blocked system_owned_event
        assert not patient._emit(Event.PLAN_CREATED, {"plan_complete": False}, Component.EXTERNAL).committed
    assert agent.run_case(patient.case_id) is State.AWAITING_PATIENT_INPUT


def test_a_tick_forgets_errors_of_cases_no_longer_active(run):
    _, agent = run()
    agent._consecutive_errors["CASE-GONE"] = 2
    agent.tick()
    assert "CASE-GONE" not in agent._consecutive_errors
