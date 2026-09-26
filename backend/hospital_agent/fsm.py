"""Transition table - spec §3, the single source of truth for State changes.

41 rows. tests/test_fsm.py compares (source, event, spec_guard, target) of every
row with docs/spec/03-transitions-guards.md, so the code cannot drift from the spec.
EXTENSION_TRANSITIONS holds sub-project 15's three rows (docs/spec_corrections.md
rows 83-85), outside that table; resolve() searches both.

A guard name starting with "!" is negated ("לא ReadinessInProgress").
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from .case import CaseRecord, compute_plan_hash
from .guards import GUARD_FAILED, GUARDS, GuardContext
from .naming import EscalationKind as K
from .naming import Event as E
from .naming import SafetyLevel
from .naming import State as S


class Effect(StrEnum):
    """Changes a transition makes to the cases row (design §6.3)."""

    MARK_IDENTITY_VERIFIED = "MarkIdentityVerified"
    RECORD_CLASSIFICATION = "RecordClassification"
    RECORD_PLAN = "RecordPlan"
    ADVANCE_STEP = "AdvanceStep"
    RECORD_RETRIEVAL = "RecordRetrieval"
    RECORD_PATIENT_DEADLINE = "RecordPatientDeadline"
    HOLD_DOCUMENT = "HoldDocument"
    OPEN_RETRY_CYCLE = "OpenRetryCycle"
    APPROVED_PATIENT_DEADLINE = "ApprovedPatientDeadline"
    CLEAR_ESCALATION = "ClearEscalation"
    CONSUME_APPROVAL = "ConsumeApproval"
    RECORD_EXECUTION_INTENT = "RecordExecutionIntent"  # Execution design §3.1 - written by the State Manager
    RECORD_REPLY_REQUEST = "RecordReplyRequest"  # sub-project 15
    CLEAR_REPLY_REQUEST = "ClearReplyRequest"  # sub-project 15


@dataclass(frozen=True)
class EscalationSpec:
    """The escalation a row records: which kinds are allowed and the state it comes from.

    from_payload=True (the HUMAN_REVIEW_REQUIRED rows): the kind is taken from the
    event payload and must be in `kinds`. Otherwise the row has exactly one kind.
    """

    kinds: frozenset[K]
    from_state: S
    from_payload: bool = False


@dataclass(frozen=True)
class Transition:
    source: S | None  # None = Initial
    event: E
    target: S
    spec_guard: str  # the Guard cell of the §3 table, verbatim
    guards: tuple[str, ...] = ()
    requires_escalation: frozenset[K] | None = None  # HUMAN_APPROVED rows: required case.escalation_kind
    escalation: EscalationSpec | None = None
    effects: tuple[Effect, ...] = field(default=())


def _fixed(kind: K, source: S) -> EscalationSpec:
    return EscalationSpec(frozenset({kind}), source)


def _signal(source: S, *kinds: K) -> EscalationSpec:
    return EscalationSpec(frozenset(kinds), source, from_payload=True)


_SYS = ("SystemEscalationRequired",)
_RESUME = (Effect.CLEAR_ESCALATION, Effect.CONSUME_APPROVAL)

TRANSITIONS: tuple[Transition, ...] = (
    Transition(None, E.REQUEST_SUBMITTED, S.RECEIVED, "PatientIdentified", ("PatientIdentified",)),
    Transition(S.RECEIVED, E.REQUEST_VALIDATED, S.CLASSIFYING, "RequestValid, IdentityVerified",
               ("RequestValid", "IdentityVerified"), effects=(Effect.MARK_IDENTITY_VERIFIED,)),
    Transition(S.RECEIVED, E.PATIENT_VERIFICATION_FAILED, S.AWAITING_HUMAN_REVIEW,
               "escalation_kind = PatientVerificationFailed, escalated_from_state = Received",
               escalation=_fixed(K.PATIENT_VERIFICATION_FAILED, S.RECEIVED)),
    Transition(S.CLASSIFYING, E.INTENT_CLASSIFIED, S.CLASSIFIED, "לא ReadinessInProgress",
               ("valid_classification", "!ReadinessInProgress"), effects=(Effect.RECORD_CLASSIFICATION,)),
    Transition(S.CLASSIFYING, E.INTENT_CLASSIFIED, S.ASSESSING_READINESS, "ReadinessInProgress",
               ("valid_classification", "ReadinessInProgress"), effects=(Effect.RECORD_CLASSIFICATION,)),
    Transition(S.CLASSIFYING, E.MEDICAL_QUESTION_DETECTED, S.AWAITING_HUMAN_REVIEW,
               "escalation_kind = MedicalQuestion, escalated_from_state = Classifying",
               escalation=_fixed(K.MEDICAL_QUESTION, S.CLASSIFYING)),
    Transition(S.CLASSIFYING, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {SafetyEscalation, ClassificationFailed, "
               "TemporalViolation}, escalated_from_state = Classifying",
               _SYS, escalation=_signal(S.CLASSIFYING, K.SAFETY_ESCALATION, K.CLASSIFICATION_FAILED,
                                        K.TEMPORAL_VIOLATION)),
    Transition(S.CLASSIFIED, E.PLAN_CREATED, S.PLANNING, "PlanComplete", ("PlanComplete",),
               effects=(Effect.RECORD_PLAN,)),
    Transition(S.CLASSIFIED, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {PlanningFailed, TemporalViolation}, "
               "escalated_from_state = Classified",
               _SYS, escalation=_signal(S.CLASSIFIED, K.PLANNING_FAILED, K.TEMPORAL_VIOLATION)),
    Transition(S.PLANNING, E.ACTION_PROPOSED, S.PLANNING, "InPlan, PlanIntact", ("InPlan", "PlanIntact")),
    Transition(S.PLANNING, E.STEP_ADVANCED, S.PLANNING, "PlanIntact, CanAdvance", ("PlanIntact", "CanAdvance"),
               effects=(Effect.ADVANCE_STEP,)),
    Transition(S.PLANNING, E.POLICY_ALLOWED, S.RETRIEVING_DATA,
               "ExecutorReverified, proposed_action ∈ retrieval_actions", ("ExecutorReverified", "retrieval_action"),
               effects=(Effect.RECORD_EXECUTION_INTENT,)),
    Transition(S.PLANNING, E.POLICY_ALLOWED, S.DELIVERING,
               "ExecutorReverified, proposed_action = SendStatusUpdate", ("ExecutorReverified", "delivery_action"),
               effects=(Effect.RECORD_EXECUTION_INTENT,)),
    Transition(S.PLANNING, E.POLICY_DENIED, S.AWAITING_HUMAN_REVIEW,
               "escalation_kind = PolicyDenied, escalated_from_state = Planning",
               escalation=_fixed(K.POLICY_DENIED, S.PLANNING)),
    Transition(S.PLANNING, E.POLICY_HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "escalation_kind = PolicyReview, escalated_from_state = Planning",
               escalation=_fixed(K.POLICY_REVIEW, S.PLANNING)),
    Transition(S.PLANNING, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {PlanningFailed, TemporalViolation}, "
               "escalated_from_state = Planning",
               _SYS, escalation=_signal(S.PLANNING, K.PLANNING_FAILED, K.TEMPORAL_VIOLATION)),
    Transition(S.RETRIEVING_DATA, E.DATA_RETRIEVED, S.PLANNING, "RetrievalStepsRemain",
               ("valid_tool_result", "RetrievalStepsRemain"), effects=(Effect.RECORD_RETRIEVAL,)),
    Transition(S.RETRIEVING_DATA, E.DATA_RETRIEVED, S.ASSESSING_READINESS, "PreReadinessPhaseComplete",
               ("valid_tool_result", "PreReadinessPhaseComplete"), effects=(Effect.RECORD_RETRIEVAL,)),
    Transition(S.RETRIEVING_DATA, E.TOOL_TRANSIENT_FAILURE, S.PLANNING, "AttemptsAvailable, IsIdempotent",
               ("AttemptsAvailable", "IsIdempotent")),
    Transition(S.RETRIEVING_DATA, E.RETRY_EXHAUSTED, S.AWAITING_HUMAN_REVIEW,
               "escalation_kind = RetryExhausted, escalated_from_state = RetrievingData",
               escalation=_fixed(K.RETRY_EXHAUSTED, S.RETRIEVING_DATA)),
    Transition(S.RETRIEVING_DATA, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {NonIdempotentFailure, ExecutionUnknown, "
               "TemporalViolation}, escalated_from_state = RetrievingData",
               _SYS, escalation=_signal(S.RETRIEVING_DATA, K.NON_IDEMPOTENT_FAILURE, K.EXECUTION_UNKNOWN,
                                        K.TEMPORAL_VIOLATION)),
    Transition(S.DELIVERING, E.CASE_RESOLVED, S.COMPLETED, "DeliveryConfirmed, ReadinessComplete",
               ("DeliveryConfirmed", "ReadinessComplete")),
    Transition(S.DELIVERING, E.TOOL_TRANSIENT_FAILURE, S.PLANNING, "AttemptsAvailable, IsIdempotent",
               ("AttemptsAvailable", "IsIdempotent")),
    Transition(S.DELIVERING, E.RETRY_EXHAUSTED, S.AWAITING_HUMAN_REVIEW,
               "escalation_kind = RetryExhausted, escalated_from_state = Delivering",
               escalation=_fixed(K.RETRY_EXHAUSTED, S.DELIVERING)),
    Transition(S.DELIVERING, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {NonIdempotentFailure, ExecutionUnknown, "
               "TemporalViolation}, escalated_from_state = Delivering",
               _SYS, escalation=_signal(S.DELIVERING, K.NON_IDEMPOTENT_FAILURE, K.EXECUTION_UNKNOWN,
                                        K.TEMPORAL_VIOLATION)),
    Transition(S.ASSESSING_READINESS, E.READINESS_PASSED, S.READY, "ReadinessComplete", ("ReadinessComplete",)),
    Transition(S.ASSESSING_READINESS, E.MISSING_INFORMATION_DETECTED, S.AWAITING_PATIENT_INPUT,
               "לא ReadinessComplete, AskPatientSafe", ("deadline_registered", "!ReadinessComplete", "AskPatientSafe"),
               effects=(Effect.RECORD_PATIENT_DEADLINE,)),
    Transition(S.ASSESSING_READINESS, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {Z3Counterexample (לא AskPatientSafe), "
               "TemporalViolation}, escalated_from_state = AssessingReadiness",
               _SYS, escalation=_signal(S.ASSESSING_READINESS, K.Z3_COUNTEREXAMPLE, K.TEMPORAL_VIOLATION)),
    Transition(S.AWAITING_PATIENT_INPUT, E.DOCUMENT_UPLOADED, S.CLASSIFYING, "DocumentValid", ("DocumentValid",),
               effects=(Effect.HOLD_DOCUMENT,)),
    Transition(S.AWAITING_PATIENT_INPUT, E.DOCUMENT_UPLOADED, S.AWAITING_PATIENT_INPUT, "DocumentValid אינו מתקיים",
               ("!DocumentValid",)),
    Transition(S.AWAITING_PATIENT_INPUT, E.TIMEOUT_EXPIRED, S.AWAITING_HUMAN_REVIEW,
               "PatientSlaExpired, escalation_kind = PatientSlaExpired, escalated_from_state = AwaitingPatientInput",
               ("PatientSlaExpired",), escalation=_fixed(K.PATIENT_SLA_EXPIRED, S.AWAITING_PATIENT_INPUT)),
    Transition(S.READY, E.DELIVERY_PLANNED, S.PLANNING, "PlanIntact, CanAdvance, DeliveryStepPending",
               ("PlanIntact", "CanAdvance", "DeliveryStepPending"), effects=(Effect.ADVANCE_STEP,)),
    Transition(S.READY, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {DeliveryStepMissing, TemporalViolation}, "
               "escalated_from_state = Ready",
               _SYS, escalation=_signal(S.READY, K.DELIVERY_STEP_MISSING, K.TEMPORAL_VIOLATION)),
    Transition(S.RECEIVED, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {TemporalViolation}, escalated_from_state = Received",
               _SYS, escalation=_signal(S.RECEIVED, K.TEMPORAL_VIOLATION)),
    Transition(S.AWAITING_PATIENT_INPUT, E.HUMAN_REVIEW_REQUIRED, S.AWAITING_HUMAN_REVIEW,
               "SystemEscalationRequired, escalation_kind in {TemporalViolation}, "
               "escalated_from_state = AwaitingPatientInput",
               _SYS, escalation=_signal(S.AWAITING_PATIENT_INPUT, K.TEMPORAL_VIOLATION)),
    Transition(S.AWAITING_HUMAN_REVIEW, E.HUMAN_APPROVED, S.RECEIVED,
               "WorkflowDecisionValid, escalation_kind = PatientVerificationFailed, verified_identity_ref קיים; "
               "identity_verified נכתב true באותה טרנזקציה",
               ("WorkflowDecisionValid",), requires_escalation=frozenset({K.PATIENT_VERIFICATION_FAILED}),
               effects=(Effect.MARK_IDENTITY_VERIFIED, *_RESUME)),
    Transition(S.AWAITING_HUMAN_REVIEW, E.HUMAN_APPROVED, S.PLANNING,
               "WorkflowDecisionValid, escalation_kind = RetryExhausted; retry_cycle + 1, attempt_count = 0",
               ("WorkflowDecisionValid",), requires_escalation=frozenset({K.RETRY_EXHAUSTED}),
               effects=(Effect.OPEN_RETRY_CYCLE, *_RESUME)),
    # Design §12.1: the PolicyReview approval is NOT consumed here; it stays open as
    # PolicyReviewOverrideValid and is consumed by the next Policy decision (sub-project 2).
    Transition(S.AWAITING_HUMAN_REVIEW, E.HUMAN_APPROVED, S.PLANNING,
               "WorkflowDecisionValid, escalation_kind = PolicyReview; יוצר PolicyReviewOverrideValid כבול "
               "ל־plan_hash + current_step",
               ("WorkflowDecisionValid",), requires_escalation=frozenset({K.POLICY_REVIEW}),
               effects=(Effect.CLEAR_ESCALATION,)),
    Transition(S.AWAITING_HUMAN_REVIEW, E.HUMAN_APPROVED, S.AWAITING_PATIENT_INPUT,
               "WorkflowDecisionValid, escalation_kind in {Z3Counterexample, PatientSlaExpired}, patient_deadline "
               "קיים; נרשם דד־ליין חדש",
               ("WorkflowDecisionValid",),
               requires_escalation=frozenset({K.Z3_COUNTEREXAMPLE, K.PATIENT_SLA_EXPIRED}),
               effects=(Effect.APPROVED_PATIENT_DEADLINE, *_RESUME)),
    Transition(S.AWAITING_HUMAN_REVIEW, E.HUMAN_RESOLVED_CASE, S.COMPLETED, "WorkflowDecisionValid, קיימת סיבת סגירה",
               ("WorkflowDecisionValid", "closure_reason"), effects=(Effect.CONSUME_APPROVAL,)),
    Transition(S.AWAITING_HUMAN_REVIEW, E.HUMAN_REJECTED, S.FAILED, "WorkflowDecisionValid",
               ("WorkflowDecisionValid",), effects=(Effect.CONSUME_APPROVAL,)),
)

# Sub-project 15 (docs/spec_corrections.md rows 83-85; design §5.2): the owner's extension, kept out
# of TRANSITIONS so that table stays §3 row for row. No row here sets an escalation kind - the case
# goes back to review with the escalation it came with (a PatientSlaExpired kind would be resumable,
# and its HUMAN_APPROVED row leads back to the agent).
EXTENSION_TRANSITIONS: tuple[Transition, ...] = (
    Transition(S.AWAITING_HUMAN_REVIEW, E.PATIENT_REPLY_REQUESTED, S.AWAITING_PATIENT_REPLY,
               "WorkflowDecisionValid (decision = request), reply_request_valid",
               ("WorkflowDecisionValid", "reply_request_valid"),
               effects=(Effect.RECORD_REPLY_REQUEST, Effect.CONSUME_APPROVAL)),
    Transition(S.AWAITING_PATIENT_REPLY, E.PATIENT_REPLY_SUBMITTED, S.AWAITING_HUMAN_REVIEW,
               "patient_reply_valid", ("patient_reply_valid",), effects=(Effect.CLEAR_REPLY_REQUEST,)),
    Transition(S.AWAITING_PATIENT_REPLY, E.TIMEOUT_EXPIRED, S.AWAITING_HUMAN_REVIEW,
               "PatientSlaExpired", ("PatientSlaExpired",), effects=(Effect.CLEAR_REPLY_REQUEST,)),
)


class AmbiguousTransition(RuntimeError):
    """More than one row matched - a bug in the table. Fail closed."""


@dataclass(frozen=True)
class Resolution:
    transition: Transition | None
    reason: str | None  # None when a row matched
    guard_results: dict[str, bool]  # the matched row's guards, for audit_log.guards


def _evaluate(row: Transition, ctx: GuardContext) -> tuple[str | None, dict[str, bool]]:
    """Return (failure reason or None, guard results) for one candidate row."""
    results: dict[str, bool] = {}
    if row.requires_escalation is not None and (ctx.case is None or ctx.case.escalation_kind not in row.requires_escalation):
        return GUARD_FAILED, results
    row_ctx = replace(ctx, escalation_kinds=row.escalation.kinds if row.escalation else frozenset())
    for name in row.guards:
        negated = name.startswith("!")
        reason = GUARDS[name.removeprefix("!")](row_ctx)
        holds = (reason is not None) if negated else (reason is None)
        results[name] = holds
        if not holds:
            return (GUARD_FAILED if negated else reason), results
    return None, results


def resolve(state: S | None, event: E, ctx: GuardContext) -> Resolution:
    """Find the one row for (state, event) whose guards all hold (§3, note 8)."""
    candidates = [row for row in (*TRANSITIONS, *EXTENSION_TRANSITIONS) if row.source == state and row.event == event]
    matched: list[tuple[Transition, dict[str, bool]]] = []
    reasons: list[str] = []
    for row in candidates:
        reason, results = _evaluate(row, ctx)
        if reason is None:
            matched.append((row, results))
        else:
            reasons.append(reason)
    if len(matched) > 1:
        raise AmbiguousTransition(f"{len(matched)} rows matched ({state}, {event})")
    if matched:
        return Resolution(matched[0][0], None, matched[0][1])
    # Surface a specific reason (identity_not_established, ...) over the generic one.
    specific = next((r for r in reasons if r != GUARD_FAILED), GUARD_FAILED)
    return Resolution(None, specific, {})


def _higher_risk(current: SafetyLevel | None, new: SafetyLevel) -> SafetyLevel:
    order = list(SafetyLevel)  # LowRisk < MediumRisk < HighRisk < CriticalRisk
    return new if current is None or order.index(new) > order.index(current) else current


def apply_effects(case: CaseRecord, row: Transition, ctx: GuardContext) -> CaseRecord:
    """The cases row after `row` fires. Pure: the State Manager persists the result."""
    p = ctx.payload
    changes: dict[str, object] = {"state": row.target}
    if row.escalation is not None:
        kind = p["escalation_kind"] if row.escalation.from_payload else next(iter(row.escalation.kinds))
        changes["escalation_kind"] = K(kind)
        changes["escalated_from_state"] = row.escalation.from_state
    for effect in row.effects:
        match effect:
            case Effect.MARK_IDENTITY_VERIFIED:
                changes["identity_verified"] = True
            case Effect.RECORD_CLASSIFICATION:
                changes["intent"] = p.get("intent")
                if p.get("safety_level"):  # LLM design §5: re-classification never lowers the risk
                    changes["safety_level"] = _higher_risk(case.safety_level, SafetyLevel(p["safety_level"]))
            case Effect.RECORD_PLAN:
                steps = [dict(step) for step in p["ordered_steps"]]
                changes.update(ordered_steps=steps, plan_hash=compute_plan_hash(steps), current_step=1,
                               retry_cycle=0, attempt_count=0)
            case Effect.ADVANCE_STEP:
                changes.update(current_step=case.current_step + 1, retry_cycle=0, attempt_count=0)
            case Effect.RECORD_RETRIEVAL:
                if "appointment_at" in p:
                    changes["appointment_at"] = p["appointment_at"]
                if "required_documents" in p:
                    changes["required_documents"] = list(p["required_documents"])
                if "held_documents" in p:
                    changes["held_documents"] = list(p["held_documents"])
                # Sub-project 18 (D6): CheckAppointment's own facts about the appointment it
                # read, each stored only when the answer actually carried it (older services,
                # or the mock, omit some of these - absent means "leave unchanged", never None).
                if "appointment_id" in p:
                    changes["appointment_id"] = p["appointment_id"]
                if "department" in p:
                    changes["department"] = p["department"]
                if "exam_type_label" in p:
                    changes["exam_type_label"] = p["exam_type_label"]
                if "instruction_source_id" in p:
                    changes["instruction_source_id"] = p["instruction_source_id"]
                if "instruction_version" in p:
                    changes["instruction_version"] = p["instruction_version"]
                if "upcoming_count" in p:
                    changes["upcoming_count"] = p["upcoming_count"]
                if "safety_level" in p:  # LLM design §5: a re-check only ever raises the risk
                    changes["safety_level"] = _higher_risk(case.safety_level, SafetyLevel(p["safety_level"]))
            case Effect.RECORD_PATIENT_DEADLINE:
                changes["patient_deadline"] = p.get("patient_deadline")
            case Effect.HOLD_DOCUMENT:
                document_id = p["document"]["document_id"]
                if document_id not in case.held_documents:
                    changes["held_documents"] = [*case.held_documents, document_id]
            case Effect.OPEN_RETRY_CYCLE:
                changes.update(retry_cycle=case.retry_cycle + 1, attempt_count=0)
            case Effect.APPROVED_PATIENT_DEADLINE:
                changes["patient_deadline"] = ctx.approval.patient_deadline
            case Effect.CLEAR_ESCALATION:
                changes.update(escalation_kind=None, escalated_from_state=None)
            case Effect.RECORD_REPLY_REQUEST:
                changes.update(human_engaged=True, reply_kind=p["reply_kind"],
                               requested_document=p.get("requested_document"),
                               patient_deadline=ctx.approval.patient_deadline)
            case Effect.CLEAR_REPLY_REQUEST:
                changes.update(reply_kind=None, requested_document=None, patient_deadline=None)
            case Effect.CONSUME_APPROVAL | Effect.RECORD_EXECUTION_INTENT:
                pass  # written by the State Manager (approvals / executions), in the same transaction
    return replace(case, **changes)
