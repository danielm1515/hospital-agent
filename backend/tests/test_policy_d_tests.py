"""The §16 D-tests that the Policy layer proves (Policy design §9). D29 is in tests/test_prolog.py."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent.case import ApprovalRecord
from hospital_agent.fsm import TRANSITIONS
from hospital_agent.naming import Event, EscalationKind, State
from hospital_agent.policy.service import InstructionSource, OutgoingMessage, PolicyService
from tests.driver import Driver

MEDICAL = OutgoingMessage(evaluated=True, medical_content_flag=True, content_hash="HASH-MED-1")


def at_step(d: Driver, step: int) -> None:
    """Drive a fresh case to Planning at `step` with the step proposed (docs held, so readiness passes)."""
    d.to_classified()
    d.plan()
    for _ in range(step - 1):
        if d.case.current_step == 3:
            break
        d.retrieve_step(required_documents=["referral"], held_documents=["referral"])
        d.advance()
    if step == 4:
        d.retrieve_step()                 # LoadInstructions -> AssessingReadiness
        d.assess(96)                      # everything held -> Ready
        d.plan_delivery()
    d.propose()
    assert (d.state, d.case.current_step) == (State.PLANNING, step)


def reasons(d: Driver) -> list[str]:
    return d.trace()[-1].policy_reasons


def content_approval(d: Driver, execution_id: str, **changes) -> ApprovalRecord:
    now = datetime.now(UTC)
    base = ApprovalRecord(
        approval_id="APPR-C1", approval_type="ContentApproval", case_id=d.case_id, patient_id=d.patient_id,
        reviewer_id="coordinator_nurse", reviewer_role="clinical_staff", decision="approve",
        reason="message checked", shown_context_ref="ctx-C1", granted_at=now - timedelta(minutes=1),
        valid_until=now + timedelta(hours=1), execution_id=execution_id, action="SendStatusUpdate",
        content_hash=MEDICAL.content_hash)
    return replace(base, **changes)


def test_d3_proposal_outside_the_plan_is_denied(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 2)
    request = d.request()
    result = d.allow(proposed_action=replace(request.proposed_action, from_step=3))
    assert (result.state_after, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.POLICY_DENIED)
    assert "action_not_in_plan" in reasons(d)


def test_d4_retrieval_before_identity_verification(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 2)
    decision = PolicyService(app_engine).decide(replace(d.case, identity_verified=False), d.request())
    assert decision.result == "Deny" and "identity_not_verified" in decision.reasons


def test_d5_missing_document_with_96_hours_asks_the_patient(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"])
    before = datetime.now(UTC)
    assert d.assess(96).state_after is State.AWAITING_PATIENT_INPUT
    assert timedelta(hours=23) < d.case.patient_deadline - before < timedelta(hours=25)


def test_d6_missing_document_with_20_hours_goes_to_a_human(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"])
    assert d.assess(20).state_after is State.AWAITING_HUMAN_REVIEW
    assert d.case.escalation_kind is EscalationKind.Z3_COUNTEREXAMPLE
    audited = reasons(d)
    assert audited[0] == "z3:sat" and audited[1].startswith("z3_detail:") and "upload_h" in audited[1]


def test_d8_medical_output_without_content_approval(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    assert d.allow(outgoing_message=MEDICAL).state_after is State.AWAITING_HUMAN_REVIEW
    assert "medical_answer_attempt" in reasons(d)


def test_d10_empty_patient_id(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 2)
    decision = PolicyService(app_engine).decide(replace(d.case, patient_id=""), d.request())
    assert decision.result == "Deny" and "missing_patient_context" in decision.reasons


def test_d11_medical_output_with_a_matching_content_approval(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    request = d.request(outgoing_message=MEDICAL)
    request = replace(request, approval=content_approval(d, request.execution_id))
    assert d.policy.apply(sm, d.case_id, request).state_after is State.DELIVERING
    row = d.trace()[-1]
    assert row.guards["ContentApprovalValid"] is True and row.content_hash == MEDICAL.content_hash


@pytest.mark.parametrize("change, extra", [
    ({"approval_type": "WorkflowDecision"}, "approval_is_workflow_only"),      # D13
    ({"valid_until": datetime(2020, 1, 1, tzinfo=UTC)}, None),                 # D13 expired
    ({"consumed_at": datetime(2026, 1, 1, tzinfo=UTC)}, None),                 # D13 used
    ({"content_hash": "HASH-OTHER"}, None),                                    # D13 another message
    ({"reviewer_role": "admin_staff"}, None),                                  # D22 role
])
def test_d13_d22_an_approval_not_bound_to_this_message_is_rejected(sm, app_engine, change, extra):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    request = d.request(outgoing_message=MEDICAL)
    request = replace(request, approval=content_approval(d, request.execution_id, **change))
    assert d.policy.apply(sm, d.case_id, request).state_after is State.AWAITING_HUMAN_REVIEW
    assert "medical_answer_attempt" in reasons(d)
    if extra:
        assert extra in reasons(d)


def test_d22_an_approval_for_another_case(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 4)
    request = d.request(outgoing_message=MEDICAL)
    request = replace(request, approval=content_approval(d, request.execution_id, case_id="CASE-OTHER"))
    d.policy.apply(sm, d.case_id, request)
    assert "medical_answer_attempt" in reasons(d)


def test_d16_document_id_to_the_appointment_system(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 1)
    request = d.request()
    d.allow(proposed_action=replace(request.proposed_action, patient_fields=("patient_id", "document_id")))
    assert d.state is State.AWAITING_HUMAN_REVIEW and "field_not_minimized" in reasons(d)


@pytest.mark.parametrize("source, expected", [
    (InstructionSource("INSTR-UNKNOWN", "3"), {"unapproved_instruction_source"}),
    (InstructionSource("INSTR-PREP-COLONOSCOPY", "2"), {"unapproved_instruction_source"}),
    (InstructionSource("INSTR-RETIRED-2025", "1"), {"unapproved_instruction_source", "instruction_source_expired"}),
])
def test_d17_instructions_only_from_the_approved_registry(sm, app_engine, source, expected):
    d = Driver(sm, app_engine)
    at_step(d, 3)
    d.allow(instruction_source=source)
    assert d.state is State.AWAITING_HUMAN_REVIEW and expected <= set(reasons(d))


def test_d18_high_risk_needs_a_scoped_one_shot_override(sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    d.validate()
    d.classify(safety_level="HighRisk")
    d.plan()
    d.propose()
    assert d.allow().state_after is State.AWAITING_HUMAN_REVIEW
    assert d.case.escalation_kind is EscalationKind.POLICY_REVIEW
    case = d.case
    d.human(Event.HUMAN_APPROVED, d.approval("approve", plan_hash=case.plan_hash, current_step=case.current_step))
    d.propose()
    assert d.allow().state_after is State.RETRIEVING_DATA          # the override, once
    d.retrieved()
    d.advance()
    d.propose()
    assert d.allow().state_after is State.AWAITING_HUMAN_REVIEW     # no reuse on the next step
    assert d.case.escalation_kind is EscalationKind.POLICY_REVIEW


def test_d30_a_temporal_violation_escalates_and_cannot_be_approved(sm, app_engine, monkeypatch):
    """A table bug lets Ready through without ReadinessComplete; the real monitor (T8) stops it."""
    buggy = tuple(replace(t, guards=()) if t.event is Event.READINESS_PASSED else t for t in TRANSITIONS)
    monkeypatch.setattr("hospital_agent.fsm.TRANSITIONS", buggy)
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"])
    result = d.readiness_passed()
    assert (result.committed, result.temporal_violation) == (False, "T8")
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.TEMPORAL_VIOLATION)
    assert not d.human(Event.HUMAN_APPROVED, d.approval("approve")).committed
    assert d.human(Event.HUMAN_REJECTED, d.approval("reject")).state_after is State.FAILED


def test_d31_plan_changed_after_plan_created(sm, app_engine):
    d = Driver(sm, app_engine)
    at_step(d, 2)
    case = d.case
    tampered = replace(case, ordered_steps=[*case.ordered_steps[:3], {"step": 4, "action": "CheckAppointment"}])
    decision = PolicyService(app_engine).decide(tampered, d.request())
    assert decision.result == "Deny" and "plan_modified" in decision.reasons
