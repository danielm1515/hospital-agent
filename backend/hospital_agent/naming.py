"""Canonical names - spec §2 (States, Events), §5 (Actions), §13.2 (event owners), §17.

This module is the ONLY place these lists live. Every list is closed: a name that
is not here does not exist. tests/test_naming.py checks each list against docs/spec/.
"""
from __future__ import annotations

from enum import StrEnum


class State(StrEnum):
    RECEIVED = "Received"
    CLASSIFYING = "Classifying"
    CLASSIFIED = "Classified"
    PLANNING = "Planning"
    RETRIEVING_DATA = "RetrievingData"
    DELIVERING = "Delivering"
    ASSESSING_READINESS = "AssessingReadiness"
    AWAITING_PATIENT_INPUT = "AwaitingPatientInput"
    AWAITING_HUMAN_REVIEW = "AwaitingHumanReview"
    READY = "Ready"
    COMPLETED = "Completed"
    FAILED = "Failed"

    # Sub-project 15's extension (docs/spec_corrections.md row 83) - not a §2.1 state.
    AWAITING_PATIENT_REPLY = "AwaitingPatientReply"


TERMINAL_STATES = frozenset({State.COMPLETED, State.FAILED})


class Event(StrEnum):
    REQUEST_SUBMITTED = "REQUEST_SUBMITTED"
    REQUEST_VALIDATED = "REQUEST_VALIDATED"
    PATIENT_VERIFICATION_FAILED = "PATIENT_VERIFICATION_FAILED"
    INTENT_CLASSIFIED = "INTENT_CLASSIFIED"
    MEDICAL_QUESTION_DETECTED = "MEDICAL_QUESTION_DETECTED"
    PLAN_CREATED = "PLAN_CREATED"
    ACTION_PROPOSED = "ACTION_PROPOSED"
    POLICY_ALLOWED = "POLICY_ALLOWED"
    POLICY_DENIED = "POLICY_DENIED"
    DATA_RETRIEVED = "DATA_RETRIEVED"
    TOOL_TRANSIENT_FAILURE = "TOOL_TRANSIENT_FAILURE"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    MISSING_INFORMATION_DETECTED = "MISSING_INFORMATION_DETECTED"
    DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
    TIMEOUT_EXPIRED = "TIMEOUT_EXPIRED"
    STEP_ADVANCED = "STEP_ADVANCED"
    POLICY_HUMAN_REVIEW_REQUIRED = "POLICY_HUMAN_REVIEW_REQUIRED"
    DELIVERY_PLANNED = "DELIVERY_PLANNED"
    READINESS_PASSED = "READINESS_PASSED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_REJECTED = "HUMAN_REJECTED"
    HUMAN_RESOLVED_CASE = "HUMAN_RESOLVED_CASE"
    CASE_RESOLVED = "CASE_RESOLVED"
    TOOL_EXECUTION_STARTED = "TOOL_EXECUTION_STARTED"
    AUDIT_RECORDED = "AUDIT_RECORDED"

    # Sub-project 15's extension (row 83) - not §2.2 events. Both are external (§13.2 has no
    # owner for them): a staff member's request, and the patient's reply through the Session Service.
    PATIENT_REPLY_REQUESTED = "PATIENT_REPLY_REQUESTED"
    PATIENT_REPLY_SUBMITTED = "PATIENT_REPLY_SUBMITTED"


class Component(StrEnum):
    """Who emitted an event. EXTERNAL covers the patient and the human reviewers."""

    EXTERNAL = "External"
    SESSION_SERVICE = "SessionService"
    CLASSIFIER_SERVICE = "ClassifierService"
    PLANNER_SERVICE = "PlannerService"
    POLICY_SERVICE = "PolicyService"
    READINESS_CHECK = "ReadinessCheck"
    TOOL_EXECUTOR = "ToolExecutor"
    AGENT_ORCHESTRATOR = "AgentOrchestrator"
    ESCALATION_COORDINATOR = "EscalationCoordinator"
    TEMPORAL_MONITOR = "TemporalMonitor"
    SLA_WORKER = "SlaWorker"
    RESPONSE_DELIVERY = "ResponseDelivery"
    STATE_MANAGER = "StateManager"


