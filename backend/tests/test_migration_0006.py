"""Migration 0006 (sub-project 15, design §11): three cases columns and two widened CHECKs."""
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

COLUMNS = {"human_engaged", "reply_kind", "requested_document"}


def _case_columns(owner_engine) -> set[str]:
    with owner_engine.connect() as conn:
        return set(conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'cases'")).scalars())


def _check(owner_engine, name: str) -> str:
    with owner_engine.connect() as conn:
        return conn.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"), {"n": name}).scalar_one()


def test_0006_adds_the_columns_and_widens_both_checks(migrated, owner_engine):
    assert COLUMNS <= _case_columns(owner_engine)
    assert "'request'" in _check(owner_engine, "ck_approvals_decision")
    kinds = _check(owner_engine, "ck_data_log_kind")
    assert "'staff_message'" in kinds and "'patient_reply'" in kinds


def test_0006_downgrades_cleanly_and_upgrades_again(migrated, owner_engine, alembic_config):
    try:
        command.downgrade(alembic_config, "0005")
        assert not COLUMNS & _case_columns(owner_engine)
        assert "'request'" not in _check(owner_engine, "ck_approvals_decision")
    finally:
        command.upgrade(alembic_config, "head")
    assert COLUMNS <= _case_columns(owner_engine)


def test_an_unknown_data_kind_is_still_refused(app_engine):
    with pytest.raises(IntegrityError), app_engine.begin() as conn:
        conn.execute(text("INSERT INTO cases (case_id, patient_id, state, state_version, identity_verified,"
                          " retry_cycle, attempt_count, held_documents, created_at, updated_at)"
                          " VALUES ('C-1', 'P-10041', 'Received', 1, true, 0, 0, '[]', now(), now())"))
        conn.execute(text("INSERT INTO data_log (entry_id, case_id, patient_id, kind, content, content_hash,"
                          " created_at) VALUES ('D-1', 'C-1', 'P-10041', 'gossip', 'x', 'h', now())"))
