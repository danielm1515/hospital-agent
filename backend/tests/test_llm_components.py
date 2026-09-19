"""Classifier, Planner, Response Evaluator and the status template (LLM design §4, §5). No network."""
import os
import threading
from datetime import UTC, datetime

import pytest

from hospital_agent.case import CaseRecord
from hospital_agent.llm.classifier import Classification, Classifier, verdict
from hospital_agent.llm.evaluator import ResponseEvaluator
from hospital_agent.llm.message import status_message
from hospital_agent.llm.planner import Planner
from hospital_agent.llm.provider import DEMO_PLAN, FakeProvider, LLMFailed
from hospital_agent.llm.schemas import Call, Intent, LLMUnusable, validate
from hospital_agent.naming import EscalationKind, Event, SafetyLevel, State
from hospital_agent.policy.service import InstructionSource

# --- Classifier ------------------------------------------------------------------------------


@pytest.mark.parametrize("intent", list(Intent))
@pytest.mark.parametrize("level", list(SafetyLevel))
def test_verdict_follows_the_section_14_precedence(intent, level):
    outcome = verdict(Classification(intent, level))
    if intent is Intent.MEDICAL_QUESTION:
        assert outcome is Event.MEDICAL_QUESTION_DETECTED          # first, whatever the risk
    elif level in (SafetyLevel.HIGH_RISK, SafetyLevel.CRITICAL_RISK):
        assert outcome is EscalationKind.SAFETY_ESCALATION          # D26: not medical, still stopped
    else:
        assert outcome is Event.INTENT_CLASSIFIED


def test_classify_makes_two_separate_calls_on_the_same_input():
    provider = FakeProvider()
    result = Classifier(provider).classify("When is my appointment?", ["Blood test results"])
    assert result == Classification(Intent.APPOINTMENT_PREPARATION, SafetyLevel.MEDIUM_RISK)
    assert sorted(call for call, _ in provider.calls) == [Call.INTENT, Call.SAFETY]
    assert all(user_input == {"request_text": "When is my appointment?", "documents": ["Blood test results"]}
               for _, user_input in provider.calls)


class GatedSafety(FakeProvider):
    """Intent answers at once; Safety waits until the test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.intent_answered, self.release = threading.Event(), threading.Event()

    def complete(self, call, user_input, schema):
        if call is Call.SAFETY:
            assert self.release.wait(5)
        answer = super().complete(call, user_input, schema)
        if call is Call.INTENT:
            self.intent_answered.set()
        return answer


def test_d21_classify_waits_for_safety_even_when_intent_is_back():
    provider = GatedSafety()
    results = []
    worker = threading.Thread(target=lambda: results.append(Classifier(provider).classify("appointment?", [])))
    worker.start()
    assert provider.intent_answered.wait(5)
    worker.join(0.2)
    assert worker.is_alive() and results == []  # the barrier holds
    provider.release.set()
    worker.join(5)
    assert results == [Classification(Intent.APPOINTMENT_PREPARATION, SafetyLevel.MEDIUM_RISK)]


def test_classify_fails_after_three_unusable_answers():
    provider = FakeProvider({Call.SAFETY: [LLMUnusable("x")] * 3})
    with pytest.raises(LLMFailed, match="safety"):
        Classifier(provider).classify("appointment?", [])


def test_safety_of_retrieved_content():
    assert Classifier(FakeProvider()).safety("Clear liquids the day before") is SafetyLevel.MEDIUM_RISK
    provider = FakeProvider({Call.SAFETY: [{"safety_level": "HighRisk"}]})
    assert Classifier(provider).safety("x") is SafetyLevel.HIGH_RISK
    assert provider.calls == [(Call.SAFETY, {"content": "x"})]


# --- Planner ---------------------------------------------------------------------------------

def test_plan_for_the_demo_intent_and_for_an_unsupported_one():
    planner = Planner(FakeProvider())
    plan = planner.plan("AppointmentPreparation", "When is my appointment?")
    assert (plan.plan_complete, plan.ordered_steps) == (True, DEMO_PLAN)
    assert not planner.plan("Unsupported", "Where do I pay?").plan_complete


def test_propose_never_sees_the_request_text():
    provider = FakeProvider()
    proposal = Planner(provider).propose(DEMO_PLAN, 2, "STEP_ADVANCED")
    assert proposal == {"action": "CheckDocuments", "from_step": 2}
    [(_, user_input)] = provider.calls
    assert user_input == {"task": "propose", "ordered_steps": DEMO_PLAN, "current_step": 2,
                          "last_event": "STEP_ADVANCED"}


# --- Response Evaluator ------------------------------------------------------------------------

class ProcessReporter:
    """Picklable: answers medical_content_flag=True only when it runs in another process."""

    model = "process-reporter"

    def __init__(self, parent_pid: int) -> None:
        self.parent_pid = parent_pid

    def complete(self, call, user_input, schema):
        assert call is Call.EVALUATOR and set(user_input) == {"message"}  # the message text only
        return validate(schema, {"medical_content_flag": os.getpid() != self.parent_pid})


def test_the_evaluator_runs_in_a_separate_process():
    evaluator = ResponseEvaluator(ProcessReporter(os.getpid()))
    try:
        assert evaluator.evaluate("Your appointment is on Monday.") is True
    finally:
        evaluator.close()


def test_the_evaluator_flags_medical_content():
    evaluator = ResponseEvaluator(FakeProvider())
    try:
        assert evaluator.evaluate("התור שלך נקבע ל־22/09/2026") is False
        assert evaluator.evaluate("You should stop taking your medication") is True
    finally:
        evaluator.close()


def test_the_evaluator_fails_after_three_unusable_answers():
    evaluator = ResponseEvaluator(FakeProvider({Call.EVALUATOR: [LLMUnusable("x")] * 5}))
    try:
        with pytest.raises(LLMFailed, match="evaluator"):
            evaluator.evaluate("message")
    finally:
        evaluator.close()


# --- the status template ------------------------------------------------------------------------

def _case(**changes) -> CaseRecord:
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    fields = dict(case_id="CASE-1", patient_id="P-10041", state=State.PLANNING, state_version=9, created_at=now,
                  updated_at=now, appointment_at=datetime(2026, 9, 23, 8, 30, tzinfo=UTC),
                  required_documents=["referral", "blood_test"], held_documents=["referral", "blood_test"])
    return CaseRecord(**{**fields, **changes})


def test_status_message_uses_only_state_facts():
    text = status_message(_case(), InstructionSource("INSTR-PREP-COLONOSCOPY", "3"))
    assert text == ("התור שלך נקבע ל־23/09/2026 בשעה 08:30 (UTC). "
                    "המסמכים הנדרשים: referral, blood_test - כולם התקבלו. "
                    "הוראות ההכנה המאושרות (INSTR-PREP-COLONOSCOPY, גרסה 3) מצורפות להודעה זו.")


def test_status_message_refuses_missing_facts():
    with pytest.raises(ValueError):
        status_message(_case(appointment_at=None), InstructionSource("INSTR-PREP-COLONOSCOPY", "3"))
