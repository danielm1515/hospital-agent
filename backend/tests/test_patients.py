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
        with owner_engine.begin() as conn:
            conn.execute(text(f"ALTER ROLE {READER} PASSWORD '{reader_password()}'"))
