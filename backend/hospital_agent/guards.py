"""Guards of spec §3.1, plus the three conditions the §3 table writes in prose.

A guard takes a GuardContext and returns None when it holds, or a reason code
when it does not. Most failures are "guard_failed"; the approval and escalation
guards return the specific codes the spec names.

§3.1's "who evaluates" column is enforced here. A fact that another component
determines ("X קובע · State Manager מאמת") is trusted only from its owning
component: for a system-owned event, the State Manager has already checked
that the event came from its owner (§13.2), so the guard can read the fact
straight from the payload; for an external event (DOCUMENT_UPLOADED has no
owner), the guard itself checks GuardContext.source against the component that
is supposed to have determined the fact (DocumentValid requires
Component.SESSION_SERVICE). A guard marked "State Manager, מה־State השמור"
reads only the cases row.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .case import MAX_ATTEMPTS, ApprovalRecord, CaseRecord, ExecutionRecord, compute_plan_hash
from .naming import AUTOMATIC_ACTIONS, RETRIEVAL_ACTIONS, Action, Component, EscalationKind, Event, SafetyLevel, State

GUARD_FAILED = "guard_failed"
INVALID_ESCALATION_REASON = "invalid_escalation_reason"
WORKFLOW_DECISION_INVALID = "workflow_decision_invalid"

SUPPORTED_DOCUMENT_FORMATS = frozenset({"pdf", "jpg", "png"})
REVIEWER_ROLES = frozenset({"clinical_staff", "admin_staff"})
DECISION_FOR_EVENT = {
    Event.HUMAN_APPROVED: "approve",
    Event.HUMAN_REJECTED: "reject",
    Event.HUMAN_RESOLVED_CASE: "resolve",
}


@dataclass(frozen=True)
class GuardPorts:
    """Guards evaluated by components the Core does not contain yet.

    Every port must be passed explicitly - there is no permissive default. Tests
    use the fakes in tests/fakes.py; sub-project 3 supplies the real Tool Executor.
    """

    executor_reverified: Callable[[GuardContext], bool]


@dataclass(frozen=True)
class GuardContext:
    case: CaseRecord | None  # None only for REQUEST_SUBMITTED (source state Initial)
    event: Event
    payload: Mapping[str, Any]
    now: datetime
    ports: GuardPorts
    source: Component = Component.EXTERNAL  # who emitted the event; populated by the State Manager
    approval: ApprovalRecord | None = None  # loaded from approvals by payload["approval_id"]
    execution: ExecutionRecord | None = None  # loaded from executions by payload["execution_id"]
    escalation_kinds: frozenset[EscalationKind] = frozenset()  # allowlist of the row being evaluated
    approval_already_used: bool = False  # payload["approval_id"] already appears on a committed Transition row


Guard = Callable[[GuardContext], str | None]


def _check(condition: bool) -> str | None:
    return None if condition else GUARD_FAILED


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _valid_plan(steps: object) -> bool:
    """Steps numbered 1..n, only the keys step/action, only automatic actions (§5)."""
    if not isinstance(steps, list) or not steps:
        return False
    automatic = {a.value for a in AUTOMATIC_ACTIONS}
    return all(
        isinstance(s, Mapping) and set(s) == {"step", "action"} and s["step"] == i and s["action"] in automatic
        for i, s in enumerate(steps, start=1)
    )


# --- §3.1 guards, in the order of the spec table ---------------------------------


def patient_identified(ctx: GuardContext) -> str | None:
    return _check(_nonempty(ctx.payload.get("patient_id")))


def request_valid(ctx: GuardContext) -> str | None:
    document = ctx.payload.get("document")
    document_ok = document is None or (
        isinstance(document, Mapping) and document.get("format") in SUPPORTED_DOCUMENT_FORMATS
    )
    return _check(_nonempty(ctx.payload.get("text")) and document_ok)


def plan_complete(ctx: GuardContext) -> str | None:
    return _check(ctx.payload.get("plan_complete") is True and _valid_plan(ctx.payload.get("ordered_steps")))


def in_plan(ctx: GuardContext) -> str | None:
    case, proposal = ctx.case, ctx.payload.get("proposed_action")
    if case is None or case.current_action is None or not isinstance(proposal, Mapping):
        return GUARD_FAILED
    return _check(proposal.get("action") == case.current_action.value and proposal.get("from_step") == case.current_step)


def identity_verified(ctx: GuardContext) -> str | None:
    if ctx.case is not None and ctx.case.identity_verified:
        return None
    # REQUEST_VALIDATED is where the Session Service's verification is written to State.
    return _check(ctx.event is Event.REQUEST_VALIDATED and ctx.payload.get("identity_verified") is True)


def attempts_available(ctx: GuardContext) -> str | None:
    return _check(ctx.case is not None and ctx.case.attempt_count < MAX_ATTEMPTS)


def is_idempotent(ctx: GuardContext) -> str | None:
    return _check(_nonempty(ctx.payload.get("idempotency_key")) and ctx.payload.get("idempotent") is True)


def readiness_complete(ctx: GuardContext) -> str | None:
    # Computed from tool results stored in State - never declared by the caller.
    return _check(ctx.case is not None and ctx.case.readiness_complete)


def delivery_confirmed(ctx: GuardContext) -> str | None:
    case, execution = ctx.case, ctx.execution
    return _check(
        case is not None
        and execution is not None
        and execution.case_id == case.case_id
        and execution.step == case.current_step
        and execution.action == Action.SEND_STATUS_UPDATE.value
        and execution.status == "succeeded"
    )


def readiness_in_progress(ctx: GuardContext) -> str | None:
    case = ctx.case
    return _check(case is not None and case.plan_hash is not None and case.required_documents is not None)


def plan_intact(ctx: GuardContext) -> str | None:
    case = ctx.case
    return _check(
        case is not None and case.ordered_steps is not None and case.plan_hash == compute_plan_hash(case.ordered_steps)
    )


def can_advance(ctx: GuardContext) -> str | None:
    case = ctx.case
    return _check(
        case is not None
        and case.ordered_steps is not None
        and case.current_step is not None
        and case.current_step + 1 <= len(case.ordered_steps)
    )


def retrieval_steps_remain(ctx: GuardContext) -> str | None:
    case = ctx.case
    return _check(
        case is not None
        and case.current_action in {Action.CHECK_APPOINTMENT, Action.CHECK_DOCUMENTS}
        and case.next_action in RETRIEVAL_ACTIONS
    )


def pre_readiness_phase_complete(ctx: GuardContext) -> str | None:
    case = ctx.case
    if case is None or case.current_action is not Action.LOAD_INSTRUCTIONS:
        return GUARD_FAILED
    earlier = [case.step_action(step) for step in range(1, case.current_step)]
    return _check(all(action in RETRIEVAL_ACTIONS for action in earlier))


def delivery_step_pending(ctx: GuardContext) -> str | None:
    case = ctx.case
    return _check(
        readiness_complete(ctx) is None
        and case.current_action is Action.LOAD_INSTRUCTIONS
        and case.next_action is Action.SEND_STATUS_UPDATE
    )


def _not_expired(expires_at: object, now: datetime) -> bool:
    """F3(a): expires_at is an aware datetime, an ISO-8601 string with a timezone, or absent.

    Anything unparsable or naive fails the guard - it never raises.
    """
    if expires_at is None:
        return True
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            return False
    if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
        return False
    return expires_at > now


def document_valid(ctx: GuardContext) -> str | None:
    case, document = ctx.case, ctx.payload.get("document")
    if case is None or not isinstance(document, Mapping):
        return GUARD_FAILED
    return _check(
        ctx.source is Component.SESSION_SERVICE
        and _nonempty(document.get("document_id"))
        and document.get("format") in SUPPORTED_DOCUMENT_FORMATS
        and document.get("patient_id") == case.patient_id
        and _not_expired(document.get("expires_at"), ctx.now)
    )


def workflow_decision_valid(ctx: GuardContext) -> str | None:
    """§3.1 + §12.4/§12.5, checked against the approvals row - never against payload flags."""
    case, approval = ctx.case, ctx.approval
    if case is None or approval is None:
        return WORKFLOW_DECISION_INVALID
    if (
        approval.approval_type != "WorkflowDecision"
        or approval.case_id != case.case_id
        or approval.patient_id != case.patient_id
        or approval.reviewer_role not in REVIEWER_ROLES
        or not all(_nonempty(v) for v in (approval.reviewer_id, approval.reason, approval.shown_context_ref))
        or not approval.granted_at <= ctx.now < approval.valid_until
        or approval.consumed_at is not None
        or approval.escalation_kind != case.escalation_kind
        or ctx.approval_already_used
    ):
        return WORKFLOW_DECISION_INVALID
    if approval.decision != DECISION_FOR_EVENT.get(ctx.event):
        return "approval_decision_mismatch"
    if ctx.event is Event.HUMAN_APPROVED:
        kind = case.escalation_kind
        if kind is EscalationKind.PATIENT_VERIFICATION_FAILED and not _nonempty(approval.verified_identity_ref):
            return "identity_not_established"
        if kind in {EscalationKind.Z3_COUNTEREXAMPLE, EscalationKind.PATIENT_SLA_EXPIRED} and approval.patient_deadline is None:
            return "patient_deadline_missing"
        if kind is EscalationKind.POLICY_REVIEW and (
            approval.plan_hash != case.plan_hash or approval.current_step != case.current_step
        ):
            return WORKFLOW_DECISION_INVALID
    return None


def executor_reverified(ctx: GuardContext) -> str | None:
    return _check(ctx.ports.executor_reverified(ctx))


def patient_sla_expired(ctx: GuardContext) -> str | None:
    case = ctx.case
    return _check(
        case is not None
        and case.state is State.AWAITING_PATIENT_INPUT
        and case.patient_deadline is not None
        and case.patient_deadline <= ctx.now
        and ctx.payload.get("registered_state_version") == case.state_version
    )


def system_escalation_required(ctx: GuardContext) -> str | None:
    case = ctx.case
    valid = (
        case is not None
        and ctx.payload.get("escalation_kind") in ctx.escalation_kinds
        and ctx.payload.get("escalated_from_state") == case.state.value
    )
    return None if valid else INVALID_ESCALATION_REASON


def ask_patient_safe(ctx: GuardContext) -> str | None:
    # Readiness Check with Z3 determines it; only UNSAT means safe (§9.1).
    return _check(ctx.payload.get("z3_result") == "unsat")


# --- conditions the §3 table writes in prose ------------------------------------


def retrieval_action(ctx: GuardContext) -> str | None:
    """"proposed_action ∈ retrieval_actions" - the current plan step, confirmed by the Policy decision."""
    case = ctx.case
    return _check(
        case is not None
        and case.current_action in RETRIEVAL_ACTIONS
        and ctx.payload.get("action") == case.current_action.value
    )


def delivery_action(ctx: GuardContext) -> str | None:
    """"proposed_action = SendStatusUpdate"."""
    case = ctx.case
    return _check(
        case is not None
        and case.current_action is Action.SEND_STATUS_UPDATE
        and ctx.payload.get("action") == Action.SEND_STATUS_UPDATE.value
    )


def closure_reason(ctx: GuardContext) -> str | None:
    """"קיימת סיבת סגירה" - the resolving approval carries a reason."""
    return _check(ctx.approval is not None and _nonempty(ctx.approval.reason))


def valid_classification(ctx: GuardContext) -> str | None:
    """F3(b): payload["safety_level"] must be one of the four SafetyLevel values (§14 invalid_safety_level).

    An unrecognised value is malformed input, not a missing rule - it must never reach
    apply_effects(), which would otherwise raise constructing SafetyLevel(...).
    """
    level = ctx.payload.get("safety_level")
    valid = isinstance(level, SafetyLevel) or (isinstance(level, str) and level in {s.value for s in SafetyLevel})
    return None if valid else "invalid_safety_level"


def _valid_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() != "" for item in value)


def valid_tool_result(ctx: GuardContext) -> str | None:
    """F3(c): required_documents / held_documents, when present, must be lists of non-empty strings.

    Otherwise a string payload silently becomes a list of characters in apply_effects().
    appointment_at (Execution design §5), when present, must be a timezone-aware datetime.
    """
    payload = ctx.payload
    for key in ("required_documents", "held_documents"):
        if key in payload and not _valid_string_list(payload[key]):
            return "invalid_tool_result"
    if "appointment_at" in payload:
        at = payload["appointment_at"]
        if not (isinstance(at, datetime) and at.tzinfo is not None):
            return "invalid_tool_result"
    return None


def deadline_registered(ctx: GuardContext) -> str | None:
    """F3(d): MISSING_INFORMATION_DETECTED must carry a future, timezone-aware patient_deadline.

    Otherwise the case would commit to AwaitingPatientInput with a NULL deadline, and
    PatientSlaExpired could never fire.
    """
    deadline = ctx.payload.get("patient_deadline")
    valid = isinstance(deadline, datetime) and deadline.tzinfo is not None and deadline > ctx.now
    return None if valid else "patient_deadline_missing"


# PascalCase keys are §3.1 guard names (tests/test_guards.py checks them against the
# spec); snake_case keys are the prose conditions above.
GUARDS: dict[str, Guard] = {
    "PatientIdentified": patient_identified,
    "RequestValid": request_valid,
    "PlanComplete": plan_complete,
    "InPlan": in_plan,
    "IdentityVerified": identity_verified,
    "AttemptsAvailable": attempts_available,
    "IsIdempotent": is_idempotent,
    "ReadinessComplete": readiness_complete,
    "DeliveryConfirmed": delivery_confirmed,
    "ReadinessInProgress": readiness_in_progress,
    "PlanIntact": plan_intact,
    "CanAdvance": can_advance,
    "RetrievalStepsRemain": retrieval_steps_remain,
    "PreReadinessPhaseComplete": pre_readiness_phase_complete,
    "DeliveryStepPending": delivery_step_pending,
    "DocumentValid": document_valid,
    "WorkflowDecisionValid": workflow_decision_valid,
    "ExecutorReverified": executor_reverified,
    "PatientSlaExpired": patient_sla_expired,
    "SystemEscalationRequired": system_escalation_required,
    "AskPatientSafe": ask_patient_safe,
    "retrieval_action": retrieval_action,
    "delivery_action": delivery_action,
    "closure_reason": closure_reason,
    "valid_classification": valid_classification,
    "valid_tool_result": valid_tool_result,
    "deadline_registered": deadline_registered,
}
