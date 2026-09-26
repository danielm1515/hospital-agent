"""LLM providers, schemas and the Model Selector (spec §18.5; LLM design §3, §4). No network."""
import json
import logging
import pickle

import httpx
import pytest

from hospital_agent.llm import telemetry
from hospital_agent.llm.model_selector import DEFAULT_MODEL, llm_version, select_provider
from hospital_agent.llm.provider import (
    DEMO_PLAN, MAX_ATTEMPTS, FakeProvider, LLMFailed, OpenAIProvider, ask, prompt, prompts_version,
)
from hospital_agent.llm.schemas import (
    EVALUATION_SCHEMA, INTENT_SCHEMA, PLAN_SCHEMA, PROPOSAL_SCHEMA, SAFETY_SCHEMA, Call, LLMUnusable, is_retryable,
    sanitize_code, validate,
)
from hospital_agent.wiring import build_state_manager

# --- schemas -----------------------------------------------------------------------------


@pytest.mark.parametrize("schema, answer", [
    (INTENT_SCHEMA, {"intent": "MedicalQuestion"}),
    (SAFETY_SCHEMA, {"safety_level": "CriticalRisk"}),
    (PLAN_SCHEMA, {"plan_complete": True, "ordered_steps": DEMO_PLAN}),
    (PLAN_SCHEMA, {"plan_complete": False, "ordered_steps": []}),
    (PROPOSAL_SCHEMA, {"action": "CheckDocuments", "from_step": 2}),
    (EVALUATION_SCHEMA, {"medical_content_flag": False}),
])
def test_valid_answers_pass(schema, answer):
    assert validate(schema, answer) == answer


@pytest.mark.parametrize("schema, answer", [
    (INTENT_SCHEMA, {"intent": "Billing"}),                                        # not an intent
    (SAFETY_SCHEMA, {"safety_level": "Low"}),
    (PLAN_SCHEMA, {"plan_complete": True, "ordered_steps": [{"step": 1, "action": "CloseMedicalCase"}]}),  # human-only
    (PROPOSAL_SCHEMA, {"action": "AnswerClinicalQuestion", "from_step": 1}),       # human-only
    (PROPOSAL_SCHEMA, {"action": "CheckDocuments"}),                               # missing field
    (EVALUATION_SCHEMA, {"medical_content_flag": False, "evaluated": True}),       # may not assert evaluated
    (EVALUATION_SCHEMA, ["not", "an", "object"]),
])
def test_invalid_answers_are_unusable(schema, answer):
    with pytest.raises(LLMUnusable, match="schema_violation"):
        validate(schema, answer)


def test_no_schema_lets_the_model_assert_a_fact():
    for schema in (INTENT_SCHEMA, SAFETY_SCHEMA, PLAN_SCHEMA, PROPOSAL_SCHEMA, EVALUATION_SCHEMA):
        assert not {"approved", "valid", "evaluated", "appointment_at", "held_documents"} & set(schema["properties"])


# --- ask(): three unusable answers in a row ------------------------------------------------

def test_ask_retries_an_unusable_answer():
    provider = FakeProvider({Call.INTENT: [LLMUnusable("x"), LLMUnusable("x"), {"intent": "Unsupported"}]})
    assert ask(provider, Call.INTENT, {"request_text": "hi"}, INTENT_SCHEMA) == {"intent": "Unsupported"}


def test_ask_gives_up_after_max_attempts():
    provider = FakeProvider({Call.INTENT: [LLMUnusable("x")] * MAX_ATTEMPTS})
    with pytest.raises(LLMFailed, match="intent"):
        ask(provider, Call.INTENT, {"request_text": "hi"}, INTENT_SCHEMA)
    assert len(provider.calls) == MAX_ATTEMPTS == 3


def test_a_scripted_answer_is_still_schema_checked():
    provider = FakeProvider({Call.SAFETY: [{"safety_level": "Unknown"}]})
    with pytest.raises(LLMUnusable):
        provider.complete(Call.SAFETY, {"content": "x"}, SAFETY_SCHEMA)


# --- staff-fixes design Task 1: no retry can fix some failures ------------------------------

@pytest.mark.parametrize("reason", [
    "unparsable", "schema_violation", "api:InternalServerError", "api:RateLimitError",
    "api:RateLimitError:some_other_code", "x",
])
def test_retryable_reasons(reason):
    assert is_retryable(reason)


@pytest.mark.parametrize("reason", [
    "api:AuthenticationError", "api:PermissionDeniedError", "api:NotFoundError",
    "api:RateLimitError:insufficient_quota", "api:RateLimitError:credit_balance_exhausted",
])
def test_non_retryable_reasons(reason):
    assert not is_retryable(reason)


def test_llm_unusable_computes_retryable_from_its_reason_by_default():
    assert LLMUnusable("schema_violation").retryable
    assert not LLMUnusable("api:AuthenticationError").retryable


def test_llm_unusable_retryable_can_be_overridden():
    assert not LLMUnusable("schema_violation", retryable=False).retryable
    assert LLMUnusable("api:AuthenticationError", retryable=True).retryable