# §13.2: the 21 system-owned events and the one component allowed to emit each.
# Injecting one of these from any other source is Blocked: system_owned_event.
EVENT_OWNER: dict[Event, Component] = {
    Event.REQUEST_VALIDATED: Component.SESSION_SERVICE,
    Event.PATIENT_VERIFICATION_FAILED: Component.SESSION_SERVICE,
    Event.INTENT_CLASSIFIED: Component.CLASSIFIER_SERVICE,
    Event.MEDICAL_QUESTION_DETECTED: Component.CLASSIFIER_SERVICE,
    Event.PLAN_CREATED: Component.PLANNER_SERVICE,
    Event.ACTION_PROPOSED: Component.PLANNER_SERVICE,
    Event.POLICY_ALLOWED: Component.POLICY_SERVICE,
    Event.POLICY_DENIED: Component.POLICY_SERVICE,
    Event.POLICY_HUMAN_REVIEW_REQUIRED: Component.POLICY_SERVICE,
    Event.TOOL_EXECUTION_STARTED: Component.TOOL_EXECUTOR,
    Event.DATA_RETRIEVED: Component.TOOL_EXECUTOR,
    Event.TOOL_TRANSIENT_FAILURE: Component.TOOL_EXECUTOR,
    Event.RETRY_EXHAUSTED: Component.TOOL_EXECUTOR,
    Event.READINESS_PASSED: Component.READINESS_CHECK,
    Event.MISSING_INFORMATION_DETECTED: Component.READINESS_CHECK,
    Event.STEP_ADVANCED: Component.AGENT_ORCHESTRATOR,
    Event.DELIVERY_PLANNED: Component.AGENT_ORCHESTRATOR,
    Event.HUMAN_REVIEW_REQUIRED: Component.ESCALATION_COORDINATOR,
    Event.TIMEOUT_EXPIRED: Component.SLA_WORKER,
    Event.CASE_RESOLVED: Component.RESPONSE_DELIVERY,
    Event.AUDIT_RECORDED: Component.STATE_MANAGER,
}

# The three outcomes of a Policy decision (§2.2 events 8, 9, 17).
POLICY_DECISION_EVENTS = frozenset({
    Event.POLICY_ALLOWED,
    Event.POLICY_DENIED,
    Event.POLICY_HUMAN_REVIEW_REQUIRED,
})

# §13.2: the patient's two events and the three human decisions.
EXTERNAL_EVENTS = frozenset(set(Event) - set(EVENT_OWNER))

# Sub-project 15 (docs/spec_corrections.md row 83): the owner's extension of §2. The members join
# the same enums, so every component handles them like any other, and are listed here so the
# spec's own lists (tests/test_naming.py) are compared without them.
EXTENSION_STATES = frozenset({State.AWAITING_PATIENT_REPLY})
EXTENSION_EVENTS = frozenset({Event.PATIENT_REPLY_REQUESTED, Event.PATIENT_REPLY_SUBMITTED})

# §2.2: recorded in the trace, never a trigger for a State change.
NON_TRANSITION_EVENTS = frozenset({Event.TOOL_EXECUTION_STARTED, Event.AUDIT_RECORDED})

# §3.1 SystemEscalationRequired: internal components allowed to raise an escalation signal.
ESCALATION_SIGNAL_SOURCES = frozenset({
    Component.CLASSIFIER_SERVICE,
    Component.PLANNER_SERVICE,
    Component.READINESS_CHECK,
    Component.TOOL_EXECUTOR,
    Component.AGENT_ORCHESTRATOR,
    Component.TEMPORAL_MONITOR,
})


class Action(StrEnum):
    CHECK_APPOINTMENT = "CheckAppointment"
    CHECK_DOCUMENTS = "CheckDocuments"
    LOAD_INSTRUCTIONS = "LoadInstructions"
    SEND_STATUS_UPDATE = "SendStatusUpdate"
    CLOSE_MEDICAL_CASE = "CloseMedicalCase"
    ANSWER_CLINICAL_QUESTION = "AnswerClinicalQuestion"


RETRIEVAL_ACTIONS = frozenset({Action.CHECK_APPOINTMENT, Action.CHECK_DOCUMENTS, Action.LOAD_INSTRUCTIONS})
# §5: the only actions the Planner may propose. The other two are human-only.
AUTOMATIC_ACTIONS = RETRIEVAL_ACTIONS | {Action.SEND_STATUS_UPDATE}

# §5: the one mapping between the OPA/JSON/State names and the Prolog/Datalog names.
_PROLOG_NAMES: dict[Action, str] = {
    Action.CHECK_APPOINTMENT: "check_appointment",
    Action.CHECK_DOCUMENTS: "check_documents",
    Action.LOAD_INSTRUCTIONS: "load_instructions",
    Action.SEND_STATUS_UPDATE: "send_status_update",
    Action.CLOSE_MEDICAL_CASE: "close_medical_case",
    Action.ANSWER_CLINICAL_QUESTION: "answer_clinical_q",
}
_PASCAL_NAMES = {prolog: action for action, prolog in _PROLOG_NAMES.items()}


