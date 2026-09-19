"""Recovery after a restart (spec §12.2, §14, D28; Execution design §5).

An execution that started but has no outcome may or may not have reached the external
system. It is never replayed: its row becomes 'unknown', an ExecutionUnknown outcome row
is written, and the case escalates to a human - all in one transaction.

An execution still in 'intent' made no call, but its case can still be stranded on it: if
the process died between POLICY_ALLOWED (which wrote the intent row) and start_execution,
the case is sitting in RetrievingData / Delivering waiting on a decision no one will ever
start. Such a row is escalated too - no call was made, so there is no outcome row and
nothing to replay - and then marked 'failed' so a later recovery leaves it alone. An
'intent' row whose case has moved on (a stale row from a decision that was superseded) is
left untouched.
"""
from __future__ import annotations

from .. import repository
from ..naming import Component, EscalationKind
from ..state_manager import ExecutionOutcome, StateManager, TransitionResult
from .verify import EXECUTING_STATES


def recover(state_manager: StateManager) -> list[TransitionResult]:
    return [*_recover_started(state_manager), *_recover_stranded_intents(state_manager)]


def _recover_started(state_manager: StateManager) -> list[TransitionResult]:
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


def _recover_stranded_intents(state_manager: StateManager) -> list[TransitionResult]:
    with state_manager.engine.connect() as conn:
        stranded = repository.executions_with_status(conn, "intent")
    results = []
    for execution in stranded:
        case = state_manager.load(execution.case_id)
        if case.state not in EXECUTING_STATES or execution.state_version != case.state_version:
            continue  # not the decision this case is waiting on - a stale intent, left alone
        result = state_manager.escalation.signal(
            execution.case_id, EscalationKind.EXECUTION_UNKNOWN, case.state, Component.TOOL_EXECUTOR,
            reasons=["execution_unknown:restart_before_start"],
        )
        results.append(result)
        if result.committed:
            # If the process dies here, the next recovery finds the case no longer executing
            # (it is now AwaitingHumanReview) and skips this row.
            with state_manager.engine.begin() as conn:
                repository.set_execution_status(conn, execution.execution_id, ("intent",), "failed", state_manager.clock())
    return results
