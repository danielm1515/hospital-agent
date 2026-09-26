"""Unit tests for the §3.1 guards. No database: cases and approvals are built in memory."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent.case import ApprovalRecord, CaseRecord, ExecutionRecord, compute_plan_hash, new_case
from hospital_agent.guards import GUARDS, GuardContext
from hospital_agent.naming import Component, EscalationKind, Event, State
from tests.fakes import fake_ports
from tests.spec_tables import read_table

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
PLAN = [
    {"step": 1, "action": "CheckAppointment"},
    {"step": 2, "action": "CheckDocuments"},
    {"step": 3, "action": "LoadInstructions"},
    {"step": 4, "action": "SendStatusUpdate"},
]


def planned(step: int = 1, **changes) -> CaseRecord:
    case = replace(
        new_case("CASE-1", "P-1", NOW),
        state=State.PLANNING,
        identity_verified=True,
        ordered_steps=PLAN,
        plan_hash=compute_plan_hash(PLAN),
        current_step=step,
    )
    return replace(case, **changes)


def ctx(case=None, event=Event.ACTION_PROPOSED, payload=None, **kw) -> GuardContext:
    return GuardContext(case=case, event=event, payload=payload or {}, now=NOW, ports=fake_ports(), **kw)


def holds(name: str, context: GuardContext) -> bool:
    return GUARDS[name](context) is None


def test_every_pascal_case_guard_is_a_spec_3_1_guard():
    spec_guards = {row[0] for row in read_table("03-transitions-guards.md", ("Guard", "מה נבדק", "מי מעריך", "אם אינו מתקיים"))}
    ours = {name for name in GUARDS if name[0].isupper()}
    assert ours <= spec_guards, ours - spec_guards


def test_patient_identified_and_request_valid():
    assert holds("PatientIdentified", ctx(payload={"patient_id": "P-1"}))
    assert not holds("PatientIdentified", ctx(payload={"patient_id": "  "}))
    assert holds("RequestValid", ctx(payload={"text": "when is my appointment?"}))
    assert holds("RequestValid", ctx(payload={"text": "x", "document": {"format": "pdf"}}))
    assert not holds("RequestValid", ctx(payload={"text": ""}))
    assert not holds("RequestValid", ctx(payload={"text": "x", "document": {"format": "exe"}}))


def test_identity_verified_is_written_only_at_request_validated():
    fresh = new_case("CASE-1", "P-1", NOW)
    assert holds("IdentityVerified", ctx(fresh, Event.REQUEST_VALIDATED, {"identity_verified": True}))
    assert not holds("IdentityVerified", ctx(fresh, Event.ACTION_PROPOSED, {"identity_verified": True}))
    assert holds("IdentityVerified", ctx(planned(), Event.ACTION_PROPOSED))


def test_plan_complete_accepts_only_numbered_automatic_steps():
    assert holds("PlanComplete", ctx(payload={"plan_complete": True, "ordered_steps": PLAN}))
    assert not holds("PlanComplete", ctx(payload={"plan_complete": False, "ordered_steps": PLAN}))
    human_step = [*PLAN[:3], {"step": 4, "action": "AnswerClinicalQuestion"}]
    assert not holds("PlanComplete", ctx(payload={"plan_complete": True, "ordered_steps": human_step}))
    extra_key = [{"step": 1, "action": "CheckAppointment", "approved": True}]
    assert not holds("PlanComplete", ctx(payload={"plan_complete": True, "ordered_steps": extra_key}))


def test_in_plan_and_plan_intact():
    case = planned(step=2)
    assert holds("InPlan", ctx(case, payload={"proposed_action": {"action": "CheckDocuments", "from_step": 2}}))
    assert not holds("InPlan", ctx(case, payload={"proposed_action": {"action": "CheckDocuments", "from_step": 3}}))
    assert not holds("InPlan", ctx(case, payload={"proposed_action": {"action": "LoadInstructions", "from_step": 2}}))
    assert holds("PlanIntact", ctx(case))
    tampered = replace(case, ordered_steps=[*PLAN[:3], {"step": 4, "action": "CheckAppointment"}])
    assert not holds("PlanIntact", ctx(tampered))


def test_step_position_guards():
    assert holds("CanAdvance", ctx(planned(step=3)))
    assert not holds("CanAdvance", ctx(planned(step=4)))
    assert holds("RetrievalStepsRemain", ctx(planned(step=1)))
    assert holds("RetrievalStepsRemain", ctx(planned(step=2)))
    assert not holds("RetrievalStepsRemain", ctx(planned(step=3)))
    assert holds("PreReadinessPhaseComplete", ctx(planned(step=3)))
    assert not holds("PreReadinessPhaseComplete", ctx(planned(step=2)))


def test_attempts_available_and_is_idempotent():
    assert holds("AttemptsAvailable", ctx(planned(attempt_count=2)))
    assert not holds("AttemptsAvailable", ctx(planned(attempt_count=3)))
    assert holds("IsIdempotent", ctx(payload={"idempotency_key": "k-1", "idempotent": True}))
    assert not holds("IsIdempotent", ctx(payload={"idempotency_key": "k-1", "idempotent": False}))
    assert not holds("IsIdempotent", ctx(payload={"idempotent": True}))


def test_readiness_is_computed_from_stored_documents():
    missing = planned(step=3, required_documents=["referral", "blood_test"], held_documents=["referral"])
    complete = replace(missing, held_documents=["referral", "blood_test"])
    assert not holds("ReadinessComplete", ctx(missing, payload={"readiness_complete": True}))
    assert holds("ReadinessComplete", ctx(complete))
    assert holds("ReadinessInProgress", ctx(missing))
    assert not holds("ReadinessInProgress", ctx(planned(step=3)))
    assert holds("DeliveryStepPending", ctx(complete))
    assert not holds("DeliveryStepPending", ctx(missing))


def test_delivery_confirmed_reads_the_execution_row():
    case = planned(step=4)
    ok = ExecutionRecord("EXEC-1", "CASE-1", "P-1", "SendStatusUpdate", 4, 0, 1, "k-1", "succeeded")
    assert holds("DeliveryConfirmed", ctx(case, execution=ok))
    assert not holds("DeliveryConfirmed", ctx(case, execution=replace(ok, status="failed")))
    assert not holds("DeliveryConfirmed", ctx(case, execution=replace(ok, action="LoadInstructions")))
    assert not holds("DeliveryConfirmed", ctx(case))


def test_document_valid_checks_format_owner_and_expiry():
    case = planned(state=State.AWAITING_PATIENT_INPUT)
    doc = {"document_id": "blood_test", "format": "pdf", "patient_id": "P-1"}
    source = Component.SESSION_SERVICE
    assert holds("DocumentValid", ctx(case, payload={"document": doc}, source=source))
    assert not holds("DocumentValid", ctx(case, payload={"document": {**doc, "patient_id": "P-2"}}, source=source))
    assert not holds("DocumentValid", ctx(case, payload={"document": {**doc, "format": "docm"}}, source=source))
    expired = {**doc, "expires_at": NOW - timedelta(days=1)}
    assert not holds("DocumentValid", ctx(case, payload={"document": expired}, source=source))


@pytest.mark.parametrize("fmt", ["pdf", "jpg", "png"])
def test_document_valid_accepts_all_three_supported_formats(fmt):
    """Sub-project 17 task 2 decision 4 (fix round 1, M3): the sniffed kind of an image upload
    (`jpg`/`png`) is a real `format` value now, not just a theoretical one - all three must
    hold, exactly like the pre-existing `pdf` case above."""
    case = planned(state=State.AWAITING_PATIENT_INPUT)
    doc = {"document_id": "blood_test", "format": fmt, "patient_id": "P-1"}
    assert holds("DocumentValid", ctx(case, payload={"document": doc}, source=Component.SESSION_SERVICE))


def test_document_valid_trusts_the_document_only_from_the_session_service():
    """F2: DOCUMENT_UPLOADED is external, so State Manager cannot trust an owner check (§13.2).

    DocumentValid must itself require source is SessionService (§3.1 "Session Service
    קובע · State Manager מאמת"); a patient-supplied document is never trusted directly.
    """
    case = planned(state=State.AWAITING_PATIENT_INPUT)
    doc = {"document_id": "blood_test", "format": "pdf", "patient_id": "P-1"}
    assert not holds("DocumentValid", ctx(case, payload={"document": doc}))  # default source is External
    assert not holds("DocumentValid", ctx(case, payload={"document": doc}, source=Component.EXTERNAL))
    assert holds("DocumentValid", ctx(case, payload={"document": doc}, source=Component.SESSION_SERVICE))


def test_document_valid_accepts_an_aware_iso_string_expiry():
    case = planned(state=State.AWAITING_PATIENT_INPUT)
    doc = {
        "document_id": "blood_test", "format": "pdf", "patient_id": "P-1",
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
    }
    assert holds("DocumentValid", ctx(case, payload={"document": doc}, source=Component.SESSION_SERVICE))


@pytest.mark.parametrize(
    "bad_expiry",
    ["not-a-real-date", (NOW + timedelta(days=1)).replace(tzinfo=None), 12345],
)
def test_document_valid_rejects_unparsable_or_naive_expiry_without_raising(bad_expiry):
    """F3(a): a malformed expires_at fails the guard - it must never raise TypeError."""
    case = planned(state=State.AWAITING_PATIENT_INPUT)
    doc = {"document_id": "blood_test", "format": "pdf", "patient_id": "P-1", "expires_at": bad_expiry}
    assert not holds("DocumentValid", ctx(case, payload={"document": doc}, source=Component.SESSION_SERVICE))


def test_valid_classification_rejects_an_unknown_safety_level():
    """F3(b): an unrecognised safety_level is malformed input (§14 invalid_safety_level)."""
    assert GUARDS["valid_classification"](ctx(payload={"safety_level": "LowRisk"})) is None
    assert GUARDS["valid_classification"](ctx(payload={"safety_level": "SuperRisk"})) == "invalid_safety_level"
    assert GUARDS["valid_classification"](ctx(payload={})) == "invalid_safety_level"


def test_valid_tool_result_requires_lists_of_non_empty_strings():
    """F3(c): a malformed tool result must not become a list of characters."""
    assert GUARDS["valid_tool_result"](ctx(payload={})) is None
    assert GUARDS["valid_tool_result"](ctx(payload={"required_documents": ["referral"], "held_documents": []})) is None
    assert GUARDS["valid_tool_result"](ctx(payload={"required_documents": "referral"})) == "invalid_tool_result"
    assert GUARDS["valid_tool_result"](ctx(payload={"held_documents": ["referral", ""]})) == "invalid_tool_result"


@pytest.mark.parametrize("field, cap", [("answered_appointment_id", 64), ("instruction_source_id", 64),
                                        ("instruction_version", 64), ("department", 200), ("exam_type_label", 200)])
def test_valid_tool_result_caps_check_appointments_new_string_fields(field, cap):
    """Sub-project 18 fix round 1 (M3): defence in depth, mirroring map_response's own caps."""
    assert GUARDS["valid_tool_result"](ctx(payload={field: "x" * cap})) is None
    assert GUARDS["valid_tool_result"](ctx(payload={field: "x" * (cap + 1)})) == "invalid_tool_result"
    assert GUARDS["valid_tool_result"](ctx(payload={field: ""})) == "invalid_tool_result"
    assert GUARDS["valid_tool_result"](ctx(payload={field: "   "})) == "invalid_tool_result"
    assert GUARDS["valid_tool_result"](ctx(payload={field: 123})) == "invalid_tool_result"


def test_valid_tool_result_bounds_upcoming_count():
    """Sub-project 18 fix round 1 (M1/M3): non-negative, bounded, never a bool."""
    assert GUARDS["valid_tool_result"](ctx(payload={"upcoming_count": 0})) is None
    assert GUARDS["valid_tool_result"](ctx(payload={"upcoming_count": 2**31 - 1})) is None
    assert GUARDS["valid_tool_result"](ctx(payload={"upcoming_count": -1})) == "invalid_tool_result"
    assert GUARDS["valid_tool_result"](ctx(payload={"upcoming_count": 2**31})) == "invalid_tool_result"
    assert GUARDS["valid_tool_result"](ctx(payload={"upcoming_count": True})) == "invalid_tool_result"
    assert GUARDS["valid_tool_result"](ctx(payload={"upcoming_count": "3"})) == "invalid_tool_result"


def test_deadline_registered_requires_a_future_aware_datetime():
    """F3(d): MISSING_INFORMATION_DETECTED must carry a deadline PatientSlaExpired can later check."""
    assert GUARDS["deadline_registered"](ctx(payload={"patient_deadline": NOW + timedelta(hours=1)})) is None
    assert GUARDS["deadline_registered"](ctx(payload={"patient_deadline": NOW - timedelta(hours=1)})) == "patient_deadline_missing"
    assert GUARDS["deadline_registered"](ctx(payload={})) == "patient_deadline_missing"
    naive = (NOW + timedelta(hours=1)).replace(tzinfo=None)
    assert GUARDS["deadline_registered"](ctx(payload={"patient_deadline": naive})) == "patient_deadline_missing"


def test_ask_patient_safe_only_on_unsat():
    assert holds("AskPatientSafe", ctx(payload={"z3_result": "unsat"}))
    for result in ("sat", "unknown", None):
        assert not holds("AskPatientSafe", ctx(payload={"z3_result": result}))


def test_patient_sla_expired_requires_the_registered_version():
    case = planned(state=State.AWAITING_PATIENT_INPUT, state_version=7, patient_deadline=NOW - timedelta(minutes=1))
    assert holds("PatientSlaExpired", ctx(case, payload={"registered_state_version": 7}))
    assert not holds("PatientSlaExpired", ctx(case, payload={"registered_state_version": 6}))
    not_yet = replace(case, patient_deadline=NOW + timedelta(hours=1))
    assert not holds("PatientSlaExpired", ctx(not_yet, payload={"registered_state_version": 7}))


def test_system_escalation_required_checks_kind_and_origin():
    case = planned(state=State.CLASSIFYING)
    allow = frozenset({EscalationKind.SAFETY_ESCALATION})
    good = {"escalation_kind": "SafetyEscalation", "escalated_from_state": "Classifying"}
    assert holds("SystemEscalationRequired", ctx(case, payload=good, escalation_kinds=allow))
    wrong_kind = {**good, "escalation_kind": "DeliveryStepMissing"}
    assert GUARDS["SystemEscalationRequired"](ctx(case, payload=wrong_kind, escalation_kinds=allow)) == "invalid_escalation_reason"
    wrong_origin = {**good, "escalated_from_state": "Planning"}
    assert not holds("SystemEscalationRequired", ctx(case, payload=wrong_origin, escalation_kinds=allow))


def test_executor_reverified_is_delegated_to_the_port():
    assert holds("ExecutorReverified", ctx(planned()))
    assert not holds("ExecutorReverified", GuardContext(planned(), Event.POLICY_ALLOWED, {}, NOW, fake_ports(False)))


# --- WorkflowDecisionValid ----------------------------------------------------------


def escalated(kind: EscalationKind) -> CaseRecord:
    return planned(state=State.AWAITING_HUMAN_REVIEW, escalation_kind=kind, escalated_from_state=State.PLANNING)


def approval(**changes) -> ApprovalRecord:
    base = ApprovalRecord(
        approval_id="APPR-1",
        approval_type="WorkflowDecision",
        case_id="CASE-1",
        patient_id="P-1",
        reviewer_id="nurse-1",
        reviewer_role="clinical_staff",
        decision="approve",
        reason="document system fixed",
        shown_context_ref="ctx-1",
        granted_at=NOW - timedelta(minutes=5),
        valid_until=NOW + timedelta(hours=1),
        escalation_kind="RetryExhausted",
    )
    return replace(base, **changes)


def wdv(case, event, appr) -> str | None:
    return GUARDS["WorkflowDecisionValid"](ctx(case, event, approval=appr))


def test_workflow_decision_valid_happy_path():
    assert wdv(escalated(EscalationKind.RETRY_EXHAUSTED), Event.HUMAN_APPROVED, approval()) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"approval_type": "ContentApproval"},
        {"case_id": "CASE-2"},
        {"patient_id": "P-2"},
        {"reviewer_role": "patient"},
        {"reason": " "},
        {"valid_until": NOW - timedelta(seconds=1)},
        {"granted_at": NOW + timedelta(minutes=1)},
        {"consumed_at": NOW - timedelta(minutes=1)},
        {"escalation_kind": "PolicyDenied"},
    ],
)
def test_workflow_decision_invalid(changes):
    assert wdv(escalated(EscalationKind.RETRY_EXHAUSTED), Event.HUMAN_APPROVED, approval(**changes)) == "workflow_decision_invalid"


