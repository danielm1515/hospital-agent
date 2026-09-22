"""Sub-project 9: the patients registry other systems read (design §3)."""
import os

import pytest
from alembic import command
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from hospital_agent.auth import DEMO_USERS, PATIENT

READER = "hospital_reader"
OTHER_TABLES = ["cases", "audit_log", "data_log", "approvals", "executions"]


def reader_password() -> str:
    return os.environ.get("READER_DB_PASSWORD", "hospital_reader_dev")


def engine_as(role: str, password: str):
    url = make_url(os.environ["TEST_DATABASE_URL"]).set(username=role, password=password)
    return create_engine(url)


def _restore_reader_role(owner_engine, alembic_config) -> None:
    """Always bring the schema back to head first - upgrade is a no-op when already there,
    and recovers the schema if a migration the caller ran inside its `try` raised partway
    through, which would otherwise leave the session-scoped `migrated` fixture's database
    stuck below head for the rest of the run - before resetting hospital_reader's password to
    the real one. Shared by every test that makes migration 0004 (re-)create the role."""
    command.upgrade(alembic_config, "head")
    # Not inside a dollar-quoted block (unlike the old migration code this guards against), so
    # doubling quotes is sufficient here - still done, since this only ever restores the real
    # env-provided password, never an attacker-chosen one.
    safe_password = reader_password().replace("'", "''")
    with owner_engine.begin() as conn:
        conn.execute(text(f"ALTER ROLE {READER} PASSWORD '{safe_password}'"))


@pytest.fixture
def reader(migrated):
    engine = engine_as(READER, reader_password())
    yield engine
    engine.dispose()


def test_the_registry_holds_exactly_the_idps_patients(app_engine):
    """DEMO_USERS stays the IdP (§18.3); the table must never drift from it (design §3.3)."""
    with app_engine.connect() as conn:
        rows = {(r.patient_id, r.full_name) for r in conn.execute(text("SELECT patient_id, full_name FROM patients"))}
    expected = {(u.user_id, u.display_name) for u in DEMO_USERS.values() if u.role == PATIENT}
    assert rows == expected


def test_every_phone_is_e164(app_engine):
    with app_engine.connect() as conn:
        phones = [r.phone for r in conn.execute(text("SELECT phone FROM patients"))]
    assert phones and all(p.startswith("+") and p[1:].isdigit() and 8 <= len(p) - 1 <= 15 for p in phones)


def test_the_database_rejects_a_phone_that_is_not_e164(owner_engine, migrated):
    with pytest.raises(IntegrityError, match="ck_patients_phone_e164"):
        with owner_engine.begin() as conn:
            conn.execute(text("INSERT INTO patients (patient_id, full_name, phone) "
                              "VALUES ('P-BADPHONE', 'בדיקה', '050-1234567')"))


def test_the_reader_can_read_the_patients(reader):
    with reader.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3


@pytest.mark.parametrize("table", OTHER_TABLES)
def test_the_reader_cannot_read_any_other_table(reader, table):
    with pytest.raises(ProgrammingError, match="permission denied"):
        with reader.connect() as conn:
            conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))


@pytest.mark.parametrize("statement", [
    "INSERT INTO patients (patient_id, full_name, phone) VALUES ('P-X', 'x', '+972500000099')",
    "UPDATE patients SET full_name = 'x'",
    "DELETE FROM patients",
    "TRUNCATE patients",
])
def test_the_reader_cannot_change_the_patients(reader, statement):
    with pytest.raises(ProgrammingError, match="permission denied"):
        with reader.begin() as conn:
            conn.execute(text(statement))


def test_the_app_role_reads_the_patients_but_cannot_write_them(app_engine):
    with app_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3
    with pytest.raises(ProgrammingError, match="permission denied"):
        with app_engine.begin() as conn:
            conn.execute(text("UPDATE patients SET full_name = 'x'"))


def test_rerunning_the_migration_keeps_an_existing_roles_password(owner_engine, migrated, alembic_config):
    """Roles are cluster-wide: 0004 must not fail on an existing role, nor reset a password
    the owner of the database changed (design §3.2)."""
    changed = "changed-by-the-owner"
    with owner_engine.begin() as conn:
        conn.execute(text(f"ALTER ROLE {READER} PASSWORD '{changed}'"))
    try:
        command.downgrade(alembic_config, "0003")
        command.upgrade(alembic_config, "head")
        engine = engine_as(READER, changed)
        try:
            with engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3
        finally:
            engine.dispose()
    finally:
        _restore_reader_role(owner_engine, alembic_config)


def test_the_migration_quotes_a_tricky_password_safely(owner_engine, migrated, alembic_config, monkeypatch):
    """0004 used to build the CREATE ROLE statement as an f-string inside a DO $body$ ... $body$
    block, with only single quotes escaped. A password containing the substring '$body$' would
    end that dollar-quoted block early, so whatever followed it would run as arbitrary SQL under
    hospital_owner, a superuser (design §3.2 correction). The fix builds the CREATE ROLE
    statement through psycopg's own SQL composition instead of string interpolation, so this
    proves a password containing both a quote and a '$body$' tag round-trips as exactly one
    password, with nothing else executed alongside it."""
    tricky = "o'brien's $body$ password; DROP TABLE patients; --"
    with owner_engine.begin() as conn:
        # The role holds a GRANT on patients; Postgres refuses to drop a role that still has
        # privileges anywhere in the cluster, so those are dropped first (this cluster's
        # `hospital` database, unlike `hospital_test`, is never migrated by these tests, so
        # hospital_test is the only database that can hold such a privilege here).
        conn.execute(text(f"DROP OWNED BY {READER}"))
        conn.execute(text(f"DROP ROLE {READER}"))
    monkeypatch.setenv("READER_DB_PASSWORD", tricky)
    try:
        command.downgrade(alembic_config, "0003")
        command.upgrade(alembic_config, "head")
        engine = engine_as(READER, tricky)
        try:
            with engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3
        finally:
            engine.dispose()
        # Nothing past the would-be broken-out point ran: the table a real exploit would have
        # tried to drop is still there, with all three seeded rows intact.
        with owner_engine.begin() as conn:
            assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3
    finally:
        # Undo the monkeypatch explicitly (safe to call twice; the fixture would otherwise do
        # it only after this function returns) so _restore_reader_role's own naive ALTER ROLE
        # string-building sees the real default password, not the tricky one still active here.
        monkeypatch.undo()
        _restore_reader_role(owner_engine, alembic_config)
