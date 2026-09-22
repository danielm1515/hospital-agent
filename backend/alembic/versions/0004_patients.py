"""The patients registry (sub-project 9) - a table other systems read.

The §18.3 IdP stays the fixed user list in auth.py; this table is the registry the outside
world reads, and tests/test_patients.py fails if the two ever disagree. Names and phones are
personal data (§12.3): only hospital_reader, a role with SELECT on this one table, is given
to other systems.

db/init/01-init.sh runs only when the volume is first created, so the reader role is created
here, idempotently: roles are cluster-wide, and this migration runs on both hospital and
hospital_test. An existing role keeps its password, but is refused if it holds more than a
reader should (a superuser, a role with other privileged attributes, or a member of any other
role).

The reader is also confined inside the database being migrated: no large objects (persistent
storage of arbitrary data), no temporary tables, and at most five connections. Large-object
creation and TEMPORARY are revoked from PUBLIC, which hospital_app never uses either; the
residual surface (CONNECT to the cluster's other databases, catalog reads) is documented in
docs/patients-registry.md.

The role's name comes from the Alembic config's attributes ("reader_role"), defaulting to
hospital_reader. Only the tests set it, so they can re-create a throwaway role instead of
touching the real, cluster-wide one.

Revision ID: 0004
"""
import os

import sqlalchemy as sa
from alembic import op
from psycopg import sql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "hospital_app"
DEFAULT_READER_ROLE = "hospital_reader"
READER_CONNECTION_LIMIT = 5

# Revoked from PUBLIC in the migrated database. Server-side lo_import/lo_export are already
# superuser-only in Postgres 16 (tests/test_patients.py pins that the reader cannot call them).
LARGE_OBJECT_CREATORS = "lo_creat(integer), lo_create(oid), lo_from_bytea(oid, bytea)"

# The same three patients as auth.DEMO_USERS. The phones are placeholders: 0004 has already run
# on existing databases, so a real phone is set by a new migration, never by editing this list.
PATIENTS = [
    {"patient_id": "P-10041", "full_name": "דנה כהן", "phone": "+972500000001"},
    {"patient_id": "P-20000", "full_name": "יוסי לוי", "phone": "+972500000002"},
    {"patient_id": "P-30000", "full_name": "מיכל אברהם", "phone": "+972500000003"},
]


def _reader_role() -> str:
    return op.get_context().config.attributes.get("reader_role", DEFAULT_READER_ROLE)


def _quote(conn: sa.engine.Connection, identifier: str) -> str:
    return conn.dialect.identifier_preparer.quote(identifier)


def upgrade() -> None:
    patients = op.create_table(
        "patients",
        sa.Column("patient_id", sa.Text, primary_key=True),
        sa.Column("full_name", sa.Text, nullable=False),
        sa.Column("phone", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(r"phone ~ '^\+[1-9][0-9]{7,14}$'", name="ck_patients_phone_e164"),
    )
    op.bulk_insert(patients, PATIENTS)

    conn = op.get_bind()
    reader_role = _reader_role()
    _create_reader_role_if_absent(conn, reader_role)
    _refuse_a_privileged_reader(conn, reader_role)
    reader = _quote(conn, reader_role)
    database = _quote(conn, conn.execute(sa.text("SELECT current_database()")).scalar_one())

    op.execute(f"ALTER ROLE {reader} CONNECTION LIMIT {READER_CONNECTION_LIMIT}")
    op.execute(f"GRANT SELECT ON patients TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON patients TO {reader}")
    op.execute(f"REVOKE EXECUTE ON FUNCTION {LARGE_OBJECT_CREATORS} FROM PUBLIC")
    op.execute(f"REVOKE TEMPORARY ON DATABASE {database} FROM PUBLIC")


def _create_reader_role_if_absent(conn: sa.engine.Connection, role: str) -> None:
    """Idempotent: roles are cluster-wide, and this migration runs on both hospital and
    hospital_test, so an existing role must keep its password rather than fail or be reset.

    CREATE ROLE has no bind-parameter form for its PASSWORD clause, so the password can't go
    through a plain SQLAlchemy text() statement - and building the SQL text ourselves (even
    with quotes doubled) is unsafe: a password containing a driver-specific delimiter (e.g. a
    dollar-quote tag) could still break out of the literal. psycopg's own SQL composition
    (sql.Literal) quotes the value as a single parameter however many quotes or special
    characters it contains, so it is used here directly against the raw psycopg connection
    that underlies this Alembic transaction, instead of building a SQL string at all.
    """
    exists = conn.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
    ).scalar()
    if exists:
        return
    password = os.environ.get("READER_DB_PASSWORD", "hospital_reader_dev")
    raw_conn = conn.connection.driver_connection  # the underlying psycopg.Connection
    statement = sql.SQL("CREATE ROLE {role} LOGIN PASSWORD {password}").format(
        role=sql.Identifier(role), password=sql.Literal(password)
    )
    with raw_conn.cursor() as cursor:
        cursor.execute(statement)


def _refuse_a_privileged_reader(conn: sa.engine.Connection, role: str) -> None:
    """A role that already existed is trusted with SELECT on names and phones only if it can do
    no more than a freshly created one: fail the migration rather than grant to it."""
    row = conn.execute(
        sa.text(
            "SELECT r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolreplication, r.rolbypassrls, "
            "       ARRAY(SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
            "             WHERE m.member = r.oid ORDER BY g.rolname) AS member_of "
            "FROM pg_roles r WHERE r.rolname = :role"
        ),
        {"role": role},
    ).one()
    attributes = [name for name, held in [
        ("SUPERUSER", row.rolsuper), ("CREATEROLE", row.rolcreaterole), ("CREATEDB", row.rolcreatedb),
        ("REPLICATION", row.rolreplication), ("BYPASSRLS", row.rolbypassrls),
    ] if held]
    if attributes or row.member_of:
        raise RuntimeError(
            f"migration 0004 refuses to grant the patients registry to the existing role {role!r}: "
            f"it is expected to be a plain login role, but it has "
            f"{', '.join(attributes) or 'no privileged attributes'}"
            f"{' and is a member of ' + ', '.join(row.member_of) if row.member_of else ''}. "
            f"Remove those (ALTER ROLE ... NOSUPERUSER ..., REVOKE <role> FROM {role}) and migrate again."
        )


def downgrade() -> None:
    # The grants go with the table, and the revokes are undone, so the database returns to its
    # pre-0004 state. The role stays: it is cluster-wide and may hold grants in the other
    # database, where dropping it would fail.
    conn = op.get_bind()
    database = _quote(conn, conn.execute(sa.text("SELECT current_database()")).scalar_one())
    op.execute(f"GRANT TEMPORARY ON DATABASE {database} TO PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {LARGE_OBJECT_CREATORS} TO PUBLIC")
    op.drop_table("patients")