def test_llm_unusable_pickles_with_its_retryable_flag():
    """The Response Evaluator's worker process pickles LLMUnusable across the process
    boundary (evaluator.py) - the new attribute must survive that."""
    clone = pickle.loads(pickle.dumps(LLMUnusable("api:AuthenticationError")))
    assert (clone.reason, clone.retryable) == ("api:AuthenticationError", False)


@pytest.mark.parametrize("code, expected", [
    ("insufficient_quota", "insufficient_quota"),
    ("a" * 64, "a" * 64),
    ("a" * 65, None),          # over the length limit
    ("bad code!", None),       # not [A-Za-z0-9_]
    (None, None),
    (123, None),               # not a string
])
def test_sanitize_code(code, expected):
    assert sanitize_code(code) == expected


def test_ask_gives_up_at_once_on_a_non_retryable_reason():
    provider = FakeProvider({Call.INTENT: [LLMUnusable("api:AuthenticationError")]})
    with pytest.raises(LLMFailed, match="intent"):
        ask(provider, Call.INTENT, {"request_text": "hi"}, INTENT_SCHEMA)
    assert len(provider.calls) == 1  # the two pointless retries are saved


def test_ask_still_retries_a_retryable_api_error():
    provider = FakeProvider({Call.INTENT: [LLMUnusable("api:RateLimitError")] * MAX_ATTEMPTS})
    with pytest.raises(LLMFailed, match="intent"):
        ask(provider, Call.INTENT, {"request_text": "hi"}, INTENT_SCHEMA)
    assert len(provider.calls) == MAX_ATTEMPTS == 3


def test_ask_logs_and_records_every_attempt(caplog):
    provider = FakeProvider({Call.INTENT: [LLMUnusable("schema_violation"), {"intent": "Unsupported"}]})
    with caplog.at_level(logging.INFO, logger="hospital_agent.llm"):
        ask(provider, Call.INTENT, {"request_text": "very private patient text"}, INTENT_SCHEMA)
    lines = [record.message for record in caplog.records]
    assert any(l.startswith("llm call=intent model=fake ms=") and l.endswith("outcome=schema_violation")
              for l in lines)
    assert any(l.startswith("llm call=intent model=fake ms=") and l.endswith("outcome=ok") for l in lines)
    assert not any("very private patient text" in l for l in lines)  # §12.3: no request content
    assert telemetry.status()["last_ok_at"] is not None


def test_ask_records_the_last_error_when_it_finally_fails():
    provider = FakeProvider({Call.INTENT: [LLMUnusable("schema_violation")] * MAX_ATTEMPTS})
    with pytest.raises(LLMFailed):
        ask(provider, Call.INTENT, {"request_text": "hi"}, INTENT_SCHEMA)
    status = telemetry.status()
    assert status["last_error"] == "schema_violation"
    assert status["last_error_at"] is not None
    assert status["last_ok_at"] is None


# --- FakeProvider's deterministic rules -----------------------------------------------------

@pytest.mark.parametrize("text, intent, level", [
    ("When is my appointment and which documents do I need?", "AppointmentPreparation", "MediumRisk"),
    ("Should I stop taking my blood thinner before the colonoscopy?", "MedicalQuestion", "HighRisk"),
    ("I have chest pain since this morning, is my appointment still on?", "MedicalQuestion", "CriticalRisk"),
    ("Where do I pay the invoice for parking?", "Unsupported", "MediumRisk"),
])
def test_fake_rules(text, intent, level):
    provider = FakeProvider()
    assert provider.complete(Call.INTENT, {"request_text": text, "documents": []}, INTENT_SCHEMA)["intent"] == intent
    assert provider.complete(Call.SAFETY, {"request_text": text, "documents": []}, SAFETY_SCHEMA)["safety_level"] == level


def test_fake_planner_plans_and_proposes_the_current_step():
    provider = FakeProvider()
    plan = provider.complete(Call.PLANNER, {"task": "plan", "intent": "AppointmentPreparation", "request_text": ""},
                             PLAN_SCHEMA)
    assert plan == {"plan_complete": True, "ordered_steps": DEMO_PLAN}
    proposal = provider.complete(Call.PLANNER, {"task": "propose", "ordered_steps": DEMO_PLAN, "current_step": 3,
                                                "last_event": "STEP_ADVANCED"}, PROPOSAL_SCHEMA)
    assert proposal == {"action": "LoadInstructions", "from_step": 3}


# --- OpenAIProvider, against a mock HTTP transport ------------------------------------------

def _openai(handler) -> OpenAIProvider:
    return OpenAIProvider("sk-test", "gpt-5.6-luna", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def _chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "c1", "object": "chat.completion", "created": 0, "model": "gpt-5.6-luna",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
    })


