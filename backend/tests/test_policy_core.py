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
from tests.fakes import fake_ports


def decision(sm, d: Driver, event: Event, **payload):
    base = {"action": d.case.current_action.value, "policy_result": "Allow", "policy_reasons": [],
            "execution_id": "EXEC-1"}
    return sm.apply(d.case_id, event, {**base, **payload}, Component.POLICY_SERVICE)


def test_readiness_complete_property():
    case = new_case("CASE-1", "P-1", datetime.now(UTC))
    assert not case.readiness_complete
    assert replace(case, required_documents=["a"], held_documents=["a", "b"]).readiness_complete
    assert not replace(case, required_documents=["a", "c"], held_documents=["a"]).readiness_complete


def test_audit_rows_carry_the_policy_version(app_engine):
    d = Driver(build_state_manager(app_engine, fake_ports()), app_engine)
    d.submit()
    assert len(policy_version()) == 12
    assert d.trace()[0].rule_version == f"{RULE_VERSION}+policy-{policy_version()}"


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
