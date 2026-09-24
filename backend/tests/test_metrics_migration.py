"""Migration 0005 (sub-project 14, design §6): the metrics' window indexes, and a clean downgrade."""
from alembic import command
from sqlalchemy import text

INDEXES = {"ix_audit_log_recorded_at", "ix_cases_created_at", "ix_executions_started_at"}


def _indexes(owner_engine) -> set[str]:
    with owner_engine.connect() as conn:
        return set(conn.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")).scalars())


def test_0005_adds_the_three_window_indexes(migrated, owner_engine):
    assert INDEXES <= _indexes(owner_engine)


def test_0005_downgrades_cleanly_and_upgrades_again(migrated, owner_engine, alembic_config):
    try:
        command.downgrade(alembic_config, "0004")
        assert not INDEXES & _indexes(owner_engine)
    finally:
        command.upgrade(alembic_config, "head")
    assert INDEXES <= _indexes(owner_engine)
