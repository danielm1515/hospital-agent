"""One smoke test against the real model (LLM design §8). Skipped unless RUN_LIVE_LLM=1.

Run it once with the key from .env:
    docker compose run --rm -e RUN_LIVE_LLM=1 backend pytest tests/test_live_llm.py -v
It checks that every call returns a schema-valid answer and that the demo request is
classified and planned as an operational one; the words of the answers are not compared.
"""
import os

import pytest

from hospital_agent.llm.classifier import Classifier
from hospital_agent.llm.evaluator import ResponseEvaluator
from hospital_agent.llm.model_selector import select_provider
from hospital_agent.llm.planner import Planner
from hospital_agent.llm.provider import DEMO_PLAN
from hospital_agent.llm.schemas import Intent
from hospital_agent.naming import SafetyLevel

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM") != "1", reason="set RUN_LIVE_LLM=1 to call the real model")


def test_the_real_model_classifies_plans_proposes_and_evaluates():
    provider = select_provider()
    assert provider is not None, "OPENAI_API_KEY is not set"
    classification = Classifier(provider).classify("מתי התור שלי ואילו מסמכים אני צריך להביא?", [])
    assert classification.intent is Intent.APPOINTMENT_PREPARATION
    assert classification.safety_level in (SafetyLevel.LOW_RISK, SafetyLevel.MEDIUM_RISK)
    medical = Classifier(provider).classify("האם להפסיק לקחת מדלל דם לפני הקולונוסקופיה?", [])
    assert medical.intent is Intent.MEDICAL_QUESTION
    planner = Planner(provider)
    plan = planner.plan(classification.intent.value, "מתי התור שלי ואילו מסמכים אני צריך להביא?")
    assert plan.plan_complete and plan.ordered_steps == DEMO_PLAN
    assert planner.propose(DEMO_PLAN, 2, "STEP_ADVANCED") == {"action": "CheckDocuments", "from_step": 2}
    evaluator = ResponseEvaluator(provider)
    try:
        assert evaluator.evaluate("התור שלך נקבע ל־23/09/2026 בשעה 08:30 (UTC).") is False
        assert evaluator.evaluate("מומלץ להפסיק את מדלל הדם שלושה ימים לפני הבדיקה.") is True
    finally:
        evaluator.close()
