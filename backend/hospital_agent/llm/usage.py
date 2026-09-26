"""LLM token usage: capture and attribution (sub-project 19, design D1 and D2).

- LLMUsage: the three counts one attempt was billed for, read from the provider's own
  `usage` (`prompt_tokens`, `prompt_tokens_details.cached_tokens`, `completion_tokens`).
  usage_from_response() never guesses: a missing or malformed `usage` is None.
- usage_scope(case_id, recorder): a contextvar the Agent Orchestrator sets around one
  case's steps, so every attempt made on its behalf - in its own thread, in the
  Classifier's two threads (they run in a copy of the context) or in the Response
  Evaluator's process (whose parent reports for it) - is recorded against that case.
- report(call, model, outcome, usage): one row per attempt, through the scope's recorder;
  without a scope (the D33 evaluation, obs.golden, most tests) nothing is recorded.

Nothing here carries text: a row is a case id, codes and counts (§12.3). Recording is
bookkeeping, not a safety control - it never raises into the case's path (design §2).
"""
from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

from .schemas import Call

logger = logging.getLogger(__name__)

SOURCE_AGENT = "agent"

# The `call` code stored per row (design D4); Task 3 adds the document-service's two.
CALL_CODES: dict[Call, str] = {
    Call.INTENT: "Intent",
    Call.SAFETY: "Safety",
    Call.PLANNER: "Planner",
    Call.EVALUATOR: "Evaluator",
}


@dataclass(frozen=True)
class LLMUsage:
    """Billed tokens of one attempt: non-negative ints, cached input never above input."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        counts = (self.input_tokens, self.cached_input_tokens, self.output_tokens)
        if not all(type(count) is int and count >= 0 for count in counts):
            raise ValueError("usage_counts_invalid")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("usage_cached_above_input")


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def usage_from_response(response: Any) -> LLMUsage | None:
    """The usage of a Chat Completions response, or None when it is missing or malformed.
    An absent `prompt_tokens_details` / `cached_tokens` is 0 cached tokens. Never raises."""
    try:
        usage = _field(response, "usage")
        if usage is None:
            return None
        cached = _field(_field(usage, "prompt_tokens_details"), "cached_tokens")
        return LLMUsage(_field(usage, "prompt_tokens"), 0 if cached is None else cached,
                        _field(usage, "completion_tokens"))
    except Exception:  # anything odd is "no usage", never a guess
        return None


class UsageSink(Protocol):
    def record(self, case_id: str, source: str, call: str, model: str, outcome: str,
               usage: LLMUsage | None) -> None: ...


@dataclass(frozen=True)
class _Scope:
    case_id: str
    recorder: UsageSink


_scope: ContextVar[_Scope | None] = ContextVar("llm_usage_scope", default=None)


@contextmanager
def usage_scope(case_id: str, recorder: UsageSink) -> Iterator[None]:
    """Every attempt reported inside this block (and in contexts copied from it) is
    recorded against case_id."""
    token = _scope.set(_Scope(case_id, recorder))
    try:
        yield
    finally:
        _scope.reset(token)


def report(call: Call, model: str, outcome: str, usage: LLMUsage | None) -> None:
    """Record one attempt (`ok` or the telemetry reason code) against the current scope's
    case; a no-op without a scope. Never raises."""
    scope = _scope.get()
    if scope is None:
        return
    try:
        scope.recorder.record(scope.case_id, SOURCE_AGENT, CALL_CODES[call], model, outcome, usage)
    except Exception as exc:  # the recorder never raises; this is the second fence
        logger.warning("llm_usage_write_failed error=%s", type(exc).__name__)
