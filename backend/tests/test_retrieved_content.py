"""Re-checking retrieved content (spec §3; LLM design §5, decision 3) at the Tool Executor.

The instructions' text goes to the Data Log, never into the event; the Safety Classifier's
level rides on DATA_RETRIEVED and can only raise the case's risk - there and on
re-classification alike.
"""
import pytest

from hospital_agent import data_log
from hospital_agent.execution.executor import ToolExecutor
from hospital_agent.execution.gateway import INSTRUCTION_TEXT, MockGateway
from hospital_agent.naming import EscalationKind, SafetyLevel, State
from tests.driver import Driver


def to_load_instructions(d: Driver) -> None:
    """Steps 1-2 done; LoadInstructions proposed next (all documents held)."""
    d.to_classified()
    d.plan()
    d.run_step()
    d.advance()
    d.run_step()
    d.advance()


def driver(sm, app_engine, content_check) -> Driver:
    """A Driver whose Tool Executor re-checks retrieved content; every required document is held."""
    d = Driver(sm, app_engine)
    d.executor = ToolExecutor(sm, MockGateway(required_documents=("referral",), held_documents=("referral",)),
                              content_check=content_check)
    return d


def test_instructions_go_to_the_data_log_and_their_risk_to_the_event(sm, app_engine):
    checked = []
    d = driver(sm, app_engine, lambda text: checked.append(text) or SafetyLevel.HIGH_RISK)
    to_load_instructions(d)
    d.run_step()
    assert checked == [INSTRUCTION_TEXT]
    assert (d.state, d.case.safety_level) == (State.ASSESSING_READINESS, SafetyLevel.HIGH_RISK)
    with app_engine.connect() as conn:
        [entry] = data_log.entries(conn, d.case_id, data_log.DataKind.INSTRUCTIONS)
    assert entry.content == INSTRUCTION_TEXT


def test_a_lower_level_never_lowers_the_risk(sm, app_engine):
    d = driver(sm, app_engine, lambda text: SafetyLevel.LOW_RISK)
    to_load_instructions(d)  # classified MediumRisk
    d.run_step()
    assert d.case.safety_level is SafetyLevel.MEDIUM_RISK


def test_the_next_step_needs_a_human_after_a_risk_rise(sm, app_engine):
    d = driver(sm, app_engine, lambda text: SafetyLevel.HIGH_RISK)
    to_load_instructions(d)
    d.run_step()
    d.assess()
    d.plan_delivery()
    assert d.run_step().state_after is State.AWAITING_HUMAN_REVIEW
    assert d.case.escalation_kind is EscalationKind.POLICY_REVIEW


def test_a_content_check_that_raises_escalates(sm, app_engine):
    def broken(text):
        raise RuntimeError("model down")

    d = driver(sm, app_engine, broken)
    to_load_instructions(d)
    d.run_step()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.EXECUTION_UNKNOWN)
    assert d.trace()[-1].policy_reasons == ["content_check_failed:RuntimeError"]
    assert [row.record_type for row in d.trace()].count("ExecutionSucceeded") == 3  # the call itself succeeded


def test_without_a_content_check_the_text_is_still_kept(sm, app_engine):
    d = Driver(sm, app_engine)
    to_load_instructions(d)
    d.run_step()
    with app_engine.connect() as conn:
        assert len(data_log.entries(conn, d.case_id, data_log.DataKind.INSTRUCTIONS)) == 1
    assert d.case.safety_level is SafetyLevel.MEDIUM_RISK


def test_reclassification_never_lowers_the_risk(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_assessing_readiness(required=["referral", "blood_test"], held=["referral"])  # classified MediumRisk
    d.missing_information()
    d.upload("blood_test")
    d.classify("LowRisk")  # re-classified after the upload
    assert (d.state, d.case.safety_level) == (State.ASSESSING_READINESS, SafetyLevel.MEDIUM_RISK)


@pytest.mark.parametrize("level", ["Extreme", 3, None])
def test_an_invalid_level_on_the_event_is_rejected(sm, app_engine, level):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    result = d.retrieved(safety_level=level)
    assert not result.committed and result.reason == "invalid_tool_result"
