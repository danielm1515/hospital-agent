"""Sub-project 19, design D1-D3: capturing each LLM attempt's usage, attributing it to its
case, and pricing it. No network, no database (the rows themselves: test_llm_costs.py)."""
import logging
import os
import pickle
from concurrent.futures.process import BrokenProcessPool
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from hospital_agent.llm import pricing
from hospital_agent.llm.classifier import Classifier
from hospital_agent.llm.evaluator import ResponseEvaluator
from hospital_agent.llm.provider import MAX_ATTEMPTS, FakeProvider, LLMFailed, OpenAIProvider, ask
from hospital_agent.llm.schemas import INTENT_SCHEMA, Call, LLMUnusable
from hospital_agent.llm.usage import LLMUsage, report, usage_from_response, usage_scope

from .fakes import SpyRecorder, UsageFakeProvider

BILLED = LLMUsage(1200, 200, 30)
SECRET = "very private patient text"

# --- LLMUsage and usage_from_response --------------------------------------------------------


@pytest.mark.parametrize("counts", [
    (-1, 0, 0), (1, 0, -1), (True, 0, 0), (1.0, 0, 0), ("1", 0, 0), (None, 0, 0),
    (10, 11, 0),  # cached above input
])
def test_usage_refuses_anything_but_consistent_non_negative_ints(counts):
    with pytest.raises(ValueError):
        LLMUsage(*counts)


def test_usage_accepts_zeros_and_cached_equal_to_input():
    assert LLMUsage(0, 0, 0) == LLMUsage(0, 0, 0)
    assert LLMUsage(5, 5, 1).cached_input_tokens == 5


def _response(**usage_fields):
    return SimpleNamespace(usage=SimpleNamespace(**usage_fields))


def test_usage_from_a_full_response():
    response = _response(prompt_tokens=1200, completion_tokens=30,
                         prompt_tokens_details=SimpleNamespace(cached_tokens=200))
    assert usage_from_response(response) == BILLED


def test_usage_from_a_mapping_response():
    response = {"usage": {"prompt_tokens": 1200, "completion_tokens": 30,
                          "prompt_tokens_details": {"cached_tokens": 200}}}
    assert usage_from_response(response) == BILLED


@pytest.mark.parametrize("details", [None, SimpleNamespace(), SimpleNamespace(cached_tokens=None)])
def test_absent_cached_tokens_are_zero(details):
    response = _response(prompt_tokens=10, completion_tokens=3, prompt_tokens_details=details)
    assert usage_from_response(response) == LLMUsage(10, 0, 3)


class _Exploding:
    def __getattr__(self, name):
        raise RuntimeError(SECRET)


@pytest.mark.parametrize("response", [
    SimpleNamespace(),                                                     # no usage attribute
    SimpleNamespace(usage=None),                                           # usage null
    _response(completion_tokens=3),                                        # prompt_tokens missing
    _response(prompt_tokens=10),                                           # completion_tokens missing
    _response(prompt_tokens="10", completion_tokens=3),                    # not an int
    _response(prompt_tokens=10.0, completion_tokens=3),
    _response(prompt_tokens=-1, completion_tokens=3),                      # negative
    _response(prompt_tokens=10, completion_tokens=3,
              prompt_tokens_details=SimpleNamespace(cached_tokens="x")),
    _response(prompt_tokens=10, completion_tokens=3,
              prompt_tokens_details=SimpleNamespace(cached_tokens=11)),    # cached above input
    SimpleNamespace(usage=_Exploding()),                                   # a getattr that raises
    None,
])
def test_a_missing_or_malformed_usage_is_none_never_a_guess(response):
    assert usage_from_response(response) is None


# --- OpenAIProvider: usage of answers kept and of answers rejected ---------------------------

