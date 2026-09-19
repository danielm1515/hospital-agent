"""Tool Executor - the only component that calls an external system (spec §1, §3.1, §12; Execution design §3).

execute() runs one accepted decision end to end:

    start   (one transaction)  re-verify the decision, attempt_count+1, the STARTED/AUDIT pair
    call    (no transaction)   the external system, with only the minimized patient fields
    finish  (one transaction)  the outcome row + the event that follows (Retry Manager on failure)

It also emits CASE_RESOLVED on behalf of Response Delivery, and a temporal violation from
`start_execution` is routed through the Temporal Monitor's escalation signal.

A start that fails re-verification makes no call and escalates as ExecutionUnknown
(Execution design decision 2) - unless the case has already left RetrievingData /
Delivering (a replayed decision), where the Blocked start row is the whole answer; a start refused for exhausted attempts emits
RETRY_EXHAUSTED; a temporal violation escalates as TemporalViolation. Fail closed (spec §14) also
covers two later anomalies, both already-recorded outcomes with a stranded case: the result
event itself blocked by its own guards (e.g. `invalid_tool_result`), and the external call
raising instead of returning - both escalate as ExecutionUnknown.
"""
from __future__ import annotations

from typing import Any

from ..case import CaseRecord, ExecutionRecord
from ..naming import Action, Component, EscalationKind, Event
from ..state_manager import ExecutionOutcome, StateManager, TransitionResult
from .gateway import ACTION_TARGETS, OK, TRANSIENT_FAILURE, ToolGateway, ToolResult
from .retry import after_failure
from .verify import EXECUTING_STATES


class ToolExecutor:
    def __init__(self, state_manager: StateManager, gateway: ToolGateway) -> None:
        self.state_manager, self.gateway = state_manager, gateway

    def execute(self, case_id: str, execution_id: str) -> TransitionResult:
        sm = self.state_manager
        start = sm.start_execution(case_id, execution_id)
        if not start.started:
            case = sm.load(case_id)
            if case.state not in EXECUTING_STATES:
                return TransitionResult(case_id, False, case.state, case.state, reason=start.reason)
            if start.temporal_violation:
                return sm.escalation.signal(case_id, EscalationKind.TEMPORAL_VIOLATION, case.state,
                                            Component.TEMPORAL_MONITOR, reasons=[start.reason])
            if start.reason == "attempts_exhausted":
                return sm.apply(case_id, Event.RETRY_EXHAUSTED, {"execution_id": execution_id}, Component.TOOL_EXECUTOR)
            return sm.escalation.signal(case_id, EscalationKind.EXECUTION_UNKNOWN, case.state,
                                        Component.TOOL_EXECUTOR, reasons=[start.reason])
        case, execution = sm.load(case_id), start.execution
        try:
            result = self.gateway.call(execution.action, self._parameters(case, execution), execution.idempotency_key)
        except Exception as exc:  # never retry an external system that raised
            return sm.escalation.signal(
                case_id, EscalationKind.EXECUTION_UNKNOWN, case.state, Component.TOOL_EXECUTOR,
                reasons=[f"tool:exception:{type(exc).__name__}"],
                execution_outcome=ExecutionOutcome(execution.execution_id, "unknown", f"exception:{type(exc).__name__}"),
            )
        return self.finish(case_id, execution, result)

    def finish(self, case_id: str, execution: ExecutionRecord, result: ToolResult) -> TransitionResult:
        sm = self.state_manager
        execution_id = execution.execution_id
        if result.kind == OK:
            outcome = ExecutionOutcome(execution_id, "succeeded")
            if execution.action == Action.SEND_STATUS_UPDATE.value:
                return self._apply_result_event(case_id, Event.CASE_RESOLVED, {"execution_id": execution_id},
                                                 Component.RESPONSE_DELIVERY, outcome)
            return self._apply_result_event(case_id, Event.DATA_RETRIEVED, {**result.data, "execution_id": execution_id},
                                             Component.TOOL_EXECUTOR, outcome)

        reason = f"tool:{result.kind}:{result.data.get('error', '')}".rstrip(":")
        outcome = ExecutionOutcome(execution_id, "failed", reason)
        case = sm.load(case_id)
        verdict = after_failure(case, idempotent=self.gateway.idempotent(execution.action),
                                transient=result.kind == TRANSIENT_FAILURE)
        if verdict.escalation is not None:
            return sm.escalation.signal(case_id, verdict.escalation, case.state, Component.TOOL_EXECUTOR,
                                        reasons=[reason], execution_outcome=outcome)
        payload = {"execution_id": execution_id, "idempotency_key": execution.idempotency_key, "idempotent": True}
        return self._apply_result_event(case_id, verdict.event, payload, Component.TOOL_EXECUTOR, outcome)

    def _apply_result_event(self, case_id: str, event: Event, payload: dict[str, Any], source: Component,
                            outcome: ExecutionOutcome) -> TransitionResult:
        """Apply the event that reports outcome; escalate a case its own guards leave stranded.

        The outcome row is written with this event whether or not it commits (§12.2). If the
        event is blocked by a guard other than a temporal violation (already handled by
        StateManager.apply itself) and the case is still mid-call, the outcome can never be
        reported again - fail closed (spec §14) rather than leave the case stuck.
        """
        sm = self.state_manager
        result = sm.apply(case_id, event, payload, source, execution_outcome=outcome)
        if not result.committed and result.temporal_violation is None:
            case = sm.load(case_id)
            if case.state in EXECUTING_STATES:
                return sm.escalation.signal(case_id, EscalationKind.EXECUTION_UNKNOWN, case.state,
                                            Component.TOOL_EXECUTOR, reasons=[f"result_event_blocked:{result.reason}"])
        return result

    @staticmethod
    def _parameters(case: CaseRecord, execution: ExecutionRecord) -> dict[str, Any]:
        """Only the patient fields the target may receive (spec §11), plus the message reference."""
        _, fields = ACTION_TARGETS[execution.action]
        parameters: dict[str, Any] = {name: getattr(case, name) for name in fields}
        if execution.action == Action.SEND_STATUS_UPDATE.value:
            parameters["content_hash"] = execution.content_hash
        return parameters
