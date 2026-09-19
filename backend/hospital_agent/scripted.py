"""Scripted stand-ins for the components that do not exist yet (Execution design §6).

ScriptedAgents plays the Session Service, the Classifier, the Planner, the Agent
Orchestrator and the human reviewers with fixed demo answers; everything else it
drives is real - the State Manager, the Policy Service (OPA + Prolog), the Readiness
Check (Z3), the Temporal Monitor and the Tool Executor. Each event comes from the
component that owns it (§13.2).

Used by `python -m obs.golden` and, through tests/driver.py, by the tests. Sub-project 4
replaces the Classifier, Planner and Orchestrator parts with the LLM-backed ones, and
sub-project 5 the reviewers.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine

from . import repository
from .case import ApprovalRecord, CaseRecord
from .execution.executor import ToolExecutor
from .execution.gateway import ACTION_TARGETS, ToolGateway
from .naming import Action, Component, Event, State
from .policy.readiness import ReadinessCheck
from .policy.service import InstructionSource, OutgoingMessage, PolicyRequest, PolicyService, ProposedAction
from .state_manager import StateManager, TransitionResult

PLAN = [
    {"step": 1, "action": "CheckAppointment"},
    {"step": 2, "action": "CheckDocuments"},
    {"step": 3, "action": "LoadInstructions"},
    {"step": 4, "action": "SendStatusUpdate"},
]
APPROVED_SOURCE = InstructionSource("INSTR-PREP-COLONOSCOPY", "3")
STATUS_MESSAGE = OutgoingMessage(evaluated=True, medical_content_flag=False, content_hash="HASH-STATUS-1")
EXECUTING_STATES = frozenset({State.RETRIEVING_DATA, State.DELIVERING})


class ScriptedAgents:
    def __init__(self, sm: StateManager, engine: Engine, gateway: ToolGateway, patient_id: str = "P-10041") -> None:
        self.sm, self.engine, self.patient_id = sm, engine, patient_id
        self.case_id: str | None = None
        self.policy = PolicyService(engine)
        self.executor = ToolExecutor(sm, gateway)
        self.readiness = ReadinessCheck(sm)
        self.last_execution_id: str | None = None

    # --- reading ---------------------------------------------------------------------

    @property
    def case(self) -> CaseRecord:
        return self.sm.load(self.case_id)

    @property
    def state(self) -> State:
        return self.case.state

    def trace(self) -> list[repository.AuditEntry]:
        with self.engine.connect() as conn:
            return repository.load_trace(conn, self.case_id)

    def _emit(self, event: Event, payload: dict, source: Component) -> TransitionResult:
        return self.sm.apply(self.case_id, event, payload, source)

    # --- patient / Session Service ---------------------------------------------------------

    def submit(self) -> TransitionResult:
        result = self.sm.apply(None, Event.REQUEST_SUBMITTED, {"patient_id": self.patient_id}, Component.EXTERNAL)
        self.case_id = result.case_id
        return result

    def validate(self, identity_verified: bool = True) -> TransitionResult:
        payload = {"text": "When is my appointment and which documents do I need?", "identity_verified": identity_verified}
        return self._emit(Event.REQUEST_VALIDATED, payload, Component.SESSION_SERVICE)

    def verification_failed(self) -> TransitionResult:
        return self._emit(Event.PATIENT_VERIFICATION_FAILED, {}, Component.SESSION_SERVICE)

    def upload(self, document_id: str, **document) -> TransitionResult:
        payload = {"document": {"document_id": document_id, "format": "pdf", "patient_id": self.patient_id, **document}}
        return self._emit(Event.DOCUMENT_UPLOADED, payload, Component.SESSION_SERVICE)

    # --- Classifier / Planner / Orchestrator ------------------------------------------------

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

    # --- Policy Service / Tool Executor -------------------------------------------------------

    def request(self, **overrides) -> PolicyRequest:
        """A well-formed PolicyRequest for the current plan step; overrides replace fields."""
        case = self.case
        action = case.current_action
        target, fields = ACTION_TARGETS[action.value]
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
        """One plan step, as the Agent Orchestrator will run it: propose -> decide -> execute."""
        self.propose()
        decided = self.allow(**overrides)
        if decided.committed and decided.state_after in EXECUTING_STATES:
            return self.execute()
        return decided

    # --- Readiness Check ------------------------------------------------------------------

    def assess(self) -> TransitionResult:
        """The real Readiness Check with Z3 (§9.1), on the stored appointment time."""
        return self.readiness.run(self.case_id)

    # --- human reviewers -----------------------------------------------------------------

    def approval(self, decision: str, **fields) -> str:
        """Insert a WorkflowDecision for the current escalation and return its id."""
        case = self.case
        now = datetime.now(UTC)
        approval_id = f"APPR-{uuid.uuid4().hex[:6]}"
        record = ApprovalRecord(
            approval_id=approval_id,
            approval_type="WorkflowDecision",
            case_id=case.case_id,
            patient_id=case.patient_id,
            reviewer_id="coordinator_nurse",
            reviewer_role="clinical_staff",
            decision=decision,
            reason="reviewed by staff",
            shown_context_ref=f"ctx-{approval_id}",
            granted_at=now - timedelta(minutes=1),
            valid_until=now + timedelta(hours=1),
            escalation_kind=case.escalation_kind.value if case.escalation_kind else None,
        )
        with self.engine.begin() as conn:
            repository.insert_approval(conn, replace(record, **fields))
        return approval_id

    def human(self, event: Event, approval_id: str) -> TransitionResult:
        return self._emit(event, {"approval_id": approval_id}, Component.EXTERNAL)
