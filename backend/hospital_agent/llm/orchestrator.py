"""Agent Orchestrator (spec §1; LLM design §5, decision 7).

It keeps no state of its own: every step reads the case from the database and does the one
thing that State calls for, so a restart simply carries on (the State is in Postgres).

    Classifying         Intent + Safety in parallel (D21) -> MEDICAL_QUESTION_DETECTED /
                        SafetyEscalation / INTENT_CLASSIFIED (§14 precedence)
    Classified          Planner: plan -> PLAN_CREATED, or PlanningFailed (plan_complete=false)
    Planning            after DATA_RETRIEVED: STEP_ADVANCED; after ACTION_PROPOSED: the Policy
                        Service decides and the Tool Executor runs an allowed call; otherwise
                        the Planner proposes the next step (ACTION_PROPOSED)
    AssessingReadiness  the Readiness Check (Z3); stuck there, it escalates as Z3Counterexample
                        - the only non-temporal kind on that State's §3 allowlist
    Ready               DELIVERY_PLANNED, or DeliveryStepMissing

Every other State waits for someone else (the patient, a reviewer, the SLA Worker) or is
final. Failures fail closed (§14): three unusable LLM answers in a row, or three blocked
events in a row in one State, escalate with that State's failure kind; three InPlan
rejections of the same step are exactly the latter (§3.1 InPlan). A Blocked row for an
injected system-owned event (§13.2) never counts - outside input cannot force an
escalation. An exception from the Tool Executor after POLICY_ALLOWED escalates at once as
ExecutionUnknown, never leaving the case in RetrievingData / Delivering. tick() also fails
closed on its own errors: a case whose step raises an unexpected exception three ticks
in a row (counted in memory per case, reset on a tick that does not raise) escalates the
same way, and neither that nor a failure listing the active cases is ever allowed to
stop the other cases or kill the background thread.

Only uploads the case accepted are classified: an uploaded document reaches the LLM only if
its content_hash is on a committed DOCUMENT_UPLOADED row that moved the case to Classifying
(D25, §12.3) - a rejected upload is never read, tombstoned or not.

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
from ..state_manager import ExecutionOutcome, StateManager, TransitionResult
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
    State.ASSESSING_READINESS: (EscalationKind.Z3_COUNTEREXAMPLE, Component.READINESS_CHECK),
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
        self._consecutive_errors: dict[str, int] = {}
        self._background_stop: threading.Event | None = None
        self._background_thread: threading.Thread | None = None

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
        """One pass over every case that is waiting for the orchestrator.

        Guarded end to end (§14 fail closed applied to the orchestrator itself): listing
        the active cases is guarded so a transient DB error there cannot kill
        run_in_background's loop, and one case's step raising is caught so it never stops
        the others. A case whose step keeps raising is escalated after
        MAX_CONSECUTIVE_BLOCKED consecutive failures - counted in memory, reset the moment
        a tick runs it without raising - and that escalation call is itself guarded too.
        """
        try:
            case_ids = self._active_case_ids()
        except Exception as exc:
            logger.error("orchestrator tick failed: %s", type(exc).__name__)
            return
        for case_id in list(self._consecutive_errors):  # a case no longer active starts afresh
            if case_id not in case_ids:
                self._consecutive_errors.pop(case_id, None)
        for case_id in case_ids:
            try:
                self.run_case(case_id)
            except Exception as exc:  # one broken case never stops the others; no patient data logged (§12.3)
                logger.error("orchestrator step failed: %s", type(exc).__name__)
                count = self._consecutive_errors[case_id] = self._consecutive_errors.get(case_id, 0) + 1
                if count >= MAX_CONSECUTIVE_BLOCKED:
                    self._fail_on_error(case_id, exc)
            else:
                self._consecutive_errors.pop(case_id, None)

    def _fail_on_error(self, case_id: str, exc: Exception) -> None:
        """§14: three unexpected exceptions in a row on one case escalate it, never crash the tick."""
        try:
            case = self.sm.load(case_id)
            if case.state in FAILURE:
                self._fail(case_id, case.state, f"orchestrator_error:{type(exc).__name__}")
        except Exception as inner:
            logger.error("orchestrator tick failed: %s", type(inner).__name__)
        finally:
            self._consecutive_errors.pop(case_id, None)

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

        thread = threading.Thread(target=loop, name="agent-orchestrator", daemon=True)
        self._background_stop, self._background_thread = stop, thread
        thread.start()
        return stop

    def close(self) -> None:
        """Stop the background thread (if any) and join it before closing the Evaluator's
        process pool, so a tick is never left running against a closed Evaluator."""
        if self._background_stop is not None:
            self._background_stop.set()
            self.wake()
            self._background_thread.join(timeout=10)
        self.evaluator.close()

    # --- per State -----------------------------------------------------------------------

    def _classify(self, case_id: str) -> TransitionResult:
        case = self.sm.load(case_id)
        with self.sm.engine.connect() as conn:
            requests = [e.content for e in data_log.entries(conn, case_id, data_log.DataKind.REQUEST_TEXT) if e.content]
            accepted = _accepted_uploads(repository.load_trace(conn, case_id))
            documents = [e.content for e in data_log.entries(conn, case_id, data_log.DataKind.UPLOADED_DOCUMENT)
                         if e.content and e.content_hash in accepted]
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
            try:
                return self.executor.execute(case_id, request.execution_id)
            except Exception as exc:  # §14: never leave the case in RetrievingData / Delivering
                return self._fail_execution(case_id, request.execution_id, exc)
        return decided

    def _fail_execution(self, case_id: str, execution_id: str, exc: Exception) -> TransitionResult | None:
        """An infrastructure error inside the Tool Executor: escalate as ExecutionUnknown if the
        case is still mid-call, with the execution's outcome 'unknown' if it had started (an
        'intent' row never called anything). Guarded; logs the exception type only (§12.3)."""
        logger.error("tool execution failed: %s", type(exc).__name__)
        try:
            case = self.sm.load(case_id)
            if case.state not in EXECUTING_STATES:
                return None
            with self.sm.engine.connect() as conn:
                execution = repository.load_execution(conn, execution_id)
            outcome = (ExecutionOutcome(execution_id, "unknown", "orchestrator_error")
                       if execution is not None and execution.status == "started" else None)
            return self.sm.escalation.signal(case_id, EscalationKind.EXECUTION_UNKNOWN, case.state,
                                             Component.TOOL_EXECUTOR, reasons=[f"execution_error:{type(exc).__name__}"],
                                             execution_outcome=outcome)
        except Exception as inner:
            logger.error("tool execution escalation failed: %s", type(inner).__name__)
            return None

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
    """Blocked rows since the last transition, not counting injected system-owned events
    (§13.2) - otherwise outside input could force an escalation."""
    count = 0
    for row in reversed(trace):
        if row.record_type == "Transition":
            break
        if row.record_type == "Blocked" and "system_owned_event" not in row.policy_reasons:
            count += 1
    return count


def _accepted_uploads(trace: list[repository.AuditEntry]) -> set[str]:
    """content_hash of every upload the case accepted: a committed DOCUMENT_UPLOADED into Classifying."""
    return {row.content_hash for row in trace
            if row.record_type == "Transition" and row.event == Event.DOCUMENT_UPLOADED.value
            and row.state_after == State.CLASSIFYING.value and row.content_hash}
