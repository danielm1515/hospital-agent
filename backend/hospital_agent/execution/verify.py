"""ExecutorReverified (spec §3.1) - the Tool Executor's two checks of a Policy decision.

1. verify_decision(ctx): the GuardPorts.executor_reverified port, evaluated on the
   POLICY_ALLOWED transition. The decision must have been computed on exactly the
   State the case is in now (decided_state_version, plan_hash, current_step, action),
   and its execution_id must be new.
2. verify_start(case, execution, approval, now): right before the call, inside the
   ExecutionStarted transaction. The executions row written with POLICY_ALLOWED must
   still match the case - nothing may have changed since the decision was accepted -
   and a medical output needs its ContentApproval to be valid still.

Both are pure: they read only what they are given (Execution design §3).
"""
from __future__ import annotations

from datetime import datetime

from ..case import ApprovalRecord, CaseRecord, ExecutionRecord, compute_plan_hash
from ..guards import GuardContext
from ..naming import State
from ..policy.approvals import content_approval_valid

REVERIFICATION_FAILED = "executor_reverification_failed"
EXECUTING_STATES = frozenset({State.RETRIEVING_DATA, State.DELIVERING})


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _plan_matches(case: CaseRecord, plan_hash: object) -> bool:
    return (case.ordered_steps is not None and plan_hash == case.plan_hash
            and case.plan_hash == compute_plan_hash(case.ordered_steps))


def verify_decision(ctx: GuardContext) -> bool:
    case, p = ctx.case, ctx.payload
    return (
        case is not None
        and _nonempty(p.get("decision_token"))
        and _nonempty(p.get("execution_id"))
        and ctx.execution is None  # the execution_id has never been used
        and p.get("decided_state_version") == case.state_version
        and _plan_matches(case, p.get("plan_hash"))
        and p.get("current_step") == case.current_step
        and case.current_action is not None
        and p.get("action") == case.current_action.value
    )


def verify_start(
    case: CaseRecord, execution: ExecutionRecord | None, approval: ApprovalRecord | None, now: datetime
) -> str | None:
    """None when the call may go out, otherwise the anomaly reason (no external call, §14)."""
    valid = (
        execution is not None
        and execution.status == "intent"
        and case.state in EXECUTING_STATES
        and execution.case_id == case.case_id
        and execution.patient_id == case.patient_id
        and case.identity_verified
        and _nonempty(case.case_id) and _nonempty(case.patient_id) and _nonempty(execution.execution_id)
        and _nonempty(execution.decision_token)
        and _nonempty(execution.idempotency_key)
        and execution.state_version == case.state_version
        and _plan_matches(case, execution.plan_hash)
        and execution.step == case.current_step
        and case.current_action is not None
        and execution.action == case.current_action.value
    )
    if not valid:
        return REVERIFICATION_FAILED
    if execution.medical_content_flag and not content_approval_valid(
        approval,
        case_id=case.case_id,
        patient_id=case.patient_id,
        execution_id=execution.execution_id,
        action=execution.action,
        content_hash=execution.content_hash,
        now=now,
    ):
        return REVERIFICATION_FAILED
    return None
