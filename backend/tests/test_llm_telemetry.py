"""llm/telemetry.py: one log line per LLM attempt, and the last outcome kept in memory
(staff-fixes design Task 1, decisions 1 and 3). No network, no database."""
import logging
import re

import pytest

from hospital_agent.llm import telemetry
from hospital_agent.llm.schemas import Call

ISO_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")


def test_status_starts_empty():
    assert telemetry.status() == {"last_ok_at": None, "last_error": None, "last_error_at": None}


def test_record_ok_sets_last_ok_at_only():
    telemetry.record(Call.INTENT, "gpt-5.6-luna", 42, "ok")
    status = telemetry.status()
    assert ISO_INSTANT.match(status["last_ok_at"])
    assert status["last_error"] is None
    assert status["last_error_at"] is None


def test_record_an_error_sets_last_error_and_its_time():
    telemetry.record(Call.SAFETY, "gpt-5.6-luna", 7, "api:AuthenticationError")
    status = telemetry.status()
    assert status["last_error"] == "api:AuthenticationError"
    assert ISO_INSTANT.match(status["last_error_at"])
    assert status["last_ok_at"] is None


def test_an_ok_after_an_error_keeps_the_error_visible():
    """The last error and the last success are two independent facts - a later success does
    not erase the fact that a call failed earlier (design decision 3)."""
    telemetry.record(Call.INTENT, "gpt-5.6-luna", 5, "unparsable")
    telemetry.record(Call.INTENT, "gpt-5.6-luna", 5, "ok")
    status = telemetry.status()
    assert status["last_error"] == "unparsable" and status["last_ok_at"] is not None


def test_summary_is_unknown_before_any_call():
    assert telemetry.summary() == "unknown"


def test_summary_is_ok_after_a_success():
    telemetry.record(Call.INTENT, "gpt-5.6-luna", 5, "ok")
    assert telemetry.summary() == "ok"


def test_summary_is_error_after_a_failure_with_no_prior_success():
    telemetry.record(Call.INTENT, "gpt-5.6-luna", 5, "api:AuthenticationError")
    assert telemetry.summary() == "error"


def test_summary_is_error_when_the_last_error_is_newer_than_the_last_success():
    telemetry.record(Call.INTENT, "gpt-5.6-luna", 5, "ok")
    telemetry.record(Call.INTENT, "gpt-5.6-luna", 5, "api:AuthenticationError")
    assert telemetry.summary() == "error"


def test_summary_is_ok_when_a_later_success_follows_an_error():
    telemetry.record(Call.INTENT, "gpt-5.6-luna", 5, "api:AuthenticationError")
    telemetry.record(Call.INTENT, "gpt-5.6-luna", 5, "ok")
    assert telemetry.summary() == "ok"


def test_reset_clears_everything():
    telemetry.record(Call.INTENT, "gpt-5.6-luna", 5, "ok")
    telemetry.reset()
    assert telemetry.status() == {"last_ok_at": None, "last_error": None, "last_error_at": None}


@pytest.mark.parametrize("outcome, level", [("ok", logging.INFO), ("schema_violation", logging.WARNING),
                                            ("api:AuthenticationError", logging.WARNING)])
def test_record_logs_one_line_in_the_documented_format(caplog, outcome, level):
    with caplog.at_level(logging.INFO, logger="hospital_agent.llm"):
        telemetry.record(Call.PLANNER, "gpt-5.6-luna", 123, outcome)
    [record] = caplog.records
    assert record.levelno == level
    assert record.name == "hospital_agent.llm"
    assert record.message == f"llm call=planner model=gpt-5.6-luna ms=123 outcome={outcome}"

    # Fix round 1, M7: `record()`'s own signature (call, model, ms, outcome) structurally
    # cannot carry request content - there is no parameter for it - so a test that only calls
    # record() directly with a fixed message can never exercise a real leak path. The
    # meaningful version of this check passes an actual sentinel through the real path that
    # *could* leak it end to end: test_llm_provider.py's test_ask_logs_and_records_every_attempt,
    # which sends "very private patient text" through ask()'s user_input and asserts it never
    # reaches a log line.
