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


def test_record_never_logs_request_content(caplog):
    """§12.3: the log line names the call, the model, the timing and the outcome code only."""
    with caplog.at_level(logging.INFO, logger="hospital_agent.llm"):
        telemetry.record(Call.EVALUATOR, "gpt-5.6-luna", 1, "ok")
    assert all("patient" not in record.message.lower() for record in caplog.records)
