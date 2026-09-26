"""Migration 0007 (sub-project 18 task 3): seven nullable cases columns."""
from alembic import command
from sqlalchemy import text

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
