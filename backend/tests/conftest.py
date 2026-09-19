"""Shared fixtures. Database tests run inside Docker against the hospital_test database:

    docker compose run --rm backend pytest
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from hospital_agent.state_manager import StateManager
from tests.fakes import AllowAllMonitor, fake_ports

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} is not set - run the tests in Docker: docker compose run --rm backend pytest", pytrace=False)
    return value


@pytest.fixture(scope="session")
def owner_engine() -> Engine:
    """The database owner (hospital_owner): runs migrations and cleans tables between tests."""
    engine = create_engine(_env("TEST_MIGRATION_DATABASE_URL"))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def migrated(owner_engine: Engine) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["url"] = _env("TEST_MIGRATION_DATABASE_URL")
    config.attributes["configure_logger"] = False
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.fixture
def app_engine(migrated: None, owner_engine: Engine) -> Engine:
    """A clean database, reached as hospital_app - the role the application uses."""
    with owner_engine.begin() as conn:
        conn.execute(text("TRUNCATE audit_log, approvals, executions, cases RESTART IDENTITY"))
    engine = create_engine(_env("TEST_DATABASE_URL"))
    yield engine
    engine.dispose()


@pytest.fixture
def sm(app_engine: Engine) -> StateManager:
    return StateManager(app_engine, AllowAllMonitor(), fake_ports())
