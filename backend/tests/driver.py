"""The scripted Session Service and reviewers (hospital_agent.scripted) plus test-only shortcuts.

The shortcuts emit a component's event directly - instead of the Agent Orchestrator and its
LLM components - so a test can put a case in an exact spot (a given classification, plan
step, document set, exhausted retry, failed delivery) and check one guard or rule there.
The Policy Service, Tool Executor and Readiness Check they drive are the real ones.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine

from hospital_agent import repository
from hospital_agent.case import ExecutionRecord
from hospital_agent.execution.executor import ToolExecutor
from hospital_agent.execution.gateway import ACTION_TARGETS, MockGateway, ToolGateway, present_patient_fields
from hospital_agent.execution.verify import EXECUTING_STATES
from hospital_agent.naming import Action, Component, Event
from hospital_agent.policy.readiness import ReadinessCheck
from hospital_agent.policy.service import InstructionSource, OutgoingMessage, PolicyRequest, PolicyService, ProposedAction
from hospital_agent.scripted import ScriptedAgents
from hospital_agent.state_manager import StateManager, TransitionResult

__all__ = ["PLAN", "Driver"]

PLAN = [
    {"step": 1, "action": "CheckAppointment"},
    {"step": 2, "action": "CheckDocuments"},
    {"step": 3, "action": "LoadInstructions"},
    {"step": 4, "action": "SendStatusUpdate"},
]
APPROVED_SOURCE = InstructionSource("INSTR-PREP-COLONOSCOPY", "3")
STATUS_MESSAGE = OutgoingMessage(evaluated=True, medical_content_flag=False, content_hash="HASH-STATUS-1")


class Driver(ScriptedAgents):
    def __init__(self, sm: StateManager, engine: Engine, patient_id: str = "P-10041",
                 gateway: ToolGateway | None = None) -> None:
        super().__init__(sm, engine, patient_id)
        self.policy = PolicyService(engine)
        self.executor = ToolExecutor(sm, gateway or MockGateway())
        self.readiness = ReadinessCheck(sm)
        self.last_execution_id: str | None = None

    # --- Classifier / Planner / Orchestrator, emitted directly -----------------------------

    def classify(self, safety_level: str = "MediumRisk") -> TransitionResult:
        payload = {"intent": "AppointmentPreparation", "safety_level": safety_level}
        return self._emit(Event.INTENT_CLASSIFIED, payload, Component.CLASSIFIER_SERVICE)

    def medical_question(self) -> TransitionResult:
        payload = {"intent": "MedicalQuestion", "safety_level": "HighRisk"}
        return self._emit(Event.MEDICAL_QUESTION_DETECTED, payload, Component.CLASSIFIER_SERVICE)

    def plan(self) -> TransitionResult:
        return self._emit(Event.PLAN_CREATED, {"plan_complete": True, "ordered_steps": PLAN}, Component.PLANNER_SERVICE)

    def propose(self) -> TransitionResult:
        case = self.case
        proposal = {"action": case.current_action.value, "from_step": case.current_step}
        return self._emit(Event.ACTION_PROPOSED, {"proposed_action": proposal}, Component.PLANNER_SERVICE)

    def advance(self) -> TransitionResult:
        return self._emit(Event.STEP_ADVANCED, {}, Component.AGENT_ORCHESTRATOR)

    def plan_delivery(self) -> TransitionResult:
        return self._emit(Event.DELIVERY_PLANNED, {}, Component.AGENT_ORCHESTRATOR)

    # --- the real Policy Service / Tool Executor / Readiness Check -------------------------

    def request(self, **overrides) -> PolicyRequest:
        """A well-formed PolicyRequest for the current plan step; overrides replace fields."""
        case = self.case
        action = case.current_action
        target, _ = ACTION_TARGETS[action.value]
        fields = present_patient_fields(case, action.value)
        request = PolicyRequest(
            execution_id=f"EXEC-{uuid.uuid4().hex[:8]}",
            proposed_action=ProposedAction(action.value, case.current_step, target, fields),
            outgoing_message=STATUS_MESSAGE if action is Action.SEND_STATUS_UPDATE else None,
            instruction_source=APPROVED_SOURCE if action is Action.LOAD_INSTRUCTIONS else None,
        )
        return replace(request, **overrides)

    def allow(self, **overrides) -> TransitionResult:
        """The real Policy Service decides the current step (Allow for a well-formed request)."""
        request = self.request(**overrides)
        self.last_execution_id = request.execution_id
        return self.policy.apply(self.sm, self.case_id, request)

    def execute(self) -> TransitionResult:
        """The real Tool Executor runs the decision allow() just accepted."""
        return self.executor.execute(self.case_id, self.last_execution_id)

    def run_step(self, **overrides) -> TransitionResult:
        """One plan step: propose -> decide -> execute."""
        self.propose()
        decided = self.allow(**overrides)
        if decided.committed and decided.state_after in EXECUTING_STATES:
            return self.execute()
        return decided

    def assess(self) -> TransitionResult:
        """The real Readiness Check with Z3 (§9.1), on the stored appointment time."""
        return self.readiness.run(self.case_id)

    # --- direct emissions (no real Tool Executor / Readiness Check) ---------------------------

    def retrieved(self, **result) -> TransitionResult:
        return self._emit(Event.DATA_RETRIEVED, result, Component.TOOL_EXECUTOR)

    def transient_failure(self) -> TransitionResult:
        payload = {"idempotency_key": f"idem-{uuid.uuid4().hex[:8]}", "idempotent": True}
        return self._emit(Event.TOOL_TRANSIENT_FAILURE, payload, Component.TOOL_EXECUTOR)

    def retry_exhausted(self) -> TransitionResult:
        return self._emit(Event.RETRY_EXHAUSTED, {}, Component.TOOL_EXECUTOR)

    def deliver(self, status: str = "succeeded") -> TransitionResult:
        case = self.case
        execution_id = f"EXEC-{uuid.uuid4().hex[:8]}"
        with self.engine.begin() as conn:
            repository.insert_execution(conn, ExecutionRecord(
                execution_id=execution_id,
                case_id=case.case_id,
                patient_id=case.patient_id,
                action="SendStatusUpdate",
                step=case.current_step,
                retry_cycle=case.retry_cycle,
                attempt_number=1,
                idempotency_key=f"idem-{execution_id}",
                status=status,
            ))
        return self._emit(Event.CASE_RESOLVED, {"execution_id": execution_id}, Component.RESPONSE_DELIVERY)

    def missing_information(self, z3_result: str = "unsat") -> TransitionResult:
        payload = {"z3_result": z3_result, "patient_deadline": datetime.now(UTC) + timedelta(hours=96)}
        return self._emit(Event.MISSING_INFORMATION_DETECTED, payload, Component.READINESS_CHECK)

    def readiness_passed(self) -> TransitionResult:
        return self._emit(Event.READINESS_PASSED, {}, Component.READINESS_CHECK)

    # --- scenario prefixes -------------------------------------------------------------------

    def to_classified(self, appointment_id: str | None = None) -> None:
        self.submit(appointment_id)
        self.validate()
        self.classify()

    def retrieve_step(self, **result) -> None:
        """propose -> allow -> retrieved (direct), for the current plan step."""
        self.propose()
        self.allow()
        self.retrieved(**result)

    def to_assessing_readiness(self, required: list[str], held: list[str], hours_until: float = 96) -> None:
        self.to_classified()
        self.plan()
        self.retrieve_step(appointment_at=datetime.now(UTC) + timedelta(hours=hours_until))
        self.advance()
        self.retrieve_step(required_documents=required, held_documents=held)
        self.advance()
        self.retrieve_step()
