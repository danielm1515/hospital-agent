"""The Core changes sub-project 2 needs (Policy design §6, §7): rule_version, decision
evidence, override consumption, escalation reasons, CaseRecord.readiness_complete.

Policy decisions are emitted directly here (as the Policy Service would), so these
tests do not depend on the engines.
"""
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from hospital_agent.case import new_case
from hospital_agent.naming import Component, EscalationKind, Event, State
from hospital_agent.state_manager import RULE_VERSION, ReprocessLimitExceeded
from hospital_agent.wiring import build_state_manager, policy_version
from tests.driver import Driver


def decision(sm, d: Driver, event: Event, **payload):
    """A decision as the Policy Service emits it, bound to the case as it is now (ExecutorReverified)."""
    case = d.case
    base = {"action": case.current_action.value, "policy_result": "Allow", "policy_reasons": [],
            "execution_id": f"EXEC-{case.state_version}", "decision_token": "tok",
            "decided_state_version": case.state_version, "plan_hash": case.plan_hash,
            "current_step": case.current_step}
    return sm.apply(d.case_id, event, {**base, **payload}, Component.POLICY_SERVICE)


def test_readiness_complete_property():
    case = new_case("CASE-1", "P-1", datetime.now(UTC))
    assert not case.readiness_complete
    assert replace(case, required_documents=["a"], held_documents=["a", "b"]).readiness_complete
    assert not replace(case, required_documents=["a", "c"], held_documents=["a"]).readiness_complete


def test_audit_rows_carry_the_policy_version(app_engine):
    d = Driver(build_state_manager(app_engine), app_engine)
    d.submit()
    assert len(policy_version()) == 12
    assert d.trace()[0].rule_version == f"{RULE_VERSION}+policy-{policy_version()}"


def test_changing_the_registry_changes_the_policy_version(tmp_path):
    """Fix round 1 (I2): policy_version() must hash the OPA bundle's data files too - a
    change to the approved-instruction-sources registry (or the minimized-fields export)
    changes rule_version exactly as a policy.rego/rules.pl/flows.dl change already does,
    so no two audit rows can carry the same rule_version under two different registries."""
    import hospital_agent.wiring as wiring

    tmp_policy, tmp_data = tmp_path / "policy", tmp_path / "policy" / "data"
    tmp_data.mkdir(parents=True)
    for name in wiring.POLICY_FILES:
        dst = tmp_policy / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((wiring.POLICY_DIR / name).read_bytes())

    original_dir = wiring.POLICY_DIR
    wiring.POLICY_DIR = tmp_policy
    try:
        wiring.policy_version.cache_clear()
        before = wiring.policy_version()
        (tmp_data / "approved_instruction_sources.json").write_text(
            '{"hospital_agent": {"approved_instruction_sources": {}}}', encoding="utf-8")
        wiring.policy_version.cache_clear()
        after = wiring.policy_version()
        assert before != after
    finally:
        wiring.POLICY_DIR = original_dir
        wiring.policy_version.cache_clear()


def test_policy_decision_rows_keep_the_evidence(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    decision(sm, d, Event.POLICY_ALLOWED, evidence={"ContentApprovalValid": True, "medical_content_flag": False,
                                                    "not_a_bool": "x"})
    row = d.trace()[-1]
    assert row.guards == {"ExecutorReverified": True, "retrieval_action": True,
                          "ContentApprovalValid": True, "medical_content_flag": False}


def test_m1_evidence_cannot_forge_or_override_a_real_guard_result(sm, app_engine):
    """M1: evidence may only ever add ContentApprovalValid/medical_content_flag - an unknown key
    is dropped, and a name clash with a real guard result is decided by the real result."""
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    decision(sm, d, Event.POLICY_ALLOWED, evidence={"ExecutorReverified": False, "SomeOtherFlag": True,
                                                    "ContentApprovalValid": True, "medical_content_flag": False})
    row = d.trace()[-1]
    assert row.guards == {"ExecutorReverified": True, "retrieval_action": True,
                          "ContentApprovalValid": True, "medical_content_flag": False}


def test_evidence_on_other_events_is_ignored(sm, app_engine):
    """Only the Policy Service may attach evidence - an external event cannot forge HumanAuthorized."""
    d = Driver(sm, app_engine)
    d.submit()
    sm.apply(d.case_id, Event.REQUEST_VALIDATED, {"text": "x", "identity_verified": True,
                                                  "evidence": {"ContentApprovalValid": True}},
             Component.SESSION_SERVICE)
    assert "ContentApprovalValid" not in d.trace()[-1].guards


def test_escalation_reasons_are_audited(sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    d.validate()
    sm.escalation.signal(d.case_id, EscalationKind.SAFETY_ESCALATION, State.CLASSIFYING,
                         Component.CLASSIFIER_SERVICE, reasons=["safety:HighRisk"])
    assert d.trace()[-1].policy_reasons == ["safety:HighRisk"]


def _approved_policy_review(sm, d: Driver) -> str:
    d.to_classified()
    d.plan()
    d.propose()
    decision(sm, d, Event.POLICY_HUMAN_REVIEW_REQUIRED, policy_result="RequireHumanReview")
    case = d.case
    approval_id = d.approval("approve", plan_hash=case.plan_hash, current_step=case.current_step)
    assert d.human(Event.HUMAN_APPROVED, approval_id).state_after is State.PLANNING
    d.propose()
    return approval_id


def _consumed_at(app_engine, approval_id):
    with app_engine.connect() as conn:
        return conn.execute(text("SELECT consumed_at FROM approvals WHERE approval_id = :id"),
                            {"id": approval_id}).scalar_one()


def test_the_next_policy_decision_consumes_the_override(sm, app_engine):
    """Policy design decision 4: consumed by the next Policy decision, in the same transaction."""
    d = Driver(sm, app_engine)
    approval_id = _approved_policy_review(sm, d)
    assert decision(sm, d, Event.POLICY_DENIED, policy_result="Deny",
                    policy_review_override_id=approval_id).committed
    assert _consumed_at(app_engine, approval_id) is not None


def test_an_already_consumed_override_fails_closed(sm, app_engine):
    d = Driver(sm, app_engine)
    approval_id = _approved_policy_review(sm, d)
    decision(sm, d, Event.POLICY_ALLOWED, policy_review_override_id=approval_id)
    d.retrieved()
    d.advance()
    d.propose()
    with pytest.raises(ReprocessLimitExceeded):
        decision(sm, d, Event.POLICY_ALLOWED, policy_review_override_id=approval_id)
    assert (d.state, d.case.current_step) == (State.PLANNING, 2)
