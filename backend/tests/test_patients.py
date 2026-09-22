"""Sub-project 9: the patients registry other systems read (design §3)."""
import os
import uuid

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


THROWAWAY_PREFIX = "hospital_reader_t_"


@pytest.fixture
def throwaway_reader(owner_engine, migrated, alembic_config):
    """A throwaway reader role for the tests that make migration 0004 (re-)create its role.

    Roles are cluster-wide, and on the owner's stack hospital (migrated on every backend start)
    and hospital_test share one cluster, so these tests never ALTER or DROP the real
    hospital_reader: 0004 reads the role's name from the Alembic config, and this fixture points
    it at a fresh role for the test's own downgrade/upgrade.

    Teardown runs even when the test body raised, and leaves hospital_test as the rest of the
    session expects it: the attribute restored, then 0003 -> head again with the default role
    (a plain `upgrade head` would be a no-op and leave patients granted only to the throwaway
    role), then the throwaway role dropped.
    """
    name = THROWAWAY_PREFIX + uuid.uuid4().hex[:8]
    missing = object()
    previous = alembic_config.attributes.get("reader_role", missing)
    alembic_config.attributes["reader_role"] = name
    try:
        yield name
    finally:
        if previous is missing:
            alembic_config.attributes.pop("reader_role", None)
        else:
            alembic_config.attributes["reader_role"] = previous
        try:
            # A migration run is one transaction (alembic/env.py), so a body that raised left the
            # database at 0003 or at head, never between: downgrading to 0003 is safe from both.
            command.downgrade(alembic_config, "0003")
            command.upgrade(alembic_config, "head")
        finally:
            with owner_engine.begin() as conn:
                if conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": name}).scalar():
                    conn.execute(text(f'DROP OWNED BY "{name}"'))
                    conn.execute(text(f'DROP ROLE "{name}"'))


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


def test_rerunning_the_migration_keeps_an_existing_roles_password(owner_engine, alembic_config, throwaway_reader):
    """Roles are cluster-wide: 0004 must not fail on an existing role, nor reset a password
    the owner of the database changed (design §3.2)."""
    command.downgrade(alembic_config, "0003")
    command.upgrade(alembic_config, "head")  # creates the throwaway role with the default password
    changed = "changed-by-the-owner"
    with owner_engine.begin() as conn:
        conn.execute(text(f"ALTER ROLE \"{throwaway_reader}\" PASSWORD '{changed}'"))
    command.downgrade(alembic_config, "0003")
    command.upgrade(alembic_config, "head")  # the role now exists: it must keep `changed`
    engine = engine_as(throwaway_reader, changed)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3
    finally:
        engine.dispose()


def test_the_migration_quotes_a_tricky_password_safely(owner_engine, alembic_config, throwaway_reader, monkeypatch):
    """0004 used to build the CREATE ROLE statement as an f-string inside a DO $body$ ... $body$
    block, with only single quotes escaped. A password containing the substring '$body$' would
    end that dollar-quoted block early, so whatever followed it would run as arbitrary SQL under
    hospital_owner, a superuser (design §3.2 correction). The fix builds the CREATE ROLE
    statement through psycopg's own SQL composition instead of string interpolation, so this
    proves a password containing both a quote and a '$body$' tag round-trips as exactly one
    password, with nothing else executed alongside it."""
    tricky = "o'brien's $body$ password; DROP TABLE patients; --"
    monkeypatch.setenv("READER_DB_PASSWORD", tricky)
    command.downgrade(alembic_config, "0003")
    command.upgrade(alembic_config, "head")  # creates the throwaway role with the tricky password
    engine = engine_as(throwaway_reader, tricky)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3
    finally:
        engine.dispose()
    # Nothing past the would-be broken-out point ran: the table a real exploit would have
    # tried to drop is still there, with all three seeded rows intact.
    with owner_engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3


def test_the_recreation_tests_leave_the_real_reader_untouched(owner_engine, reader):
    """Runs after the two tests above (file order): the real hospital_reader still reads
    patients in hospital_test with its own password, and no throwaway role is left behind."""
    with reader.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 3
    with owner_engine.connect() as conn:
        leftovers = conn.execute(
            text("SELECT rolname FROM pg_roles WHERE rolname LIKE :p"), {"p": THROWAWAY_PREFIX.replace("_", r"\_") + "%"}
        ).scalars().all()
    assert leftovers == []


def test_the_migration_refuses_an_existing_reader_that_is_a_superuser(owner_engine, alembic_config, throwaway_reader):
    with owner_engine.begin() as conn:
        conn.execute(text(f"CREATE ROLE \"{throwaway_reader}\" LOGIN SUPERUSER"))
    command.downgrade(alembic_config, "0003")
    with pytest.raises(RuntimeError, match="refuses to grant.*SUPERUSER"):
        command.upgrade(alembic_config, "head")


def test_the_migration_refuses_an_existing_reader_that_is_a_member_of_another_role(
        owner_engine, alembic_config, throwaway_reader):
    with owner_engine.begin() as conn:
        conn.execute(text(f"CREATE ROLE \"{throwaway_reader}\" LOGIN"))
        conn.execute(text(f"GRANT hospital_app TO \"{throwaway_reader}\""))
    command.downgrade(alembic_config, "0003")
    with pytest.raises(RuntimeError, match="refuses to grant.*member of hospital_app"):
        command.upgrade(alembic_config, "head")


def test_the_reader_has_a_connection_limit(owner_engine, migrated):
    with owner_engine.connect() as conn:
        limit = conn.execute(text("SELECT rolconnlimit FROM pg_roles WHERE rolname = :r"), {"r": READER}).scalar()
    assert limit == 5


@pytest.mark.parametrize("statement", [
    "SELECT lo_creat(-1)",
    "SELECT lo_create(0)",
    "SELECT lo_from_bytea(0, decode('00', 'hex'))",
    "SELECT lo_import('/etc/hostname')",
    "SELECT lo_export(1, '/tmp/x')",
    "CREATE TEMP TABLE reader_scratch (x int)",
    "CREATE TABLE public.reader_scratch (x int)",
])
def test_the_reader_cannot_store_anything(reader, statement):
    """No large objects, no temporary tables, no tables in public (design §3.2, final review I1)."""
    with pytest.raises(ProgrammingError, match="permission denied"):
        with reader.begin() as conn:
            conn.execute(text(statement))
