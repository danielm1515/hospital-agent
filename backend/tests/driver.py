"""Plays the components the Core does not contain yet, so a case can be driven through §3.

Each method emits one event from the component that owns it (§13.2), with the
payload that component would attest. Sub-projects 2-5 replace these stand-ins
with the real Classifier, Planner, Policy Service, Tool Executor and reviewers.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine

from hospital_agent import repository
from hospital_agent.case import ApprovalRecord, CaseRecord, ExecutionRecord
from hospital_agent.naming import Action, Component, Event, State
from hospital_agent.policy.readiness import ReadinessCheck
from hospital_agent.policy.service import (
    InstructionSource,
    OutgoingMessage,
    PolicyRequest,
    PolicyService,
    ProposedAction,
)
from hospital_agent.state_manager import StateManager, TransitionResult

PLAN = [
    {"step": 1, "action": "CheckAppointment"},
    {"step": 2, "action": "CheckDocuments"},
    {"step": 3, "action": "LoadInstructions"},
    {"step": 4, "action": "SendStatusUpdate"},
]

# What the Planner would send out for each action (spec §11 minimized fields).
TARGETS = {
    Action.CHECK_APPOINTMENT: ("appointment_system", ("patient_id",)),
    Action.CHECK_DOCUMENTS: ("document_system", ("patient_id",)),
    Action.LOAD_INSTRUCTIONS: ("instruction_system", ()),
    Action.SEND_STATUS_UPDATE: ("patient_channel", ("patient_id",)),
}
APPROVED_SOURCE = InstructionSource("INSTR-PREP-COLONOSCOPY", "3")
STATUS_MESSAGE = OutgoingMessage(evaluated=True, medical_content_flag=False, content_hash="HASH-STATUS-1")


class Driver:
    def __init__(self, sm: StateManager, engine: Engine, patient_id: str = "P-10041") -> None:
        self.sm, self.engine, self.patient_id = sm, engine, patient_id
        self.case_id: str | None = None
        self.policy = PolicyService(engine)

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
        target, fields = TARGETS[action]
        request = PolicyRequest(
            execution_id=f"EXEC-{uuid.uuid4().hex[:8]}",
            proposed_action=ProposedAction(action.value, case.current_step, target, fields),
            outgoing_message=STATUS_MESSAGE if action is Action.SEND_STATUS_UPDATE else None,
            instruction_source=APPROVED_SOURCE if action is Action.LOAD_INSTRUCTIONS else None,
        )
        return replace(request, **overrides)

    def allow(self, **overrides) -> TransitionResult:
        """The real Policy Service decides the current step (Allow for a well-formed request)."""
        return self.policy.apply(self.sm, self.case_id, self.request(**overrides))

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

    # --- Readiness Check ------------------------------------------------------------------

    def missing_information(self, z3_result: str = "unsat") -> TransitionResult:
        payload = {"z3_result": z3_result, "patient_deadline": datetime.now(UTC) + timedelta(hours=96)}
        return self._emit(Event.MISSING_INFORMATION_DETECTED, payload, Component.READINESS_CHECK)

    def readiness_passed(self) -> TransitionResult:
        return self._emit(Event.READINESS_PASSED, {}, Component.READINESS_CHECK)

    def assess(self, hours_until: object = 96) -> TransitionResult:
        """The real Readiness Check with Z3 (spec §9.1)."""
        return ReadinessCheck(self.sm).run(self.case_id, hours_until)

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

    # --- scenario prefixes -------------------------------------------------------------------

    def to_classified(self) -> None:
        self.submit()
        self.validate()
        self.classify()

    def retrieve_step(self, **result) -> None:
        """propose -> allow -> retrieved, for the current plan step."""
        self.propose()
        self.allow()
        self.retrieved(**result)

    def to_assessing_readiness(self, required: list[str], held: list[str]) -> None:
        self.to_classified()
        self.plan()
        self.retrieve_step()
        self.advance()
        self.retrieve_step(required_documents=required, held_documents=held)
        self.advance()
        self.retrieve_step()
