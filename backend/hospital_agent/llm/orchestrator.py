"""Agent Orchestrator (spec §1; LLM design §5, decision 7).

It keeps no state of its own: every step reads the case from the database and does the one
thing that State calls for, so a restart simply carries on (the State is in Postgres).

    Classifying         Intent + Safety in parallel (D21) -> MEDICAL_QUESTION_DETECTED /
                        SafetyEscalation / INTENT_CLASSIFIED (§14 precedence)
    Classified          Planner: plan -> PLAN_CREATED, or PlanningFailed (plan_complete=false)
    Planning            after DATA_RETRIEVED: STEP_ADVANCED; after ACTION_PROPOSED: the Policy
                        Service decides and the Tool Executor runs an allowed call; otherwise
                        the Planner proposes the next step (ACTION_PROPOSED)
    AssessingReadiness  the Readiness Check (Z3)
    Ready               DELIVERY_PLANNED, or DeliveryStepMissing

Every other State waits for someone else (the patient, a reviewer, the SLA Worker) or is
final. Failures fail closed (§14): three unusable LLM answers in a row, or three blocked
events in a row in one State, escalate with that State's failure kind; three InPlan
rejections of the same step are exactly the latter (§3.1 InPlan).

Only the Tool Executor calls an external system; LLM calls change nothing outside, so a
step interrupted by a crash is simply done again.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid

from .. import data_log, repository
from ..execution.executor import ToolExecutor
from ..execution.gateway import ACTION_TARGETS, ToolGateway
from ..execution.verify import EXECUTING_STATES
from ..naming import Action, Component, EscalationKind, Event, State
from ..policy.readiness import ReadinessCheck
from ..policy.service import InstructionSource, OutgoingMessage, PolicyRequest, PolicyService, ProposedAction
from ..state_manager import StateManager, TransitionResult
from .classifier import Classifier, verdict
from .evaluator import ResponseEvaluator
from .message import status_message
from .planner import Planner
from .provider import LLMFailed, LLMProvider

logger = logging.getLogger(__name__)

ACTIVE_STATES = (State.CLASSIFYING, State.CLASSIFIED, State.PLANNING, State.ASSESSING_READINESS, State.READY)
MAX_CONSECUTIVE_BLOCKED = 3
DEFAULT_INTERVAL_SECONDS = 5.0
# The approved instruction source of the demo's one intent (spec §8 bundle data); the Planner
# never supplies it (§3.1 ApprovedSource).
INSTRUCTION_SOURCE = InstructionSource("INSTR-PREP-COLONOSCOPY", "3")

# (the escalation kind, the component that signals it) when a State cannot make progress
FAILURE: dict[State, tuple[EscalationKind, Component]] = {
    State.CLASSIFYING: (EscalationKind.CLASSIFICATION_FAILED, Component.CLASSIFIER_SERVICE),
    State.CLASSIFIED: (EscalationKind.PLANNING_FAILED, Component.PLANNER_SERVICE),
    State.PLANNING: (EscalationKind.PLANNING_FAILED, Component.PLANNER_SERVICE),
    State.READY: (EscalationKind.DELIVERY_STEP_MISSING, Component.AGENT_ORCHESTRATOR),
}


def orchestrator_interval_seconds() -> float:
    return float(os.environ.get("ORCHESTRATOR_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))


class Orchestrator:
    def __init__(self, state_manager: StateManager, provider: LLMProvider, gateway: ToolGateway) -> None:
        self.sm = state_manager
        self.classifier = Classifier(provider)
        self.planner = Planner(provider)
        self.evaluator = ResponseEvaluator(provider)
        self.policy = PolicyService(state_manager.engine)
        self.executor = ToolExecutor(state_manager, gateway, content_check=self.classifier.safety)
        self.readiness = ReadinessCheck(state_manager)
        self._wake = threading.Event()

    # --- one step ------------------------------------------------------------------------

    def step(self, case_id: str) -> TransitionResult | None:
        """Do the one thing the case's State calls for; None if the State waits for someone else."""
        case = self.sm.load(case_id)
        if case.state not in ACTIVE_STATES:
            return None
        trace = self._trace(case_id)
        blocked = _blocked_since_last_transition(trace)
        if blocked >= MAX_CONSECUTIVE_BLOCKED and case.state in FAILURE:
            return self._fail(case_id, case.state, f"blocked:{blocked}")
        try:
            match case.state:
                case State.CLASSIFYING:
                    return self._classify(case_id)
                case State.CLASSIFIED:
                    return self._plan(case_id)
                case State.PLANNING:
                    return self._planning(case_id, _last_transition_event(trace))
                case State.ASSESSING_READINESS:
                    return self.readiness.run(case_id)
                case State.READY:
                    return self._deliver(case_id)
        except LLMFailed as exc:  # §14: three unusable answers in a row
            return self._fail(case_id, case.state, f"llm_failed:{exc}")
        return None

    def run_case(self, case_id: str, max_steps: int = 100) -> State:
        """Step the case until it waits for someone else or is final."""
        for _ in range(max_steps):
            if self.step(case_id) is None:
                break
        return self.sm.load(case_id).state

    def tick(self) -> None:
        """One pass over every case that is waiting for the orchestrator."""
        for case_id in self._active_case_ids():
            try:
                self.run_case(case_id)
            except Exception as exc:  # one broken case never stops the others; no patient data logged (§12.3)
                logger.error("orchestrator step failed: %s", type(exc).__name__)

    def wake(self) -> None:
        """Run the next tick now (e.g. a new request was submitted)."""
        self._wake.set()

    def run_in_background(self, interval_seconds: float = DEFAULT_INTERVAL_SECONDS) -> threading.Event:
        stop = threading.Event()

        def loop() -> None:
            while not stop.is_set():
                self.tick()
                self._wake.wait(interval_seconds)
                self._wake.clear()

        threading.Thread(target=loop, name="agent-orchestrator", daemon=True).start()
        return stop

    def close(self) -> None:
        self.evaluator.close()

    # --- per State -----------------------------------------------------------------------

    def _classify(self, case_id: str) -> TransitionResult:
        case = self.sm.load(case_id)
        with self.sm.engine.connect() as conn:
            requests = [e.content for e in data_log.entries(conn, case_id, data_log.DataKind.REQUEST_TEXT) if e.content]
            documents = [e.content for e in data_log.entries(conn, case_id, data_log.DataKind.UPLOADED_DOCUMENT)
                         if e.content]
        if not requests:
            return self._fail(case_id, case.state, "request_text_unavailable")
        classification = self.classifier.classify(requests[-1], documents)
        payload = {"intent": classification.intent.value, "safety_level": classification.safety_level.value}
        outcome = verdict(classification)
        if outcome is EscalationKind.SAFETY_ESCALATION:
            return self.sm.escalation.signal(case_id, outcome, State.CLASSIFYING, Component.CLASSIFIER_SERVICE,
                                             reasons=[f"safety_level:{classification.safety_level.value}"])
        return self.sm.apply(case_id, outcome, payload, Component.CLASSIFIER_SERVICE)

    def _plan(self, case_id: str) -> TransitionResult:
        case = self.sm.load(case_id)
        plan = self.planner.plan(case.intent or "", self._request_text(case_id))
        if not plan.plan_complete:  # §3.1 PlanComplete: the case does not move to Planning
            return self._fail(case_id, State.CLASSIFIED, "plan_incomplete")
        payload = {"plan_complete": True, "ordered_steps": plan.ordered_steps}
        return self.sm.apply(case_id, Event.PLAN_CREATED, payload, Component.PLANNER_SERVICE)

    def _planning(self, case_id: str, last_event: str | None) -> TransitionResult:
        if last_event == Event.DATA_RETRIEVED.value:
            return self.sm.apply(case_id, Event.STEP_ADVANCED, {}, Component.AGENT_ORCHESTRATOR)
        if last_event == Event.ACTION_PROPOSED.value:
            return self._decide_and_execute(case_id)
        case = self.sm.load(case_id)
        proposal = self.planner.propose(case.ordered_steps or [], case.current_step or 0, last_event or "")
        return self.sm.apply(case_id, Event.ACTION_PROPOSED, {"proposed_action": proposal}, Component.PLANNER_SERVICE)

    def _decide_and_execute(self, case_id: str) -> TransitionResult:
        case = self.sm.load(case_id)
        action = case.current_action
        target, fields = ACTION_TARGETS[action.value]
        request = PolicyRequest(
            execution_id=f"EXEC-{uuid.uuid4().hex[:12]}",
            proposed_action=ProposedAction(action.value, case.current_step, target, fields),
            outgoing_message=self._evaluated_message(case_id) if action is Action.SEND_STATUS_UPDATE else None,
            instruction_source=INSTRUCTION_SOURCE if action is Action.LOAD_INSTRUCTIONS else None,
        )
        decided = self.policy.apply(self.sm, case_id, request)
        if decided.committed and decided.state_after in EXECUTING_STATES:
            return self.executor.execute(case_id, request.execution_id)
        return decided

    def _evaluated_message(self, case_id: str) -> OutgoingMessage:
        """The template message, kept in the Data Log and classified by the Response Evaluator."""
        case = self.sm.load(case_id)
        text = status_message(case, INSTRUCTION_SOURCE)
        with self.sm.engine.begin() as conn:
            entry = data_log.record(conn, case_id, case.patient_id, data_log.DataKind.OUTGOING_MESSAGE, text,
                                    self.sm.clock())
        medical = self.evaluator.evaluate(text)
        return OutgoingMessage(evaluated=True, medical_content_flag=medical, content_hash=entry.content_hash)

    def _deliver(self, case_id: str) -> TransitionResult:
        planned = self.sm.apply(case_id, Event.DELIVERY_PLANNED, {}, Component.AGENT_ORCHESTRATOR)
        if planned.committed or planned.temporal_violation is not None:
            return planned
        return self._fail(case_id, State.READY, f"delivery_not_planned:{planned.reason}")

    # --- helpers -------------------------------------------------------------------------

    def _fail(self, case_id: str, state: State, reason: str) -> TransitionResult:
        kind, source = FAILURE[state]
        return self.sm.escalation.signal(case_id, kind, state, source, reasons=[reason])

    def _request_text(self, case_id: str) -> str:
        with self.sm.engine.connect() as conn:
            requests = [e.content for e in data_log.entries(conn, case_id, data_log.DataKind.REQUEST_TEXT) if e.content]
        return requests[-1] if requests else ""

    def _trace(self, case_id: str) -> list[repository.AuditEntry]:
        with self.sm.engine.connect() as conn:
            return repository.load_trace(conn, case_id)

    def _active_case_ids(self) -> list[str]:
        with self.sm.engine.connect() as conn:
            return [case.case_id for state in ACTIVE_STATES for case in repository.list_cases(conn, state)]


def _last_transition_event(trace: list[repository.AuditEntry]) -> str | None:
    return next((row.event for row in reversed(trace) if row.record_type == "Transition"), None)


def _blocked_since_last_transition(trace: list[repository.AuditEntry]) -> int:
    count = 0
    for row in reversed(trace):
        if row.record_type == "Transition":
            break
        if row.record_type == "Blocked":
            count += 1
    return count