def test_openai_request_shape():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return _chat('{"intent": "AppointmentPreparation"}')

    answer = _openai(handler).complete(Call.INTENT, {"request_text": "מתי התור שלי?"}, INTENT_SCHEMA)
    assert answer == {"intent": "AppointmentPreparation"}
    [body] = sent
    assert body["model"] == "gpt-5.6-luna"
    assert body["reasoning_effort"] == "none"
    assert "temperature" not in body  # LLM design decision 1: the model accepts no other value
    assert body["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "intent", "strict": True, "schema": INTENT_SCHEMA}}
    assert body["messages"][0] == {"role": "system", "content": prompt(Call.INTENT)}
    assert json.loads(body["messages"][1]["content"]) == {"request_text": "מתי התור שלי?"}


@pytest.mark.parametrize("response, code", [
    (httpx.Response(500, json={"error": {"message": "boom"}}), "api:InternalServerError"),
    (httpx.Response(429, json={"error": {"message": "slow down"}}), "api:RateLimitError"),
    (_chat("not json"), "unparsable"),
    (_chat('{"intent": "Billing"}'), "schema_violation"),
])
def test_openai_unusable_answers(response, code):
    with pytest.raises(LLMUnusable, match=code):
        _openai(lambda request: response).complete(Call.INTENT, {"request_text": "x"}, INTENT_SCHEMA)


def test_openai_timeout_is_unusable():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMUnusable, match="api:APITimeoutError"):
        _openai(handler).complete(Call.INTENT, {"request_text": "x"}, INTENT_SCHEMA)


@pytest.mark.parametrize("status, body, reason, retryable", [
    (429, {"error": {"message": "no credit", "code": "insufficient_quota"}},
     "api:RateLimitError:insufficient_quota", False),
    (429, {"error": {"message": "no credit", "code": "credit_balance_exhausted"}},
     "api:RateLimitError:credit_balance_exhausted", False),
    (429, {"error": {"message": "slow down", "code": "rate_limit_exceeded"}},
     "api:RateLimitError:rate_limit_exceeded", True),
    (401, {"error": {"message": "bad key", "code": "invalid_api_key"}},
     "api:AuthenticationError:invalid_api_key", False),
    (401, {"error": {"message": "bad key"}}, "api:AuthenticationError", False),  # no code at all
    (403, {"error": {"message": "denied"}}, "api:PermissionDeniedError", False),
    (404, {"error": {"message": "no such model"}}, "api:NotFoundError", False),
    # Fix round 1, M5 - the live diagnosis's own shape: code is null, the quota signal is
    # only in `type`.
    (429, {"error": {"message": "no credit", "code": None, "type": "insufficient_quota"}},
     "api:RateLimitError:insufficient_quota", False),
    (429, {"error": {"message": "no credit", "code": None, "type": "credit_balance_exhausted"}},
     "api:RateLimitError:credit_balance_exhausted", False),
    # `type` is a fallback only: a real `code` always wins over `type`.
    (429, {"error": {"message": "no credit", "code": "rate_limit_exceeded", "type": "insufficient_quota"}},
     "api:RateLimitError:rate_limit_exceeded", True),
])
def test_openai_carries_the_api_code_and_marks_it_retryable(status, body, reason, retryable):
    with pytest.raises(LLMUnusable, match=f"^{reason}$") as excinfo:
        _openai(lambda request: httpx.Response(status, json=body)).complete(
            Call.INTENT, {"request_text": "x"}, INTENT_SCHEMA)
    assert (excinfo.value.reason, excinfo.value.retryable) == (reason, retryable)


def test_openai_drops_an_unsafe_looking_code():
    # A code with characters outside [A-Za-z0-9_] is dropped rather than logged verbatim (§12.3).
    body = {"error": {"message": "boom", "code": "weird; code with spaces"}}
    with pytest.raises(LLMUnusable, match="^api:InternalServerError$"):
        _openai(lambda request: httpx.Response(500, json=body)).complete(
            Call.INTENT, {"request_text": "x"}, INTENT_SCHEMA)


def test_openai_provider_pickles_without_its_client():
    provider = _openai(lambda request: _chat('{"intent": "Unsupported"}'))
    provider.complete(Call.INTENT, {"request_text": "x"}, INTENT_SCHEMA)
    clone = pickle.loads(pickle.dumps(provider))
    assert (clone.model, clone._client, clone._http_client) == ("gpt-5.6-luna", None, None)


# --- Model Selector and rule_version --------------------------------------------------------

def test_no_key_no_provider():
    assert select_provider({}) is None
    assert select_provider({"OPENAI_API_KEY": "  "}) is None


def test_the_default_model_is_gpt_5_6_luna():
    assert DEFAULT_MODEL == "gpt-5.6-luna"
    assert select_provider({"OPENAI_API_KEY": "sk-test"}).model == "gpt-5.6-luna"
    assert select_provider({"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "gpt-4.1"}).model == "gpt-4.1"


def test_rule_version_records_the_model_and_the_prompts(app_engine):
    version = llm_version(FakeProvider())
    assert version == f"llm-fake-{prompts_version()}" and len(prompts_version()) == 12
    assert build_state_manager(app_engine, version).rule_version.endswith(f"+{version}")
    assert "+llm-" not in build_state_manager(app_engine).rule_version
