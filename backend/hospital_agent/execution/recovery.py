"""Recovery after a restart (spec §12.2, §14; Execution design §5).

An execution that started but has no outcome may or may not have reached the external
system. It is never replayed: its row becomes 'unknown', an ExecutionUnknown outcome row
is written, and the case escalates to a human - all in one transaction. Rows still in
'intent' made no call and are left for the orchestrator.
"""
from __future__ import annotations

from .. import repository
from ..naming import Component, EscalationKind
from ..state_manager import ExecutionOutcome, StateManager, TransitionResult


def recover(state_manager: StateManager) -> list[TransitionResult]:
    with state_manager.engine.connect() as conn:
        running = repository.executions_with_status(conn, "started")
    results = []
    for execution in running:
        case = state_manager.load(execution.case_id)
        results.append(state_manager.escalation.signal(
            execution.case_id, EscalationKind.EXECUTION_UNKNOWN, case.state, Component.TOOL_EXECUTOR,
            reasons=["execution_unknown:restart"],
            execution_outcome=ExecutionOutcome(execution.execution_id, "unknown", "restart"),
        ))
    return results
