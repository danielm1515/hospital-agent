"""Response Evaluator (spec §3.1 MessageEvaluated, §6.5, INV-11).

"קריאת LLM נפרדת עם prompt ייעודי ו־Schema; אינה המודל שייצר את הפלט ורצה כתהליך נפרד
בתוך Planner Service" (§6.5): the call runs in a separate Python process that receives
only the message text - never the Planner's prompt, the request or the State (LLM design
decision 6). evaluate() returns medical_content_flag; only this module's caller may then
mark the message evaluated=True.
"""
from __future__ import annotations

import multiprocessing
from concurrent.futures import ProcessPoolExecutor

from .provider import MAX_ATTEMPTS, LLMFailed, LLMProvider
from .schemas import EVALUATION_SCHEMA, Call, LLMUnusable


def _evaluate_once(provider: LLMProvider, message: str) -> bool:
    """Runs in the evaluator process: one call, the message text only."""
    return provider.complete(Call.EVALUATOR, {"message": message}, EVALUATION_SCHEMA)["medical_content_flag"]


class ResponseEvaluator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self._pool: ProcessPoolExecutor | None = None

    def _process(self) -> ProcessPoolExecutor:
        if self._pool is None:  # spawn: a clean interpreter, nothing inherited from the caller
            self._pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
        return self._pool

    def evaluate(self, message: str) -> bool:
        """medical_content_flag for one outgoing message; LLMFailed after MAX_ATTEMPTS unusable answers."""
        for _ in range(MAX_ATTEMPTS):
            try:
                return self._process().submit(_evaluate_once, self.provider, message).result()
            except LLMUnusable:
                continue
        raise LLMFailed(Call.EVALUATOR.value)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown()
            self._pool = None
