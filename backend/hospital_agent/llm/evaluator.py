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

from . import telemetry
from .provider import MAX_ATTEMPTS, LLMFailed, LLMProvider, elapsed_ms
from .schemas import EVALUATION_SCHEMA, Call, LLMUnusable


def _evaluate_once(provider: LLMProvider, message: str) -> bool:
    """Runs in the evaluator process: one call, the message text only."""
    return provider.complete(Call.EVALUATOR, {"message": message}, EVALUATION_SCHEMA)["medical_content_flag"]


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
        (staff-fixes design Task 1, decisions 1-3)."""
        for _ in range(MAX_ATTEMPTS):
            start = time.monotonic()
            try:
                result = self._process().submit(_evaluate_once, self.provider, message).result()
            except LLMUnusable as exc:
                telemetry.record(Call.EVALUATOR, self.provider.model, elapsed_ms(start), exc.reason)
                if not exc.retryable:
                    raise LLMFailed(Call.EVALUATOR.value) from None
                continue
            except BrokenProcessPool:  # the worker died: replace it on the next attempt
                telemetry.record(Call.EVALUATOR, self.provider.model, elapsed_ms(start), "worker_died")
                self._discard_pool()
                continue
            else:
                telemetry.record(Call.EVALUATOR, self.provider.model, elapsed_ms(start), "ok")
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
