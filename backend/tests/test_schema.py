"""The migrated database must match db.py, and must keep the Audit append-only."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError

from hospital_agent import repository
from hospital_agent.case import new_case
from hospital_agent.db import metadata
from hospital_agent.repository import AuditEntry


def test_tables_and_columns_match_db_py(owner_engine, migrated):
    inspector = inspect(owner_engine)
    for table in metadata.sorted_tables:
        db_columns = {column["name"] for column in inspector.get_columns(table.name)}
        assert db_columns == set(table.columns.keys()), table.name


def _seed(app_engine) -> int:
    now = datetime.now(UTC)
    with app_engine.begin() as conn:
        repository.insert_case(conn, new_case("CASE-1", "P-1", now))
        return repository.insert_audit(conn, AuditEntry(
            case_id="CASE-1", patient_id="P-1", record_type="Transition", event="REQUEST_SUBMITTED",
            state_before=None, state_after="Received", rule_version="test", recorded_at=now,
        ))


def test_app_role_can_append_audit_rows(app_engine):
    audit_id = _seed(app_engine)
    with app_engine.connect() as conn:
        assert [e.audit_id for e in repository.load_trace(conn, "CASE-1")] == [audit_id]


@pytest.mark.parametrize("statement", [
    "UPDATE audit_log SET event = 'HUMAN_APPROVED'",
    "DELETE FROM audit_log",
    "TRUNCATE audit_log",
])
def test_app_role_cannot_change_or_delete_audit_rows(app_engine, statement):
    _seed(app_engine)
    with pytest.raises(ProgrammingError, match="permission denied"):
        with app_engine.begin() as conn:
            conn.execute(text(statement))


def test_optimistic_lock_rejects_a_stale_version(app_engine):
    now = datetime.now(UTC)
    case = new_case("CASE-1", "P-1", now)
    with app_engine.begin() as conn:
        repository.insert_case(conn, case)
        assert repository.update_case(conn, case, expected_version=1) == 1
        assert repository.update_case(conn, case, expected_version=0) == 0


# Sub-project 19 (design D4): llm_usage is append-only for the application, like audit_log.

def _seed_usage(app_engine) -> None:
    now = datetime.now(UTC)
    with app_engine.begin() as conn:
        repository.insert_case(conn, new_case("CASE-1", "P-1", now))
        conn.execute(text("INSERT INTO llm_usage (case_id, source, call, model, outcome, created_at) "
                          "VALUES ('CASE-1', 'agent', 'Intent', 'fake', 'ok', now())"))


def test_app_role_can_append_and_read_usage_rows(app_engine):
    _seed_usage(app_engine)
    with app_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM llm_usage")).scalar() == 1


@pytest.mark.parametrize("statement", [
    "UPDATE llm_usage SET cost_usd = 0",
    "DELETE FROM llm_usage",
    "TRUNCATE llm_usage",
])
def test_app_role_cannot_change_or_delete_usage_rows(app_engine, statement):
    _seed_usage(app_engine)
    with pytest.raises(ProgrammingError, match="permission denied"):
        with app_engine.begin() as conn:
            conn.execute(text(statement))


# --- row 98: upload_attempts is append-only for the app role, like llm_usage -----------------

def _seed_attempt(app_engine) -> None:
    now = datetime.now(UTC)
    with app_engine.begin() as conn:
        repository.insert_case(conn, new_case("CASE-1", "P-1", now))
        conn.execute(text("INSERT INTO upload_attempts (case_id, kind, outcome, reason, created_at) "
                          "VALUES ('CASE-1', 'upload', 'document_service_unavailable', 'no_answer', now())"))


def test_app_role_can_append_and_read_upload_attempts(app_engine):
    _seed_attempt(app_engine)
    with app_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM upload_attempts")).scalar() == 1


@pytest.mark.parametrize("statement", [
    "UPDATE upload_attempts SET outcome = 'accepted'",
    "DELETE FROM upload_attempts",
    "TRUNCATE upload_attempts",
])
def test_app_role_cannot_change_or_delete_upload_attempts(app_engine, statement):
    _seed_attempt(app_engine)
    with pytest.raises(ProgrammingError, match="permission denied"):
        with app_engine.begin() as conn:
            conn.execute(text(statement))
