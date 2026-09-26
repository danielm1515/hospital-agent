"""hospital_agent/logging_setup.py: configures `hospital_agent`'s own loggers once, for a
real server (staff-fixes design Task 1 follow-up). Never called for a test's own
`create_app(app_engine)` - see test_app_orchestrator.py, whose two owned-mode tests exercise
`create_app()` for real and therefore call `configure()` for real too; this file checks that
pytest's `caplog` still works afterwards (it does: pytest attaches its capturing handler
directly to any non-propagating logger, precisely for this case - `_pytest/logging.py`'s
`catching_logs`)."""
from __future__ import annotations

import logging

import pytest

from hospital_agent import logging_setup


@pytest.fixture
def clean_logger():
    """Saves and restores `hospital_agent`'s logger state so this file's own configure()
    calls never leak into another test - regardless of what an earlier test already did."""
    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    orig_handlers = list(logger.handlers)
    orig_level = logger.level
    orig_propagate = logger.propagate
    orig_configured = getattr(logger, "_hospital_agent_configured", False)
    logger.handlers = []
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    if hasattr(logger, "_hospital_agent_configured"):
        del logger._hospital_agent_configured
    yield logger
    logger.handlers = orig_handlers
    logger.setLevel(orig_level)
    logger.propagate = orig_propagate
    logger._hospital_agent_configured = orig_configured  # type: ignore[attr-defined]


def test_configure_sets_info_level_one_stderr_handler_and_no_propagation(clean_logger):
    logging_setup.configure()
    assert clean_logger.level == logging.INFO
    assert clean_logger.propagate is False
    assert len(clean_logger.handlers) == 1
    [handler] = clean_logger.handlers
    assert isinstance(handler, logging.StreamHandler)


def test_configuring_twice_leaves_one_handler(clean_logger):
    logging_setup.configure()
    logging_setup.configure()
    logging_setup.configure()
    assert len(clean_logger.handlers) == 1


def test_info_from_a_child_logger_is_emitted_in_the_documented_format(clean_logger, capsys):
    logging_setup.configure()
    logging.getLogger("hospital_agent.llm").info(
        "llm call=intent model=gpt-5.6-luna ms=42 outcome=ok")

    captured = capsys.readouterr()
    assert "hospital_agent.llm" in captured.err
    assert "INFO" in captured.err
    assert "llm call=intent model=gpt-5.6-luna ms=42 outcome=ok" in captured.err


def test_configure_never_runs_for_an_injected_test_server(clean_logger, app_engine):
    """The follow-up's core requirement: a test always injects an engine (create_app(app_engine)
    is "not owned"), so configure() must never fire from that path - caplog stays untouched.

    Fix round 1, M2: `clean_logger` guarantees the marker is False *before* this runs, so the
    assertion is not vacuously true just because an earlier test happened to configure it."""
    from fastapi.testclient import TestClient

    from hospital_agent.api.app import create_app

    assert getattr(clean_logger, "_hospital_agent_configured", False) is False

    with TestClient(create_app(app_engine)):
        pass

    assert getattr(clean_logger, "_hospital_agent_configured", False) is False
