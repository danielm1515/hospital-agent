"""The JSON Schemas of the four LLM calls (spec §18.5; LLM design §4).

Every schema is strict: all properties required, no others allowed, and every choice an
enum drawn from the closed lists (naming.py) or the demo's intents. Nothing here lets the
model assert an authoritative fact - no approved/valid/evaluated flags, no appointment,
document or instruction data (§3.1 ApprovedSource, MessageEvaluated).

The provider's strict mode is not trusted alone: validate() checks every answer again.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

import jsonschema

from ..naming import AUTOMATIC_ACTIONS, Action, SafetyLevel


class Intent(StrEnum):
    """LLM design decision 4: the demo's intents."""

    APPOINTMENT_PREPARATION = "AppointmentPreparation"
    MEDICAL_QUESTION = "MedicalQuestion"
    UNSUPPORTED = "Unsupported"


class Call(StrEnum):
    """The four calls of §18.5, each with its own prompt."""

    INTENT = "intent"
    SAFETY = "safety"
    PLANNER = "planner"
    EVALUATOR = "evaluator"


_ACTIONS = [a.value for a in Action if a in AUTOMATIC_ACTIONS]  # declaration order: stable across runs


def _object(**properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


INTENT_SCHEMA = _object(intent={"type": "string", "enum": [i.value for i in Intent]})
SAFETY_SCHEMA = _object(safety_level={"type": "string", "enum": [s.value for s in SafetyLevel]})
PLAN_SCHEMA = _object(
    plan_complete={"type": "boolean"},
    ordered_steps={"type": "array", "items": _object(step={"type": "integer"},
                                                     action={"type": "string", "enum": _ACTIONS})},
)
PROPOSAL_SCHEMA = _object(action={"type": "string", "enum": _ACTIONS}, from_step={"type": "integer"})
EVALUATION_SCHEMA = _object(medical_content_flag={"type": "boolean"})


class LLMUnusable(Exception):
    """One answer that cannot be used: an API or network error, a timeout, bad JSON, or a
    schema violation (LLM design §3). The message is a code, never the model's output."""


def validate(schema: dict[str, Any], data: object) -> dict[str, Any]:
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError:
        raise LLMUnusable("schema_violation") from None
    return data  # type: ignore[return-value]
