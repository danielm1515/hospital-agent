"""Policy Service: OPA + Prolog folded into one decision, and its event (Policy design §6)."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent.case import ApprovalRecord, compute_plan_hash, new_case
from hospital_agent.naming import Event, SafetyLevel, State
from hospital_agent.policy.approvals import content_approval_valid
from hospital_agent.policy.opa_runner import OpaDecision
from hospital_agent.policy.service import (
    OutgoingMessage,
    PolicyDecision,
    PolicyRequest,
    PolicyService,
    ProposedAction,
    build_opa_input,
    decision_event,
    prolog_verdict,
)

NOW = datetime.now(UTC)
PLAN = [{"step": 1, "action": "CheckAppointment"}, {"step": 2, "action": "CheckDocuments"},
        {"step": 3, "action": "LoadInstructions"}, {"step": 4, "action": "SendStatusUpdate"}]


def planned(step=2, **changes):
    case = replace(new_case("CASE-482", "P-10041", NOW), state=State.PLANNING, identity_verified=True,
                   safety_level=SafetyLevel.MEDIUM_RISK, intent="AppointmentPreparation",
                   ordered_steps=PLAN, plan_hash=compute_plan_hash(PLAN), current_step=step)
    return replace(case, **changes)


def request(action="CheckDocuments", step=2, target="document_system", fields=("patient_id",), **changes):
    return replace(PolicyRequest("EXEC-482-02", ProposedAction(action, step, target, fields)), **changes)


def content_approval(**changes):
    base = ApprovalRecord(
        approval_id="APPR-C1", approval_type="ContentApproval", case_id="CASE-482", patient_id="P-10041",
        reviewer_id="coordinator_nurse", reviewer_role="clinical_staff", decision="approve", reason="ok",
        shown_context_ref="ctx", granted_at=NOW - timedelta(minutes=5), valid_until=NOW + timedelta(hours=1),
        execution_id="EXEC-482-02", action="SendStatusUpdate", content_hash="HASH-1")
    return replace(base, **changes)


MEDICAL = OutgoingMessage(evaluated=True, medical_content_flag=True, content_hash="HASH-1")


def delivery_request(**changes):
    return request("SendStatusUpdate", 4, "patient_channel", ("patient_id",), outgoing_message=MEDICAL, **changes)


# --- building the OPA input ------------------------------------------------------------


def test_opa_input_comes_from_stored_state_and_trusted_request():
    data = build_opa_input(planned(), request(), None)
    assert data["plan"] == {"current_step": 2, "plan_hash": compute_plan_hash(PLAN), "ordered_steps": PLAN}
    assert data["execution"] == {"attempt_count": 0, "max_attempts": 3}
    assert data["proposed_action"]["parameters"] == {"patient_fields": ["patient_id"]}
    assert (data["safety_level"], data["identity_verified"], data["approval"]) == ("MediumRisk", True, None)


def test_approvals_are_sent_with_rfc3339_times_and_policy_review_origin():
    override = content_approval(approval_type="WorkflowDecision", escalation_kind="PolicyReview")
    data = build_opa_input(planned(), request(), override)["policy_review_override"]
    assert data["granted_at"] == override.granted_at.isoformat()
    assert data["escalated_from_state"] == "Planning" and data["consumed_at"] is None


# --- Prolog --------------------------------------------------------------------------------


def test_prolog_allows_the_current_step_and_explains_a_block():
    assert prolog_verdict(planned(), request(), approval_ok=False) == (True, "allowed")
    wrong_step = request("LoadInstructions", 3, "instruction_system", ())
    assert prolog_verdict(planned(), wrong_step, approval_ok=False) == (
        False, "reason(action_not_current_step, load_instructions)")


def test_prolog_needs_the_validated_content_approval_for_medical_output():
    case = planned(step=4)
    assert prolog_verdict(case, delivery_request(), approval_ok=False)[0] is False
    assert prolog_verdict(case, delivery_request(), approval_ok=True) == (True, "allowed")


def test_prolog_rejects_an_unknown_action():
    assert prolog_verdict(planned(), request("DeleteRecords"), approval_ok=False) == (False, "action_not_supported")


# --- folding the two engines ------------------------------------------------------------


def service(app_engine, opa_result, *reasons):
    return PolicyService(app_engine, opa=lambda _: OpaDecision(opa_result, tuple(reasons)))


def test_both_allow(app_engine):
    decision = service(app_engine, "Allow").decide(planned(), request())
    assert (decision.result, decision.reasons) == ("Allow", ())


def test_opa_deny_wins(app_engine):
    decision = service(app_engine, "Deny", "identity_not_verified").decide(planned(), request())
    assert (decision.result, decision.reasons) == ("Deny", ("identity_not_verified",))


def test_prolog_disagreement_denies(app_engine):
    """§14: Prolog disagreeing with OPA blocks."""
    decision = service(app_engine, "Allow").decide(planned(), request("LoadInstructions", 3, "instruction_system", ()))
    assert (decision.result, decision.reasons) == ("Deny", ("prolog:reason(action_not_current_step, load_instructions)",))


def test_require_human_review_passes_through(app_engine):
    decision = service(app_engine, "RequireHumanReview", "human_decision_required").decide(planned(), request())
    assert (decision.result, decision.reasons) == ("RequireHumanReview", ("human_decision_required",))


def test_prolog_failure_fails_closed(app_engine, monkeypatch):
    def broken(*args):
        raise RuntimeError("engine crashed")

    monkeypatch.setattr("hospital_agent.policy.service.prolog_verdict", broken)
    decision = service(app_engine, "Allow").decide(planned(), request())
    assert (decision.result, decision.reasons) == ("Deny", ("prolog:policy_engine_unavailable",))


def test_real_engines_allow_the_spec_8_example(app_engine):
    decision = PolicyService(app_engine).decide(planned(), request(fields=("patient_id", "document_id")))
    assert (decision.result, decision.reasons) == ("Allow", ())


# --- the event ------------------------------------------------------------------------------


@pytest.mark.parametrize("result, event", [("Allow", Event.POLICY_ALLOWED), ("Deny", Event.POLICY_DENIED),
                                           ("RequireHumanReview", Event.POLICY_HUMAN_REVIEW_REQUIRED)])
def test_decision_event(result, event):
    decision = PolicyDecision(result, (), "CheckDocuments", "EXEC-1", "tok", content_hash="H",
                              content_approval_valid=True, medical_content_flag=True,
                              policy_review_override_id="APPR-9")
    got_event, payload = decision_event(decision)
    assert got_event is event
    assert payload["action"] == "CheckDocuments" and payload["decision_token"] == "tok"
    assert payload["evidence"] == {"ContentApprovalValid": True, "medical_content_flag": True}
    assert payload["content_hash"] == "H" and payload["policy_review_override_id"] == "APPR-9"


# --- Approval Validator -------------------------------------------------------------------


def test_content_approval_validator():
    def valid(approval):
        return content_approval_valid(approval, case_id="CASE-482", patient_id="P-10041", execution_id="EXEC-482-02",
                                      action="SendStatusUpdate", content_hash="HASH-1", now=NOW)

    assert valid(content_approval())
    for change in [{"approval_type": "WorkflowDecision"}, {"reviewer_role": "admin_staff"},
                   {"case_id": "CASE-2"}, {"execution_id": "EXEC-OTHER"}, {"content_hash": "OTHER"},
                   {"valid_until": NOW - timedelta(seconds=1)}, {"consumed_at": NOW}, {"decision": "reject"},
                   {"action": "CheckDocuments"}]:
        assert not valid(content_approval(**change)), change
    assert not valid(None)
