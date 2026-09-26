"""Migration 0007 (sub-project 18 task 3): seven nullable cases columns."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from sqlalchemy import text

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0007_appointment_choice.py"

COLUMNS = {"appointment_id", "answered_appointment_id", "department", "exam_type_label",
          "instruction_source_id", "instruction_version", "upcoming_count"}


def _case_columns(owner_engine) -> set[str]:
    with owner_engine.connect() as conn:
        return set(conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'cases'")).scalars())


def test_0007_adds_the_columns(migrated, owner_engine):
    assert COLUMNS <= _case_columns(owner_engine)


def test_0007_downgrades_cleanly_and_upgrades_again(migrated, owner_engine, alembic_config):
    try:
        command.downgrade(alembic_config, "0006")
        assert not COLUMNS & _case_columns(owner_engine)
    finally:
        command.upgrade(alembic_config, "head")
    assert COLUMNS <= _case_columns(owner_engine)


class _FakeOp:
    """Records what upgrade() asks of alembic's `op`, for a given dialect."""

    def __init__(self, dialect):
        self.dialect, self.executed, self.added = dialect, [], []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def execute(self, sql):
        self.executed.append(sql)

    def add_column(self, table, column):
        self.added.append((table, column.name))


@pytest.mark.parametrize("dialect, expected", [
    ("postgresql", ["SET LOCAL lock_timeout = '5s'"]),
    ("sqlite", []),
])
def test_0007_sets_a_lock_timeout_on_postgresql_only(monkeypatch, dialect, expected):
    """Final review M7: the lock timeout is the first thing upgrade() does on PostgreSQL, and
    SQLite (no lock_timeout) is never sent it."""
    spec = importlib.util.spec_from_file_location("migration_0007", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fake = _FakeOp(dialect)
    monkeypatch.setattr(module, "op", fake)
    module.upgrade()
    assert fake.executed == expected
    assert {name for _, name in fake.added} == COLUMNS

