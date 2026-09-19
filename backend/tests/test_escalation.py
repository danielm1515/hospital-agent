"""Escalation Coordinator: the only way HUMAN_REVIEW_REQUIRED enters the system (§3.1, §13.2)."""
import pytest

from hospital_agent.naming import Component, EscalationKind, Event, State
from tests.driver import Driver


@pytest.fixture
def classifying(sm, app_engine) -> Driver:
    d = Driver(sm, app_engine)
    d.submit()
    d.validate()
    return d


def test_valid_signal_escalates(sm, classifying):
    result = sm.escalation.signal(classifying.case_id, EscalationKind.SAFETY_ESCALATION, State.CLASSIFYING,
                                  Component.CLASSIFIER_SERVICE)
    assert result.committed and result.state_after is State.AWAITING_HUMAN_REVIEW
    case = classifying.case
    assert (case.escalation_kind, case.escalated_from_state) == (EscalationKind.SAFETY_ESCALATION, State.CLASSIFYING)


@pytest.mark.parametrize("kind, from_state, source", [
    ("SafetyEscalation", "Classifying", Component.SESSION_SERVICE),     # not an authorized signal source
    ("SafetyEscalation", "Classifying", Component.EXTERNAL),
    ("DeliveryStepMissing", "Classifying", Component.CLASSIFIER_SERVICE),  # not allowed from Classifying
    ("SafetyEscalation", "Planning", Component.CLASSIFIER_SERVICE),     # origin is not the current State
    ("NotAKind", "Classifying", Component.CLASSIFIER_SERVICE),
])
def test_invalid_signal_is_blocked(sm, classifying, kind, from_state, source):
    result = sm.escalation.signal(classifying.case_id, kind, from_state, source)
    assert (result.committed, result.reason) == (False, "invalid_escalation_reason")
    assert classifying.state is State.CLASSIFYING
    assert classifying.trace()[-1].record_type == "Blocked"


def test_human_review_required_cannot_be_injected_directly(sm, classifying):
    payload = {"escalation_kind": "SafetyEscalation", "escalated_from_state": "Classifying"}
    result = sm.apply(classifying.case_id, Event.HUMAN_REVIEW_REQUIRED, payload, Component.CLASSIFIER_SERVICE)
    assert (result.committed, result.reason) == (False, "system_owned_event")


def test_non_resumable_escalation_can_only_be_resolved_or_rejected(sm, classifying):
    sm.escalation.signal(classifying.case_id, EscalationKind.SAFETY_ESCALATION, State.CLASSIFYING,
                         Component.CLASSIFIER_SERVICE)
    approve = classifying.human(Event.HUMAN_APPROVED, classifying.approval("approve"))
    assert (approve.committed, approve.reason) == (False, "guard_failed")
    reject = classifying.human(Event.HUMAN_REJECTED, classifying.approval("reject"))
    assert reject.committed and classifying.state is State.FAILED