def test_workflow_decision_missing_approval():
    assert wdv(escalated(EscalationKind.RETRY_EXHAUSTED), Event.HUMAN_APPROVED, None) == "workflow_decision_invalid"


def test_decision_must_match_the_event():
    case = escalated(EscalationKind.RETRY_EXHAUSTED)
    assert wdv(case, Event.HUMAN_APPROVED, approval(decision="reject")) == "approval_decision_mismatch"
    assert wdv(case, Event.HUMAN_REJECTED, approval(decision="reject")) is None
    assert wdv(case, Event.HUMAN_RESOLVED_CASE, approval(decision="resolve")) is None


def test_resume_requires_the_field_of_its_escalation_kind():
    pvf = escalated(EscalationKind.PATIENT_VERIFICATION_FAILED)
    appr = approval(escalation_kind="PatientVerificationFailed")
    assert wdv(pvf, Event.HUMAN_APPROVED, appr) == "identity_not_established"
    assert wdv(pvf, Event.HUMAN_APPROVED, replace(appr, verified_identity_ref="ID-CHECK-9")) is None

    z3 = escalated(EscalationKind.Z3_COUNTEREXAMPLE)
    appr = approval(escalation_kind="Z3Counterexample")
    assert wdv(z3, Event.HUMAN_APPROVED, appr) == "patient_deadline_missing"
    assert wdv(z3, Event.HUMAN_APPROVED, replace(appr, patient_deadline=NOW + timedelta(days=2))) is None

    review = escalated(EscalationKind.POLICY_REVIEW)
    appr = approval(escalation_kind="PolicyReview", plan_hash=review.plan_hash, current_step=review.current_step)
    assert wdv(review, Event.HUMAN_APPROVED, appr) is None
    assert wdv(review, Event.HUMAN_APPROVED, replace(appr, current_step=3)) == "workflow_decision_invalid"


def test_workflow_decision_rejects_an_approval_already_used_on_a_committed_transition():
    case = escalated(EscalationKind.RETRY_EXHAUSTED)
    result = GUARDS["WorkflowDecisionValid"](
        ctx(case, Event.HUMAN_APPROVED, approval=approval(), approval_already_used=True)
    )
    assert result == "workflow_decision_invalid"


def test_reject_and_resolve_need_no_resume_field():
    pvf = escalated(EscalationKind.PATIENT_VERIFICATION_FAILED)
    appr = approval(escalation_kind="PatientVerificationFailed", decision="reject")
    assert wdv(pvf, Event.HUMAN_REJECTED, appr) is None
