"""Response Evaluator (spec §3.1 MessageEvaluated, §6.5, INV-11).

"קריאת LLM נפרדת עם prompt ייעודי ו־Schema; אינה המודל שייצר את הפלט ורצה כתהליך נפרד
בתוך Planner Service" (§6.5): the call runs in a separate Python process that receives
only the message text - never the Planner's prompt, the request or the State (LLM design
decision 6). evaluate() returns medical_content_flag; only this module's caller may then
mark the message evaluated=True.

A worker process that dies leaves its pool broken for good (BrokenProcessPool): that pool is
dropped and the attempt counts as unusable, so the next attempt gets a fresh process and
three in a row still fail closed as LLMFailed (§14). After close() no process is ever
created again - a tick still running then gets LLMFailed("evaluator_closed").
"""
from __future__ import annotations

import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from . import telemetry, usage
from .provider import MAX_ATTEMPTS, LLMFailed, LLMProvider, complete_once, elapsed_ms
from .schemas import EVALUATION_SCHEMA, Call, LLMUnusable
from .usage import LLMUsage


def _evaluate_once(provider: LLMProvider, message: str) -> tuple[bool, LLMUsage | None]:
    """Runs in the evaluator process: one call, the message text only. Returns the flag and
    the attempt's usage, which the parent reports (sub-project 19, design D2) - a rejected
    answer's usage rides back on the pickled LLMUnusable instead."""
    answer, billed = complete_once(provider, Call.EVALUATOR, {"message": message}, EVALUATION_SCHEMA)
    return answer["medical_content_flag"], billed


class ResponseEvaluator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self._pool: ProcessPoolExecutor | None = None
        self._closed = False

    def _process(self) -> ProcessPoolExecutor:
        if self._closed:  # never leak a new process after close()
            raise LLMFailed("evaluator_closed")
        if self._pool is None:  # spawn: a clean interpreter, nothing inherited from the caller
            self._pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
        return self._pool

    def evaluate(self, message: str) -> bool:
        """medical_content_flag for one outgoing message; LLMFailed after MAX_ATTEMPTS unusable
        answers, or at once on a failure no retry could fix. The call runs in a worker process
        with no log configuration, so this parent-side loop times and records every attempt
        (staff-fixes design Task 1, decisions 1-3), and reports it with its usage to the
        case's usage scope (sub-project 19, design D2) - one row per telemetry line. A dead
        worker's attempt is reported with no usage: whether it reached the provider is
        unknown, and nothing billed came back."""
        model = self.provider.model
        for _ in range(MAX_ATTEMPTS):
            start = time.monotonic()
            try:
                result, billed = self._process().submit(_evaluate_once, self.provider, message).result()
            except LLMUnusable as exc:
                telemetry.record(Call.EVALUATOR, model, elapsed_ms(start), exc.reason)
                usage.report(Call.EVALUATOR, model, exc.reason, exc.usage)
                if not exc.retryable:
                    raise LLMFailed(Call.EVALUATOR.value) from None
                continue
            except BrokenProcessPool:  # the worker died: replace it on the next attempt
                telemetry.record(Call.EVALUATOR, model, elapsed_ms(start), "worker_died")
                usage.report(Call.EVALUATOR, model, "worker_died", None)
                self._discard_pool()
                continue
            else:
                telemetry.record(Call.EVALUATOR, model, elapsed_ms(start), "ok")
                usage.report(Call.EVALUATOR, model, "ok", billed)
                return result
        raise LLMFailed(Call.EVALUATOR.value)

    def _discard_pool(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None

    def close(self) -> None:
        self._closed = True
        if self._pool is not None:
            self._pool.shutdown()
            self._pool = None
