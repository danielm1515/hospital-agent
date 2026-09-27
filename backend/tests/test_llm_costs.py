"""Sub-project 19, design D2-D4: llm_usage rows on Postgres - the recorder, the table's own
checks, and the Agent Orchestrator recording every attempt against its case."""
import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from hospital_agent import repository
from hospital_agent.case import new_case
from hospital_agent.db import llm_usage
from hospital_agent.execution.gateway import MockGateway
from hospital_agent.llm import pricing
from hospital_agent.llm.orchestrator import Orchestrator
from hospital_agent.llm.provider import FakeProvider
from hospital_agent.llm.schemas import Call, LLMUnusable
from hospital_agent.llm.usage import LLMUsage
from hospital_agent.llm_costs import UsageRecorder
from hospital_agent.naming import State
from hospital_agent.scripted import ScriptedAgents
from obs.golden import SCENARIOS

from .fakes import UsageFakeProvider

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
BILLED = LLMUsage(1200, 200, 30)
SECRET = "very private patient text"


def _case(app_engine, case_id: str = "CASE-1") -> str:
    with app_engine.begin() as conn:
        repository.insert_case(conn, new_case(case_id, "P-1", NOW))
    return case_id


def _rows(engine) -> list[dict]:
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(llm_usage).order_by(llm_usage.c.usage_id)).mappings()]


def _recorder(app_engine) -> UsageRecorder:
    return UsageRecorder(app_engine, prices=pricing.PRICES, clock=lambda: NOW)


# --- the recorder ------------------------------------------------------------------------------

def test_the_recorder_prices_and_stores_one_attempt(app_engine):
    _recorder(app_engine).record(_case(app_engine), "agent", "Intent", "gpt-5.6-luna", "ok", BILLED)
    [row] = _rows(app_engine)
    assert {key: value for key, value in row.items() if key != "usage_id"} == {
        "case_id": "CASE-1", "source": "agent", "call": "Intent", "model": "gpt-5.6-luna", "outcome": "ok",
        "input_tokens": 1200, "cached_input_tokens": 200, "output_tokens": 30,
        "price_input_per_mtok": Decimal("0.20"), "price_output_per_mtok": Decimal("1.20"),
        "cost_usd": Decimal("0.00027600"), "created_at": NOW,
    }


def test_an_api_error_row_has_no_tokens_and_no_cost(app_engine):
    _recorder(app_engine).record(_case(app_engine), "agent", "Safety", "gpt-5.6-luna", "api:APITimeoutError", None)
    [row] = _rows(app_engine)
    assert (row["outcome"], row["input_tokens"], row["cached_input_tokens"], row["output_tokens"], row["cost_usd"]) \
        == ("api:APITimeoutError", None, None, None, None)
    assert (row["price_input_per_mtok"], row["price_output_per_mtok"]) == (Decimal("0.20"), Decimal("1.20"))


def test_an_unknown_model_row_is_unpriced(app_engine):
    _recorder(app_engine).record(_case(app_engine), "agent", "Planner", "fake", "ok", BILLED)
    [row] = _rows(app_engine)
    assert row["input_tokens"] == 1200
    assert (row["price_input_per_mtok"], row["price_output_per_mtok"], row["cost_usd"]) == (None, None, None)


