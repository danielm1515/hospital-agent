"""The patients registry (sub-project 9) - a table other systems read.

The §18.3 IdP stays the fixed user list in auth.py; this table is the registry the outside
world reads, and tests/test_patients.py fails if the two ever disagree. Names and phones are
personal data (§12.3): only hospital_reader, a role with SELECT on this one table, is given
to other systems.

db/init/01-init.sh runs only when the volume is first created, so the reader role is created
here, idempotently: roles are cluster-wide, and this migration runs on both hospital and
hospital_test. An existing role keeps its password.

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
READER_ROLE = "hospital_reader"

# The same three patients as auth.DEMO_USERS. The phones are placeholders the owner replaces.
PATIENTS = [
    {"patient_id": "P-10041", "full_name": "דנה כהן", "phone": "+972500000001"},
    {"patient_id": "P-20000", "full_name": "יוסי לוי", "phone": "+972500000002"},
    {"patient_id": "P-30000", "full_name": "מיכל אברהם", "phone": "+972500000003"},
]


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

    _create_reader_role_if_absent(op.get_bind())
    op.execute(f"GRANT SELECT ON patients TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON patients TO {READER_ROLE}")


def _create_reader_role_if_absent(conn: sa.engine.Connection) -> None:
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
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": READER_ROLE}
    ).scalar()
    if exists:
        return
    password = os.environ.get("READER_DB_PASSWORD", "hospital_reader_dev")
    raw_conn = conn.connection.driver_connection  # the underlying psycopg.Connection
    statement = sql.SQL("CREATE ROLE {role} LOGIN PASSWORD {password}").format(
        role=sql.Identifier(READER_ROLE), password=sql.Literal(password)
    )
    with raw_conn.cursor() as cursor:
        cursor.execute(statement)


def downgrade() -> None:
    # The grants go with the table. The role stays: it is cluster-wide and may hold grants in
    # the other database, where dropping it would fail.
    op.drop_table("patients")