def to_prolog(action: Action | str) -> str:
    return _PROLOG_NAMES[Action(action)]


def to_pascal(prolog_name: str) -> Action:
    try:
        return _PASCAL_NAMES[prolog_name]
    except KeyError:
        raise UnknownName(f"{prolog_name!r} is not a Prolog action name (spec §5)") from None


class EscalationKind(StrEnum):
    PATIENT_VERIFICATION_FAILED = "PatientVerificationFailed"
    MEDICAL_QUESTION = "MedicalQuestion"
    SAFETY_ESCALATION = "SafetyEscalation"
    CLASSIFICATION_FAILED = "ClassificationFailed"
    TEMPORAL_VIOLATION = "TemporalViolation"
    PLANNING_FAILED = "PlanningFailed"
    POLICY_DENIED = "PolicyDenied"
    POLICY_REVIEW = "PolicyReview"
    RETRY_EXHAUSTED = "RetryExhausted"
    NON_IDEMPOTENT_FAILURE = "NonIdempotentFailure"
    EXECUTION_UNKNOWN = "ExecutionUnknown"
    Z3_COUNTEREXAMPLE = "Z3Counterexample"
    PATIENT_SLA_EXPIRED = "PatientSlaExpired"
    DELIVERY_STEP_MISSING = "DeliveryStepMissing"


# §3 HUMAN_APPROVED rows + §12.4: the escalations a human may resume, and the
# approval fields each one requires. Every other escalation is resolve/reject only.
RESUMABLE: dict[EscalationKind, tuple[str, ...]] = {
    EscalationKind.PATIENT_VERIFICATION_FAILED: ("verified_identity_ref",),
    EscalationKind.RETRY_EXHAUSTED: (),
    EscalationKind.POLICY_REVIEW: ("plan_hash", "current_step"),
    EscalationKind.Z3_COUNTEREXAMPLE: ("patient_deadline",),
    EscalationKind.PATIENT_SLA_EXPIRED: ("patient_deadline",),
}


class SafetyLevel(StrEnum):
    LOW_RISK = "LowRisk"
    MEDIUM_RISK = "MediumRisk"
    HIGH_RISK = "HighRisk"
    CRITICAL_RISK = "CriticalRisk"


class UnknownName(ValueError):
    """A name that is not in one of the closed lists."""


class DeprecatedEventName(UnknownName):
    """A spelling from an earlier draft of the spec. Kept so stale input fails loudly."""


# Spellings from earlier spec drafts (carried over from the AI_Hospital project).
DEPRECATED_EVENT_ALIASES: dict[str, Event] = {
    "SUBMITTED_REQUEST": Event.REQUEST_SUBMITTED,
    "VALIDATED_REQUEST": Event.REQUEST_VALIDATED,
    "CLASSIFIED_INTENT": Event.INTENT_CLASSIFIED,
    "CREATED_PLAN": Event.PLAN_CREATED,
    "PROPOSED_ACTION": Event.ACTION_PROPOSED,
    "ALLOWED_POLICY": Event.POLICY_ALLOWED,
    "DENIED_POLICY": Event.POLICY_DENIED,
    "RETRIEVED_DATA": Event.DATA_RETRIEVED,
    "EXHAUSTED_RETRY": Event.RETRY_EXHAUSTED,
    "UPLOADED_DOCUMENT": Event.DOCUMENT_UPLOADED,
    "PASSED_READINESS": Event.READINESS_PASSED,
    "APPROVED_HUMAN": Event.HUMAN_APPROVED,
    "REJECTED_HUMAN": Event.HUMAN_REJECTED,
    "RESOLVED_CASE": Event.CASE_RESOLVED,
    "RECORDED_AUDIT": Event.AUDIT_RECORDED,
}


def canonical_event(name: Event | str) -> Event:
    """Resolve a string to a canonical Event; refuse deprecated and unknown spellings."""
    if isinstance(name, Event):
        return name
    if name in {e.value for e in Event}:
        return Event(name)
    if name in DEPRECATED_EVENT_ALIASES:
        raise DeprecatedEventName(
            f"{name!r} is a deprecated spelling; use {DEPRECATED_EVENT_ALIASES[name].value!r} (spec §2.2)"
        )
    raise UnknownName(f"{name!r} is not one of the 26 canonical events (spec §2.2)")