def test_the_recorder_reads_the_price_override_once(app_engine, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("LLM_PRICE_INPUT_PER_MTOK", "1")
    monkeypatch.setenv("LLM_PRICE_OUTPUT_PER_MTOK", "2")
    recorder = UsageRecorder(app_engine)
    monkeypatch.setenv("LLM_PRICE_INPUT_PER_MTOK", "9")  # read at construction, not per call
    recorder.record(_case(app_engine), "agent", "Intent", "gpt-5.6-luna", "ok", LLMUsage(1_000_000, 0, 1_000_000))
    [row] = _rows(app_engine)
    assert (row["price_input_per_mtok"], row["price_output_per_mtok"], row["cost_usd"]) == (1, 2, 3)


@pytest.mark.parametrize("case_id, source, call", [
    ("CASE-MISSING", "agent", "Intent"),   # no such case: the foreign key refuses it
    ("CASE-1", "somewhere", "Intent"),     # ck_llm_usage_source
    ("CASE-1", "agent", "Translate"),      # ck_llm_usage_call
])
def test_the_recorder_never_raises_and_logs_a_code(app_engine, caplog, case_id, source, call):
    _case(app_engine)
    with caplog.at_level(logging.WARNING, logger="hospital_agent.llm_costs"):
        _recorder(app_engine).record(case_id, source, call, "gpt-5.6-luna", "ok", BILLED)
    assert _rows(app_engine) == []
    assert [record.message for record in caplog.records] == ["llm_usage_write_failed error=IntegrityError"]


def test_the_recorder_never_raises_when_the_database_is_unreachable(app_engine, monkeypatch, caplog):
    recorder = _recorder(app_engine)

    def unreachable():
        raise RuntimeError(SECRET)

    monkeypatch.setattr(recorder.engine, "begin", unreachable)
    with caplog.at_level(logging.WARNING, logger="hospital_agent.llm_costs"):
        recorder.record("CASE-1", "agent", "Intent", "gpt-5.6-luna", "ok", BILLED)
    assert [record.message for record in caplog.records] == ["llm_usage_write_failed error=RuntimeError"]
    assert SECRET not in caplog.text


# --- the table's own checks (the owner role, so only the constraints stand in the way) ---------

@pytest.mark.parametrize("values, constraint", [
    ({"input_tokens": 10, "cached_input_tokens": 11, "output_tokens": 1}, "ck_llm_usage_cached_within_input"),
    ({"input_tokens": -1, "cached_input_tokens": -1, "output_tokens": 1}, "ck_llm_usage_non_negative"),
    ({"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": -1}, "ck_llm_usage_non_negative"),
    ({"input_tokens": 10, "cached_input_tokens": None, "output_tokens": 1}, "ck_llm_usage_tokens_together"),
    ({"cost_usd": Decimal("-0.1")}, "ck_llm_usage_non_negative"),
    ({"price_input_per_mtok": Decimal("-1")}, "ck_llm_usage_non_negative"),
])
def test_the_database_refuses_an_inconsistent_row(app_engine, owner_engine, values, constraint):
    _case(app_engine)
    with pytest.raises(IntegrityError, match=constraint):
        with owner_engine.begin() as conn:
            conn.execute(insert(llm_usage).values(case_id="CASE-1", source="agent", call="Intent", model="m",
                                                  outcome="ok", created_at=NOW, **values))


def test_the_database_accepts_the_document_service_calls(app_engine):
    case_id = _case(app_engine)
    for call in ("DocumentClassify", "DocumentVision"):
        _recorder(app_engine).record(case_id, "document_service", call, "gpt-5.6-luna", "ok", BILLED)
    assert [row["call"] for row in _rows(app_engine)] == ["DocumentClassify", "DocumentVision"]


# --- the Agent Orchestrator records every attempt against its case -----------------------------

@pytest.fixture
def agent(sm, app_engine):
    made = []

    def make(provider=None, recorder=None):
        made.append(Orchestrator(sm, provider or FakeProvider(), MockGateway(), recorder=recorder))
        return ScriptedAgents(sm, app_engine), made[-1]

    yield make
    for orchestrator in made:
        orchestrator.close()


def test_a_fakeprovider_run_records_every_call_against_its_case_with_no_cost(agent, app_engine):
    patient, orchestrator = agent(recorder=UsageRecorder(app_engine))
    patient.submit()
    patient.validate()
    assert orchestrator.run_case(patient.case_id) is State.AWAITING_PATIENT_INPUT
    patient.upload("blood_test")
    assert orchestrator.run_case(patient.case_id) is State.COMPLETED
    rows = _rows(app_engine)
    assert {row["call"] for row in rows} == {"Intent", "Safety", "Planner", "Evaluator"}
    assert {(row["case_id"], row["source"], row["model"], row["outcome"]) for row in rows} == {
        (patient.case_id, "agent", "fake", "ok")}
    assert all(row[column] is None for row in rows for column in (
        "input_tokens", "cached_input_tokens", "output_tokens",
        "price_input_per_mtok", "price_output_per_mtok", "cost_usd"))
    # one row per attempt: two classifications (the upload re-classifies, T10), each Intent + Safety
    assert sum(row["call"] == "Intent" for row in rows) == 2
    assert sum(row["call"] == "Evaluator" for row in rows) == 1
    assert len(patient.trace()) == 35  # golden scenario 1's count: usage rows add no audit row


def test_failed_attempts_are_recorded_with_their_usage(agent, app_engine):
    provider = UsageFakeProvider(BILLED, {Call.INTENT: [LLMUnusable("schema_violation", usage=BILLED)] * 3})
    patient, orchestrator = agent(provider=provider, recorder=UsageRecorder(app_engine, prices={}))
    patient.submit()
    patient.validate()
    orchestrator.run_case(patient.case_id)
    assert patient.case.escalation_kind == "ClassificationFailed"
    intents = [(row["outcome"], row["input_tokens"]) for row in _rows(app_engine) if row["call"] == "Intent"]
    assert intents == [("schema_violation", 1200)] * 3


def test_two_cases_are_attributed_to_their_own_case(agent, app_engine):
    patient_a, orchestrator = agent(recorder=UsageRecorder(app_engine))
    patient_b = ScriptedAgents(orchestrator.sm, app_engine, patient_id="P-20000")
    for patient in (patient_a, patient_b):
        patient.submit()
        patient.validate()
    orchestrator.tick()
    by_case = {}
    for row in _rows(app_engine):
        by_case.setdefault(row["case_id"], set()).add(row["call"])
    assert set(by_case) == {patient_a.case_id, patient_b.case_id}
    assert all({"Intent", "Safety"} <= calls for calls in by_case.values())


def test_without_a_recorder_nothing_is_recorded(agent, app_engine):
    patient, orchestrator = agent()
    patient.submit()
    patient.validate()
    orchestrator.run_case(patient.case_id)
    assert _rows(app_engine) == []


def test_a_failing_usage_write_never_changes_the_case(agent, app_engine, caplog):
    recorder = UsageRecorder(app_engine)

    def broken():
        raise RuntimeError(SECRET)

    recorder.engine = type("Broken", (), {"begin": staticmethod(broken)})()
    patient, orchestrator = agent(recorder=recorder)
    patient.submit()
    patient.validate()
    with caplog.at_level(logging.INFO, logger="hospital_agent"):
        assert orchestrator.run_case(patient.case_id) is State.AWAITING_PATIENT_INPUT
    assert _rows(app_engine) == []
    assert "llm_usage_write_failed error=RuntimeError" in caplog.text
    assert SECRET not in caplog.text


# --- the §0 golden scenarios' call counts (docs/llm-costs.md's estimate, sub-project 19 Task 5) --

def _call_kind(call: Call, user_input: dict) -> str:
    """The measured kind (design D9) an in-process attempt belongs to, from what it was sent."""
    if call is Call.PLANNER:
        return f"Planner {user_input['task']}"
    if "content" in user_input:  # the Tool Executor's re-check of retrieved content (§3)
        return "Safety instructions re-check"
    return f"{call.value.capitalize()} {'with document' if user_input['documents'] else 'request'}"


@pytest.mark.parametrize("number, expected", [
    (1, {"Intent request": 1, "Safety request": 1, "Intent with document": 1, "Safety with document": 1,
         "Safety instructions re-check": 1, "Planner plan": 1, "Planner propose": 4, "Evaluator": 1}),
    (2, {"Intent request": 1, "Safety request": 1}),
    (3, {"Intent request": 1, "Safety request": 1, "Intent with document": 1, "Safety with document": 1,
         "Safety instructions re-check": 1, "Planner plan": 1, "Planner propose": 7, "Evaluator": 1}),
])
def test_the_golden_scenarios_llm_calls_per_kind(sm, app_engine, number, expected):
    """Every llm_usage row of each §0 scenario, by the kind docs/llm-costs.md prices it as."""
    _title, gateway, play = SCENARIOS[number]
    provider = FakeProvider()
    patient = ScriptedAgents(sm, app_engine)
    orchestrator = Orchestrator(sm, provider, gateway(), recorder=UsageRecorder(app_engine))
    try:
        play(patient, orchestrator)
    finally:
        orchestrator.close()
    rows = _rows(app_engine)
    assert {(row["case_id"], row["outcome"]) for row in rows} == {(patient.case_id, "ok")}
    kinds: dict[str, int] = {}
    for call, user_input in provider.calls:  # the Evaluator runs in its own process: counted from its rows
        kind = _call_kind(call, user_input)
        kinds[kind] = kinds.get(kind, 0) + 1
    evaluations = sum(row["call"] == "Evaluator" for row in rows)
    if evaluations:
        kinds["Evaluator"] = evaluations
    assert kinds == expected
    # the split accounts for every row, and every row for one attempt
    by_call = {call: sum(row["call"] == call for row in rows) for call in ("Intent", "Safety", "Planner", "Evaluator")}
    assert by_call == {call: sum(n for kind, n in expected.items() if kind.startswith(call)) for call in by_call}
    assert len(rows) == sum(expected.values())
    assert (patient.state, len(patient.trace())) == (State.COMPLETED, {1: 35, 2: 4, 3: 54}[number])  # the golden run
