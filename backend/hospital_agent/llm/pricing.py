"""LLM prices and the cost of one attempt (sub-project 19, design D3).

Prices are USD per 1M tokens, as Decimal - money never goes through a float. The table
holds the owner's figures; `LLM_PRICE_INPUT_PER_MTOK` / `LLM_PRICE_OUTPUT_PER_MTOK`
override the price of the configured model (`OPENAI_MODEL`, default gpt-5.6-luna). A bad
override (not a finite decimal, negative, or above 1000 per 1M tokens) is refused and logged by code, and the table's
price stays. A model the table does not know has no price and its cost is None - never a
guess. Cached input tokens are billed at the full input price (the owner gave two prices;
this is the upper bound) and stored separately.

    cost = input_tokens × input_price / 1e6 + output_tokens × output_price / 1e6

rounded half-up to 8 decimals.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .model_selector import DEFAULT_MODEL
from .usage import LLMUsage

logger = logging.getLogger(__name__)

Price = tuple[Decimal, Decimal]  # (input, output) per 1M tokens

PRICES: dict[str, Price] = {
    "gpt-5.6-luna": (Decimal("0.20"), Decimal("1.20")),
}
INPUT_OVERRIDE = "LLM_PRICE_INPUT_PER_MTOK"
OUTPUT_OVERRIDE = "LLM_PRICE_OUTPUT_PER_MTOK"
MAX_PRICE_PER_MTOK = Decimal(1000)  # an override above this is a typo, not a price
MTOK = Decimal(1_000_000)
QUANTUM = Decimal("0.00000001")


def _override(env: Mapping[str, str], name: str) -> Decimal | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite() or value < 0 or value > MAX_PRICE_PER_MTOK:
        logger.warning("llm_price_override_invalid name=%s", name)
        return None
    return value


def price_table(env: Mapping[str, str] = os.environ) -> dict[str, Price]:
    """PRICES with the configured model's price overridden from the environment. An
    unknown configured model is priced only when both overrides are given and valid."""
    table = dict(PRICES)
    model = env.get("OPENAI_MODEL", "").strip() or DEFAULT_MODEL
    input_price, output_price = table.get(model, (None, None))
    input_override, output_override = _override(env, INPUT_OVERRIDE), _override(env, OUTPUT_OVERRIDE)
    if input_override is not None:  # an explicit 0 is a valid price, so never `or`
        input_price = input_override
    if output_override is not None:
        output_price = output_override
    if input_price is not None and output_price is not None:
        table[model] = (input_price, output_price)
    return table


def cost(model: str, usage: LLMUsage | None,
         prices: Mapping[str, Price] | None = None) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """(input price, output price, cost) of one attempt. An unknown model is
    (None, None, None); a known model with no usage (an API error, a provider that reports
    none) keeps its prices and has cost None."""
    known = (price_table() if prices is None else prices).get(model)
    if known is None:
        return None, None, None
    input_price, output_price = known
    if usage is None:
        return input_price, output_price, None
    total = (Decimal(usage.input_tokens) * input_price + Decimal(usage.output_tokens) * output_price) / MTOK
    return input_price, output_price, total.quantize(QUANTUM, rounding=ROUND_HALF_UP)