def _openai(handler) -> OpenAIProvider:
    return OpenAIProvider("sk-test", "gpt-5.6-luna", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def _chat(content: str, usage_body: dict | None = None) -> httpx.Response:
    body = {"id": "c1", "object": "chat.completion", "created": 0, "model": "gpt-5.6-luna",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}
    if usage_body is not None:
        body["usage"] = usage_body
    return httpx.Response(200, json=body)


USAGE_BODY = {"prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1230,
              "prompt_tokens_details": {"cached_tokens": 200}}


def test_openai_returns_the_answer_with_its_usage():
    provider = _openai(lambda request: _chat('{"intent": "Unsupported"}', USAGE_BODY))
    assert provider.complete_with_usage(Call.INTENT, {"request_text": "x"}, INTENT_SCHEMA) == (
        {"intent": "Unsupported"}, BILLED)
    assert provider.complete(Call.INTENT, {"request_text": "x"}, INTENT_SCHEMA) == {"intent": "Unsupported"}


def test_openai_without_usage_in_the_response_has_none():
    provider = _openai(lambda request: _chat('{"intent": "Unsupported"}'))
    assert provider.complete_with_usage(Call.INTENT, {"request_text": "x"}, INTENT_SCHEMA) == (
        {"intent": "Unsupported"}, None)


@pytest.mark.parametrize("content, reason", [("not json", "unparsable"), ('{"intent": "Billing"}', "schema_violation")])
def test_a_rejected_answer_keeps_its_billed_usage(content, reason):
    provider = _openai(lambda request: _chat(content, USAGE_BODY))
    with pytest.raises(LLMUnusable, match=f"^{reason}$") as excinfo:
        provider.complete_with_usage(Call.INTENT, {"request_text": "x"}, INTENT_SCHEMA)
    assert (excinfo.value.usage, excinfo.value.retryable) == (BILLED, True)


def test_an_api_error_carries_no_usage():
    provider = _openai(lambda request: httpx.Response(500, json={"error": {"message": "boom"}}))
    with pytest.raises(LLMUnusable, match="^api:InternalServerError$") as excinfo:
        provider.complete_with_usage(Call.INTENT, {"request_text": "x"}, INTENT_SCHEMA)
    assert excinfo.value.usage is None


def test_llm_unusable_pickles_with_its_usage():
    clone = pickle.loads(pickle.dumps(LLMUnusable("schema_violation", usage=BILLED)))
    assert (clone.reason, clone.retryable, clone.usage) == ("schema_violation", True, BILLED)


# --- the usage scope and ask() ---------------------------------------------------------------

def test_report_outside_a_scope_records_nothing():
    spy = SpyRecorder()
    report(Call.INTENT, "fake", "ok", BILLED)
    with usage_scope("CASE-1", spy):
        pass
    report(Call.INTENT, "fake", "ok", BILLED)
    assert spy.rows == []


def test_the_scope_ends_with_its_block_even_on_an_exception():
    spy = SpyRecorder()
    with pytest.raises(RuntimeError), usage_scope("CASE-1", spy):
        raise RuntimeError("x")
    report(Call.INTENT, "fake", "ok", None)
    assert spy.rows == []


def test_ask_reports_an_ok_attempt_with_its_usage():
    spy = SpyRecorder()
    with usage_scope("CASE-1", spy):
        ask(UsageFakeProvider(BILLED), Call.INTENT, {"request_text": "hi"}, INTENT_SCHEMA)
    assert spy.rows == [("CASE-1", "agent", "Intent", "fake", "ok", BILLED)]


def test_ask_reports_every_failed_attempt_with_its_usage():
    spy = SpyRecorder()
    provider = FakeProvider({Call.INTENT: [LLMUnusable("schema_violation", usage=BILLED),
                                           LLMUnusable("api:InternalServerError"),
                                           {"intent": "Unsupported"}]})
    with usage_scope("CASE-1", spy):
        ask(provider, Call.INTENT, {"request_text": "hi"}, INTENT_SCHEMA)
    assert spy.rows == [("CASE-1", "agent", "Intent", "fake", "schema_violation", BILLED),
                        ("CASE-1", "agent", "Intent", "fake", "api:InternalServerError", None),
                        ("CASE-1", "agent", "Intent", "fake", "ok", None)]  # FakeProvider reports no usage


def test_ask_reports_all_three_attempts_before_it_gives_up():
    spy = SpyRecorder()
    provider = FakeProvider({Call.PLANNER: [LLMUnusable("unparsable", usage=BILLED)] * MAX_ATTEMPTS})
    with usage_scope("CASE-1", spy), pytest.raises(LLMFailed):
        ask(provider, Call.PLANNER, {"task": "plan"}, INTENT_SCHEMA)
    assert [(row[2], row[4], row[5]) for row in spy.rows] == [("Planner", "unparsable", BILLED)] * MAX_ATTEMPTS


class _FailingRecorder:
    def record(self, *args) -> None:
        raise RuntimeError(SECRET)


def test_a_failing_recorder_never_breaks_the_call(caplog):
    with caplog.at_level(logging.WARNING, logger="hospital_agent.llm.usage"), usage_scope("CASE-1", _FailingRecorder()):
        assert ask(FakeProvider(), Call.INTENT, {"request_text": SECRET}, INTENT_SCHEMA) == {
            "intent": "AppointmentPreparation"}
    assert [record.message for record in caplog.records] == ["llm_usage_write_failed error=RuntimeError"]


def test_the_classifier_keeps_the_scope_in_both_threads():
    spy = SpyRecorder()
    with usage_scope("CASE-7", spy):
        Classifier(UsageFakeProvider(BILLED)).classify("When is my appointment?", [])
    assert sorted(spy.rows) == [("CASE-7", "agent", "Intent", "fake", "ok", BILLED),
                                ("CASE-7", "agent", "Safety", "fake", "ok", BILLED)]


def test_the_classifier_outside_a_scope_records_nothing():
    spy = SpyRecorder()
    with usage_scope("CASE-7", spy):
        pass
    Classifier(UsageFakeProvider(BILLED)).classify("When is my appointment?", [])
    assert spy.rows == []


# --- the Response Evaluator: usage crosses the process ---------------------------------------

def test_the_evaluators_usage_crosses_the_process():
    spy = SpyRecorder()
    evaluator = ResponseEvaluator(UsageFakeProvider(BILLED))
    try:
        with usage_scope("CASE-9", spy):
            assert evaluator.evaluate("Your appointment is on Monday.") is False
    finally:
        evaluator.close()
    assert spy.rows == [("CASE-9", "agent", "Evaluator", "fake", "ok", BILLED)]


def test_a_rejected_evaluator_answer_keeps_its_usage_across_the_process():
    spy = SpyRecorder()
    evaluator = ResponseEvaluator(FakeProvider({Call.EVALUATOR: [LLMUnusable("schema_violation", usage=BILLED)]}))
    try:
        with usage_scope("CASE-9", spy), pytest.raises(LLMFailed):
            evaluator.evaluate("message")
    finally:
        evaluator.close()
    # the worker re-reads the unpickled script each time, so every attempt is the same rejection
    assert spy.rows == [("CASE-9", "agent", "Evaluator", "fake", "schema_violation", BILLED)] * MAX_ATTEMPTS


def test_a_dead_evaluator_worker_is_reported_with_no_usage():
    """Chosen rule: one usage row per telemetry line - the dead worker's attempt is recorded,
    with NULL usage (whether it reached the provider is unknown; nothing billed came back)."""
    spy = SpyRecorder()
    evaluator = ResponseEvaluator(UsageFakeProvider(BILLED))
    try:
        with pytest.raises(BrokenProcessPool):
            evaluator._process().submit(os._exit, 1).result()  # the worker dies; the pool is broken
        with usage_scope("CASE-9", spy):
            assert evaluator.evaluate("You should stop taking your medication") is True
    finally:
        evaluator.close()
    assert [(row[4], row[5]) for row in spy.rows] == [("worker_died", None), ("ok", BILLED)]


# --- pricing ---------------------------------------------------------------------------------

LUNA = {"OPENAI_MODEL": "gpt-5.6-luna"}


def test_the_table_prices_gpt_5_6_luna():
    assert pricing.price_table({}) == {"gpt-5.6-luna": (Decimal("0.20"), Decimal("1.20"))}
    assert pricing.price_table(LUNA) == pricing.PRICES


def test_the_cost_of_a_known_model():
    # 1200 × 0.20 / 1e6 + 30 × 1.20 / 1e6 = 0.00024 + 0.000036; cached tokens at the full input price
    assert pricing.cost("gpt-5.6-luna", BILLED, pricing.price_table(LUNA)) == (
        Decimal("0.20"), Decimal("1.20"), Decimal("0.00027600"))


def test_cost_reads_the_environment_when_no_table_is_given(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("LLM_PRICE_INPUT_PER_MTOK", "1")
    monkeypatch.delenv("LLM_PRICE_OUTPUT_PER_MTOK", raising=False)
    assert pricing.cost("gpt-5.6-luna", LLMUsage(1_000_000, 0, 0)) == (Decimal("1"), Decimal("1.20"), Decimal("1"))


def test_a_known_model_without_usage_keeps_its_prices_and_has_no_cost():
    assert pricing.cost("gpt-5.6-luna", None, pricing.PRICES) == (Decimal("0.20"), Decimal("1.20"), None)


@pytest.mark.parametrize("model", ["fake", "gpt-4o", ""])
def test_an_unknown_model_is_never_priced(model):
    assert pricing.cost(model, BILLED, pricing.PRICES) == (None, None, None)
    assert pricing.cost(model, None, pricing.PRICES) == (None, None, None)


def test_the_override_prices_the_configured_model():
    table = pricing.price_table({**LUNA, "LLM_PRICE_INPUT_PER_MTOK": "0.25", "LLM_PRICE_OUTPUT_PER_MTOK": "2"})
    assert table["gpt-5.6-luna"] == (Decimal("0.25"), Decimal("2"))


def test_one_override_keeps_the_other_table_price():
    assert pricing.price_table({**LUNA, "LLM_PRICE_OUTPUT_PER_MTOK": "0"})["gpt-5.6-luna"] == (
        Decimal("0.20"), Decimal("0"))  # an explicit zero is a price, not "unset"


def test_the_override_applies_to_the_configured_model_only():
    table = pricing.price_table({"OPENAI_MODEL": "gpt-other", "LLM_PRICE_INPUT_PER_MTOK": "1",
                                 "LLM_PRICE_OUTPUT_PER_MTOK": "3"})
    assert table == {"gpt-5.6-luna": (Decimal("0.20"), Decimal("1.20")), "gpt-other": (Decimal("1"), Decimal("3"))}


def test_an_unknown_configured_model_with_one_override_stays_unpriced():
    assert "gpt-other" not in pricing.price_table({"OPENAI_MODEL": "gpt-other", "LLM_PRICE_INPUT_PER_MTOK": "1"})


@pytest.mark.parametrize("bad", ["abc", "-0.1", "NaN", "Infinity", "1,5", "sk-" + SECRET])
def test_a_bad_override_is_refused_and_logged_by_code_only(bad, caplog):
    with caplog.at_level(logging.WARNING, logger="hospital_agent.llm.pricing"):
        table = pricing.price_table({**LUNA, "LLM_PRICE_INPUT_PER_MTOK": bad})
    assert table["gpt-5.6-luna"] == (Decimal("0.20"), Decimal("1.20"))
    assert [record.message for record in caplog.records] == ["llm_price_override_invalid name=LLM_PRICE_INPUT_PER_MTOK"]


def test_an_empty_override_is_no_override(caplog):
    with caplog.at_level(logging.WARNING, logger="hospital_agent.llm.pricing"):
        assert pricing.price_table({**LUNA, "LLM_PRICE_INPUT_PER_MTOK": " "}) == pricing.PRICES
    assert caplog.records == []


@pytest.mark.parametrize("input_price, expected", [
    ("0.005", Decimal("0.00000001")),   # 5e-9 rounds half up
    ("0.004", Decimal("0.00000000")),   # 4e-9 rounds down
    ("0.20", Decimal("0.00000020")),
])
def test_cost_is_rounded_half_up_to_eight_decimals(input_price, expected):
    _, _, amount = pricing.cost("m", LLMUsage(1, 0, 0), {"m": (Decimal(input_price), Decimal("0"))})
    assert amount == expected
    assert amount.as_tuple().exponent == -8


def test_a_large_usage_is_exact():
    _, _, amount = pricing.cost("gpt-5.6-luna", LLMUsage(123_456_789, 0, 98_765_432), pricing.PRICES)
    # 123456789 × 0.20 / 1e6 = 24.6913578; 98765432 × 1.20 / 1e6 = 118.5185184
    assert amount == Decimal("143.20987620")

