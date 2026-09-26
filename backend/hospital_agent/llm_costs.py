"""LLM usage rows (sub-project 19, design D2-D4): the repository side of `llm_usage`.

UsageRecorder.record() prices one attempt (llm/pricing.py) and inserts its row in its own
short transaction - never inside StateManager.apply's. It is bookkeeping, not a safety
control (design §2): any failure is logged as `llm_usage_write_failed` (codes only) and
swallowed, so a failed write never changes a case's path.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from sqlalchemy import insert
from sqlalchemy.engine import Engine

from .db import llm_usage
from .llm import pricing
from .llm.usage import LLMUsage

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class UsageRecorder:
    """Writes llm_usage rows through `engine`. The price table is read once, here - a bad
    override is logged at startup, not on every call."""

    def __init__(self, engine: Engine, *, prices: Mapping[str, pricing.Price] | None = None,
                 clock: Callable[[], datetime] = _now) -> None:
        self.engine = engine
        self.prices = pricing.price_table() if prices is None else dict(prices)
        self.clock = clock

    def record(self, case_id: str, source: str, call: str, model: str, outcome: str,
               usage: LLMUsage | None) -> None:
        """One attempt's row. Never raises."""
        try:
            price_input, price_output, cost_usd = pricing.cost(model, usage, self.prices)
            with self.engine.begin() as conn:
                conn.execute(insert(llm_usage).values(
                    case_id=case_id, source=source, call=call, model=model, outcome=outcome,
                    input_tokens=usage.input_tokens if usage else None,
                    cached_input_tokens=usage.cached_input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    price_input_per_mtok=price_input, price_output_per_mtok=price_output, cost_usd=cost_usd,
                    created_at=self.clock(),
                ))
        except Exception as exc:  # bookkeeping never changes the case's path (design §2)
            logger.warning("llm_usage_write_failed error=%s", type(exc).__name__)
