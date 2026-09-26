"""Classifier Service (spec §1): two separate calls, intent and risk level.

classify() runs both calls in parallel and returns only when both have answered: an intent
that is back before its safety level never releases the classification barrier (D21).
The result is turned into an event by the §14 precedence (verdict()):

    intent MedicalQuestion                  -> MEDICAL_QUESTION_DETECTED
    otherwise HighRisk / CriticalRisk       -> SafetyEscalation (via the Escalation Coordinator)
    otherwise                               -> INTENT_CLASSIFIED

safety() is the same Safety call on retrieved content (§3: "Safety Classifier בודק מחדש
תוכן שנשלף").
"""
from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..naming import EscalationKind, Event, SafetyLevel
from .provider import LLMProvider, ask
from .schemas import INTENT_SCHEMA, SAFETY_SCHEMA, Call, Intent

HIGH_RISK = frozenset({SafetyLevel.HIGH_RISK, SafetyLevel.CRITICAL_RISK})


@dataclass(frozen=True)
class Classification:
    intent: Intent
    safety_level: SafetyLevel


def verdict(classification: Classification) -> Event | EscalationKind:
    """The §14 precedence: a medical question first, then a high risk, then the plan."""
    if classification.intent is Intent.MEDICAL_QUESTION:
        return Event.MEDICAL_QUESTION_DETECTED
    if classification.safety_level in HIGH_RISK:
        return EscalationKind.SAFETY_ESCALATION
    return Event.INTENT_CLASSIFIED


class Classifier:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def classify(self, request_text: str, documents: list[str]) -> Classification:
        """Both calls, in parallel; LLMFailed if either gives MAX_ATTEMPTS unusable answers.

        Each call runs in its own copy of the caller's context, so the case's usage scope
        (sub-project 19, design D2) reaches both threads - a pool thread does not inherit it.
        One copy each: a single Context cannot be entered by two threads at once."""
        user_input = {"request_text": request_text, "documents": documents}
        with ThreadPoolExecutor(max_workers=2) as pool:
            intent = pool.submit(contextvars.copy_context().run, ask, self.provider, Call.INTENT, user_input,
                                 INTENT_SCHEMA)
            safety = pool.submit(contextvars.copy_context().run, ask, self.provider, Call.SAFETY, user_input,
                                 SAFETY_SCHEMA)
            return Classification(Intent(intent.result()["intent"]), SafetyLevel(safety.result()["safety_level"]))

    def safety(self, content: str) -> SafetyLevel:
        return SafetyLevel(ask(self.provider, Call.SAFETY, {"content": content}, SAFETY_SCHEMA)["safety_level"])
