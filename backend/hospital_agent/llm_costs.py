"""LLM usage rows (sub-project 19, design D2-D6): the repository side of `llm_usage`.

UsageRecorder.record() prices one attempt (llm/pricing.py) and inserts its row in its own
short transaction - never inside StateManager.apply's. It is bookkeeping, not a safety
control (design §2): any failure is logged as `llm_usage_write_failed` (codes only) and
swallowed, so a failed write never changes a case's path.

The read side (design D6) - a page of cases' costs in one grouped query, one case's usage -
follows one NULL rule everywhere (total_cost): a cost is the SUM of the non-null costs; it is
None when there is no row at all, or when there is an unpriced row
(`price_input_per_mtok IS NULL`, an unknown model) and no priced cost at all; rows that are
all priced API errors (NULL tokens, NULL cost) cost 0. "Unpriced" is never `cost_usd IS NULL`,
which an API error on a priced model also is.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Connection, Engine

from .db import llm_usage
from .llm import pricing
from .llm.usage import LLMUsage

logger = logging.getLogger(__name__)

ZERO = Decimal(0).quantize(pricing.QUANTUM)


def money(value: Decimal) -> Decimal:
    """8 decimal places, half-up - the cost column's own rounding (llm/pricing.py)."""
    return value.quantize(pricing.QUANTUM, rounding=ROUND_HALF_UP)


def total_cost(cost_sum: Decimal | None, calls: int, unpriced_calls: int) -> Decimal | None:
    """The NULL rule (module docstring), from SUM(cost_usd), COUNT(*) and the unpriced count."""
    if cost_sum is not None:
        return money(cost_sum)
    if calls == 0 or unpriced_calls > 0:
        return None
    return ZERO


@dataclass(frozen=True)
class CallUsage:
    call: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None


@dataclass(frozen=True)
class SourceUsage:
    source: str
    calls: int
    cost_usd: Decimal | None


@dataclass(frozen=True)
class CaseUsage:
    """One case's usage (`CaseDetail.llm_usage`). Token sums count only the attempts that
    reported usage; `calls` counts every attempt."""

    calls: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    unpriced_calls: int
    by_call: list[CallUsage]


_CALLS = func.count().label("calls")
_COST = func.sum(llm_usage.c.cost_usd).label("cost")
_UNPRICED = func.count().filter(llm_usage.c.price_input_per_mtok.is_(None)).label("unpriced")


def _tokens(column: Any, name: str) -> Any:
    return func.coalesce(func.sum(column), 0).label(name)


@dataclass(frozen=True)
class CaseCost:
    """One case's cost on the Case Monitor list. `partial`: the case has an unpriced row AND a
    known (non-null) cost - the cost shown is a lower bound, the unpriced attempts are not in it."""

    cost_usd: Decimal | None
    partial: bool


NO_COST = CaseCost(None, partial=False)


def case_costs(conn: Connection, case_ids: Sequence[str]) -> dict[str, CaseCost]:
    """Each case's cost, in ONE grouped query for the whole page (never one per case); a case
    with no row is NO_COST."""
    if not case_ids:
        return {}
    costs: dict[str, CaseCost] = dict.fromkeys(case_ids, NO_COST)
    query = (select(llm_usage.c.case_id, _CALLS, _COST, _UNPRICED)
             .where(llm_usage.c.case_id.in_(list(case_ids))).group_by(llm_usage.c.case_id))
    for row in conn.execute(query):
        cost = total_cost(row.cost, row.calls, row.unpriced)
        costs[row.case_id] = CaseCost(cost, partial=row.unpriced > 0 and cost is not None)
    return costs


def case_usage(conn: Connection, case_id: str) -> CaseUsage:
    """One case's totals and its per-call breakdown (ordered by call code)."""
    query = (select(llm_usage.c.call, _CALLS, _COST, _UNPRICED,
                    _tokens(llm_usage.c.input_tokens, "input_tokens"),
                    _tokens(llm_usage.c.cached_input_tokens, "cached_input_tokens"),
                    _tokens(llm_usage.c.output_tokens, "output_tokens"))
             .where(llm_usage.c.case_id == case_id).group_by(llm_usage.c.call).order_by(llm_usage.c.call))
    rows = conn.execute(query).all()
    by_call = [CallUsage(row.call, row.calls, row.input_tokens, row.output_tokens,
                         total_cost(row.cost, row.calls, row.unpriced)) for row in rows]
    costs = [row.cost for row in rows if row.cost is not None]
    calls, unpriced = sum(row.calls for row in rows), sum(row.unpriced for row in rows)
    return CaseUsage(calls=calls, input_tokens=sum(row.input_tokens for row in rows),
                     cached_input_tokens=sum(row.cached_input_tokens for row in rows),
                     output_tokens=sum(row.output_tokens for row in rows),
                     cost_usd=total_cost(sum(costs) if costs else None, calls, unpriced),
                     unpriced_calls=unpriced, by_call=by_call)


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
