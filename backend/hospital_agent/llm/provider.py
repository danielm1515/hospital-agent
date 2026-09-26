"""LLM providers (spec §18.5; LLM design §3).

One model, four calls. Every provider returns an answer already checked against its
schema, or raises LLMUnusable. ask() retries an unusable answer and gives up after
MAX_ATTEMPTS in a row with LLMFailed, which the caller turns into the §14 escalation
(ClassificationFailed / PlanningFailed).

- OpenAIProvider: the real model (gpt-5.6-luna by default). No temperature - the model
  rejects every value but the default (LLM design decision 1) - so reasoning_effort="none"
  and a strict JSON Schema instead.
- FakeProvider: deterministic rules over the input, plus optional scripted answers; used
  by the tests and by `python -m obs.golden`, never by the server.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any, Protocol

from . import telemetry
from .schemas import Call, LLMUnusable, sanitize_code, validate

MAX_ATTEMPTS = 3  # §14: three unusable answers in a row escalate
PROMPTS_DIR = Path(__file__).with_name("prompts")


@cache
def prompt(call: Call) -> str:
    return (PROMPTS_DIR / f"{call.value}.md").read_text(encoding="utf-8")


@cache
def prompts_version() -> str:
    """Hash of the four prompts, recorded in rule_version (§18.5)."""
    digest = hashlib.sha256()
    for call in Call:
        digest.update(prompt(call).encode("utf-8"))
    return digest.hexdigest()[:12]


class LLMFailed(Exception):
    """MAX_ATTEMPTS unusable answers in a row for one call."""


class LLMProvider(Protocol):
    model: str

    def complete(self, call: Call, user_input: Mapping[str, Any], schema: dict[str, Any]) -> dict[str, Any]: ...


def elapsed_ms(start: float) -> int:
    return round((time.monotonic() - start) * 1000)


def ask(provider: LLMProvider, call: Call, user_input: Mapping[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """One call, retried on a retryable unusable answer; LLMFailed after MAX_ATTEMPTS in a
    row, or at once on a failure no retry could fix (staff-fixes design Task 1, decision 2).
    Every attempt is logged and its outcome kept in telemetry (decisions 1, 3)."""
    for _ in range(MAX_ATTEMPTS):
        start = time.monotonic()
        try:
            result = provider.complete(call, user_input, schema)
        except LLMUnusable as exc:
            telemetry.record(call, provider.model, elapsed_ms(start), exc.reason)
            if not exc.retryable:
                raise LLMFailed(call.value) from None
            continue
        telemetry.record(call, provider.model, elapsed_ms(start), "ok")
        return result
    raise LLMFailed(call.value)


class OpenAIProvider:
    """gpt-5.6-luna through the Chat Completions API. The client is built lazily and never
    pickled, so the provider can be handed to the Response Evaluator's process."""

    def __init__(self, api_key: str, model: str, *, timeout_seconds: float = 30.0, http_client: Any = None) -> None:
        self.model, self._api_key, self._timeout, self._http_client = model, api_key, timeout_seconds, http_client
        self._client: Any = None

    def __getstate__(self) -> dict[str, Any]:
        return {**self.__dict__, "_client": None, "_http_client": None}

    def _openai(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self._api_key, timeout=self._timeout, max_retries=0,
                                  http_client=self._http_client)
        return self._client

    def complete(self, call: Call, user_input: Mapping[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        import openai

        try:
            response = self._openai().chat.completions.create(
                model=self.model,
                reasoning_effort="none",
                messages=[{"role": "system", "content": prompt(call)},
                          {"role": "user", "content": json.dumps(user_input, ensure_ascii=False)}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": call.value, "strict": True, "schema": schema}},
            )
            data = json.loads(response.choices[0].message.content)
        except openai.OpenAIError as exc:
            reason = f"api:{type(exc).__name__}"
            body = getattr(exc, "body", None)
            body_get = body.get if isinstance(body, Mapping) else lambda _key: None
            # Fix round 1, M5: a 429 sometimes carries `code: null, type: "insufficient_quota"`
            # (the live diagnosis's own example) - `.type`/`body["type"]` is the fallback, tried
            # only once `.code`/`body["code"]` gave nothing, so a real code is never overridden.
            code = (sanitize_code(getattr(exc, "code", None)) or sanitize_code(body_get("code"))
                    or sanitize_code(getattr(exc, "type", None)) or sanitize_code(body_get("type")))
            raise LLMUnusable(f"{reason}:{code}" if code else reason) from None
        except (json.JSONDecodeError, TypeError, IndexError, AttributeError):
            raise LLMUnusable("unparsable") from None
        return validate(schema, data)


# --- FakeProvider: deterministic demo answers --------------------------------------------

DEMO_PLAN = [
    {"step": 1, "action": "CheckAppointment"},
    {"step": 2, "action": "CheckDocuments"},
    {"step": 3, "action": "LoadInstructions"},
    {"step": 4, "action": "SendStatusUpdate"},
]
MEDICAL_WORDS = ("medication", "blood thinner", "stop taking", "dose", "symptom", "pain", "diagnos", "should i")
CRITICAL_WORDS = ("chest pain", "can't breathe", "cannot breathe", "suicid", "overdose")
HIGH_WORDS = ("bleeding", "severe", "faint", "worse")
UNSUPPORTED_WORDS = ("invoice", "bill", "parking")


def _text(user_input: Mapping[str, Any]) -> str:
    parts = [user_input.get("request_text") or "", user_input.get("content") or "", user_input.get("message") or "",
             *user_input.get("documents", ())]
    return " ".join(parts).lower()


def _has(text: str, words: Sequence[str]) -> bool:
    return any(word in text for word in words)


def _rule(call: Call, user_input: Mapping[str, Any]) -> dict[str, Any]:
    text = _text(user_input)
    match call:
        case Call.INTENT:
            if _has(text, MEDICAL_WORDS):
                return {"intent": "MedicalQuestion"}
            return {"intent": "Unsupported" if _has(text, UNSUPPORTED_WORDS) else "AppointmentPreparation"}
        case Call.SAFETY:
            if _has(text, CRITICAL_WORDS):
                return {"safety_level": "CriticalRisk"}
            return {"safety_level": "HighRisk" if _has(text, HIGH_WORDS + MEDICAL_WORDS) else "MediumRisk"}
        case Call.PLANNER if user_input["task"] == "plan":
            if user_input["intent"] == "AppointmentPreparation":
                return {"plan_complete": True, "ordered_steps": DEMO_PLAN}
            return {"plan_complete": False, "ordered_steps": []}
        case Call.PLANNER:
            step = user_input["current_step"]
            return {"action": user_input["ordered_steps"][step - 1]["action"], "from_step": step}
        case Call.EVALUATOR:
            return {"medical_content_flag": _has(text, MEDICAL_WORDS)}


class FakeProvider:
    """Deterministic answers from _rule(); `script` overrides them per call, in order - each
    item is an answer dict or an exception to raise (e.g. LLMUnusable). A provider handed to
    the Response Evaluator's process is pickled on every submit, so there its script does not
    advance across calls (each call sees the original script) and `calls` stays empty here."""

    model = "fake"

    def __init__(self, script: Mapping[Call, Sequence[dict[str, Any] | Exception]] | None = None) -> None:
        self.script = {call: list(items) for call, items in (script or {}).items()}
        self.calls: list[tuple[Call, dict[str, Any]]] = []

    def complete(self, call: Call, user_input: Mapping[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((call, dict(user_input)))
        queue = self.script.get(call)
        answer = queue.pop(0) if queue else _rule(call, user_input)
        if isinstance(answer, Exception):
            raise answer
        return validate(schema, answer)
