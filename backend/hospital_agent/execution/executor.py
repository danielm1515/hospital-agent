"""Tool Executor - the only component that calls an external system (spec §1, §3.1, §12; Execution design §3).

execute() runs one accepted decision end to end:

    start   (one transaction)  re-verify the decision, attempt_count+1, the STARTED/AUDIT pair
    call    (no transaction)   the external system, with only the minimized patient fields
    finish  (one transaction)  the outcome row + the event that follows (Retry Manager on failure)

A start that fails re-verification makes no call and escalates as ExecutionUnknown
(Execution design decision 2) - unless the case has already left RetrievingData /
Delivering (a replayed decision), where the Blocked start row is the whole answer; a start refused for exhausted attempts emits
RETRY_EXHAUSTED; a temporal violation escalates as TemporalViolation.
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
        result = self.gateway.call(execution.action, self._parameters(case, execution), execution.idempotency_key)
        return self.finish(case_id, execution, result)

    def finish(self, case_id: str, execution: ExecutionRecord, result: ToolResult) -> TransitionResult:
        sm = self.state_manager
        execution_id = execution.execution_id
        if result.kind == OK:
            outcome = ExecutionOutcome(execution_id, "succeeded")
            if execution.action == Action.SEND_STATUS_UPDATE.value:
                return sm.apply(case_id, Event.CASE_RESOLVED, {"execution_id": execution_id},
                                Component.RESPONSE_DELIVERY, execution_outcome=outcome)
            return sm.apply(case_id, Event.DATA_RETRIEVED, {"execution_id": execution_id, **result.data},
                            Component.TOOL_EXECUTOR, execution_outcome=outcome)

        reason = f"tool:{result.kind}:{result.data.get('error', '')}".rstrip(":")
        outcome = ExecutionOutcome(execution_id, "failed", reason)
        case = sm.load(case_id)
        verdict = after_failure(case, idempotent=self.gateway.idempotent(execution.action),
                                transient=result.kind == TRANSIENT_FAILURE)
        if verdict.escalation is not None:
            return sm.escalation.signal(case_id, verdict.escalation, case.state, Component.TOOL_EXECUTOR,
                                        reasons=[reason], execution_outcome=outcome)
        payload = {"execution_id": execution_id, "idempotency_key": execution.idempotency_key, "idempotent": True}
        return sm.apply(case_id, verdict.event, payload, Component.TOOL_EXECUTOR, execution_outcome=outcome)

    @staticmethod
    def _parameters(case: CaseRecord, execution: ExecutionRecord) -> dict[str, Any]:
        """Only the patient fields the target may receive (spec §11), plus the message reference."""
        _, fields = ACTION_TARGETS[execution.action]
        parameters: dict[str, Any] = {name: getattr(case, name) for name in fields}
        if execution.action == Action.SEND_STATUS_UPDATE.value:
            parameters["content_hash"] = execution.content_hash
        return parameters
