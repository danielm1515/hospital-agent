"""Migration 0008 (sub-project 19): the llm_usage table - its lock timeout (final review M1)."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0008_llm_usage.py"


class _FakeOp:
    """Records what upgrade() asks of alembic's `op`, for a given dialect, in order."""

    def __init__(self, dialect):
        self.dialect, self.steps = dialect, []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def execute(self, sql):
        self.steps.append(("execute", sql))

    def create_table(self, name, *columns):
        self.steps.append(("create_table", name))

    def create_index(self, name, table, columns):
        self.steps.append(("create_index", name))


@pytest.mark.parametrize("dialect, expected", [
    ("postgresql", [("execute", "SET LOCAL lock_timeout = '5s'")]),
    ("sqlite", []),
])
def test_0008_sets_a_lock_timeout_on_postgresql_only(monkeypatch, dialect, expected):
    """The lock timeout is the first thing upgrade() does on PostgreSQL - before the table and
    its foreign key to cases - and SQLite (no lock_timeout) is never sent it."""
    spec = importlib.util.spec_from_file_location("migration_0008", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fake = _FakeOp(dialect)
    monkeypatch.setattr(module, "op", fake)
    module.upgrade()
    assert fake.steps[:len(expected)] == expected
    assert fake.steps[len(expected)] == ("create_table", "llm_usage")
    assert ("execute", "GRANT SELECT, INSERT ON llm_usage TO hospital_app") in fake.steps
