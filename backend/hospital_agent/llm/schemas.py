"""The JSON Schemas of the four LLM calls (spec §18.5; LLM design §4).

Every schema is strict: all properties required, no others allowed, and every choice an
enum drawn from the closed lists (naming.py) or the demo's intents. Nothing here lets the
model assert an authoritative fact - no approved/valid/evaluated flags, no appointment,
document or instruction data (§3.1 ApprovedSource, MessageEvaluated).

The provider's strict mode is not trusted alone: validate() checks every answer again.
"""
from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import jsonschema

from ..naming import AUTOMATIC_ACTIONS, Action, SafetyLevel

if TYPE_CHECKING:
    from .usage import LLMUsage


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


_CODE_PATTERN = re.compile(r"[A-Za-z0-9_]{1,64}")

# Staff-fixes design Task 1, decision 2: no retry can fix these - ask()/the Evaluator give up
# at once instead of spending MAX_ATTEMPTS on a call that cannot succeed.
NON_RETRYABLE_EXCEPTION_TYPES = frozenset({"AuthenticationError", "PermissionDeniedError", "NotFoundError"})
NON_RETRYABLE_RATE_LIMIT_CODES = frozenset({"insufficient_quota", "credit_balance_exhausted"})


def sanitize_code(code: object) -> str | None:
    """A short API error code, safe to log and to put in a reason string - or None. Never the
    model's output, never patient data: just OpenAI's own short error-code token."""
    if isinstance(code, str):
        match = _CODE_PATTERN.fullmatch(code)
        if match:
            return match.group()
    return None


def is_retryable(reason: str) -> bool:
    """Whether a reason from LLMUnusable can still be fixed by trying again. `unparsable` and
    `schema_violation` always are; an `api:<ExceptionType>[:<code>]` reason is not when no
    retry could help (LLM design decision 2)."""
    if not reason.startswith("api:"):
        return True
    exc_type, _, code = reason.removeprefix("api:").partition(":")
    if exc_type in NON_RETRYABLE_EXCEPTION_TYPES:
        return False
    return not (exc_type == "RateLimitError" and code in NON_RETRYABLE_RATE_LIMIT_CODES)


class LLMUnusable(Exception):
    """One answer that cannot be used: an API or network error, a timeout, bad JSON, or a
    schema violation (LLM design §3). The message is a code, never the model's output.

    `retryable` defaults to `is_retryable(reason)` - true for `unparsable`/`schema_violation`
    and for an API error no retry could fix; an explicit value overrides it (e.g. a test).

    `usage` is the billed usage of an answer that arrived but was rejected (unparsable,
    schema-invalid) - sub-project 19, design D1; None for an API error, which bills nothing
    to report. It rides along when the exception is pickled back from the Evaluator's process."""

    def __init__(self, reason: str, *, retryable: bool | None = None, usage: LLMUsage | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = is_retryable(reason) if retryable is None else retryable
        self.usage = usage


def validate(schema: dict[str, Any], data: object) -> dict[str, Any]:
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError:
        raise LLMUnusable("schema_violation") from None
    return data  # type: ignore[return-value]
