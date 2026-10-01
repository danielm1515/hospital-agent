"""Shared fixtures. Database tests run inside Docker against the hospital_test database:

    docker compose run --rm backend pytest
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from hospital_agent import instruction_registry, logging_setup
from hospital_agent.llm import telemetry
from hospital_agent.state_manager import StateManager
from hospital_agent.wiring import build_state_manager

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} is not set - run the tests in Docker: docker compose run --rm backend pytest", pytrace=False)
    return value


@pytest.fixture(autouse=True)
def _no_real_appointment_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """`docker compose run backend pytest` inherits APPOINTMENT_SERVICE_URL/APPOINTMENT_API_KEY
    and DOCUMENT_SERVICE_URL/DOCUMENT_API_KEY from the repo's .env (docker-compose.yml passes
    them through, defaulting to empty) - the same way it inherits DATABASE_URL. Unlike
    DATABASE_URL, a test must never pick these four up: build_gateway() (appointment_service.py)
    and build_document_gateway() (document_service.py) would then build a live gateway pointed
    at the owner's real appointment-service / document-service instead of the mock, on every
    test that builds an app or a State Manager. This fixture clears all four before each test;
    a test that wants a real gateway sets them itself (e.g.
    test_build_gateway_reads_the_environment_by_default, the build_gateway tests in
    test_app_orchestrator.py)."""
    monkeypatch.delenv("APPOINTMENT_SERVICE_URL", raising=False)
    monkeypatch.delenv("APPOINTMENT_API_KEY", raising=False)
    monkeypatch.delenv("DOCUMENT_SERVICE_URL", raising=False)
    monkeypatch.delenv("DOCUMENT_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _fresh_instruction_memo():
    """`instruction_registry` keeps a process-wide memo of OPA's answers (final review M5) -
    clear it before and after every test, so one test's answer (or its patched DATA_DIR,
    PATH or subprocess) never decides another's."""
    instruction_registry.clear_cache()
    yield
    instruction_registry.clear_cache()


@pytest.fixture(autouse=True)
def _fresh_llm_telemetry() -> None:
    """`llm/telemetry.py` keeps the last outcome in a process-wide global (staff-fixes design
    Task 1, decision 3) - reset it before every test so one test's LLM calls never leak into
    another's `telemetry.status()` assertion."""
    telemetry.reset()


@pytest.fixture(autouse=True)
def _clean_hospital_agent_logger():
    """`logging_setup.configure()` (api/app.py's owned-app path) mutates the `hospital_agent`
    logger's handlers/level/propagate/marker as a real, process-wide side effect - a few tests
    (test_app_orchestrator.py) exercise a real owned `create_app()` and so call it for real.
    Save this logger's state before every test and restore it after, so one test's real
    startup never changes what a later test's `caplog` or configure()-marker check sees
    (fix round 1, I1: without this, `configure()`'s `propagate=False` and extra handler used
    to leak into every test that ran afterwards)."""
    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    orig_handlers = list(logger.handlers)
    orig_level = logger.level
    orig_propagate = logger.propagate
    orig_configured = getattr(logger, "_hospital_agent_configured", False)
    yield
    logger.handlers = orig_handlers
    logger.setLevel(orig_level)
    logger.propagate = orig_propagate
    if orig_configured:
        logger._hospital_agent_configured = True  # type: ignore[attr-defined]
    elif hasattr(logger, "_hospital_agent_configured"):
        del logger._hospital_agent_configured


@pytest.fixture(scope="session")
def owner_engine() -> Engine:
    """The database owner (hospital_owner): runs migrations and cleans tables between tests."""
    engine = create_engine(_env("TEST_MIGRATION_DATABASE_URL"))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def managed_postgres(owner_engine: Engine) -> bool:
    """True when the migrating role is not a real superuser - a managed Postgres such as AWS
    RDS, whose master user is rds_superuser. There, a few things a superuser can do are
    impossible (pg_catalog functions belong to rdsadmin; nobody may create a SUPERUSER), and
    the tests that depend on them say so instead of failing or passing by accident."""
    with owner_engine.connect() as conn:
        return not conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar()


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["url"] = _env("TEST_MIGRATION_DATABASE_URL")
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def migrated(owner_engine: Engine, alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


@pytest.fixture(autouse=True)
def _fresh_document_status():
    """Row 98: the document-service's last outcome is process memory - no test inherits another's."""
    from hospital_agent import document_status
    document_status.reset()
    yield
    document_status.reset()


@pytest.fixture
def app_engine(migrated: None, owner_engine: Engine) -> Engine:
    """A clean database, reached as hospital_app - the role the application uses."""
    with owner_engine.begin() as conn:
        conn.execute(text("TRUNCATE upload_attempts, llm_usage, data_log, audit_log, approvals, executions, cases RESTART IDENTITY"))
    engine = create_engine(_env("TEST_DATABASE_URL"))
    yield engine
    engine.dispose()


@pytest.fixture
def sm(app_engine: Engine) -> StateManager:
    """The production State Manager: real Temporal Monitor and real ExecutorReverified."""
    return build_state_manager(app_engine)
