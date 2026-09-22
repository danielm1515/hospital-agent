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

    # A dev default, like APP_DB_PASSWORD. Quotes are doubled so the literal stays one literal.
    password = os.environ.get("READER_DB_PASSWORD", "hospital_reader_dev").replace("'", "''")
    op.execute(
        f"""
        DO $body$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{READER_ROLE}') THEN
                CREATE ROLE {READER_ROLE} LOGIN PASSWORD '{password}';
            END IF;
        END
        $body$
        """
    )
    op.execute(f"GRANT SELECT ON patients TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON patients TO {READER_ROLE}")


def downgrade() -> None:
    # The grants go with the table. The role stays: it is cluster-wide and may hold grants in
    # the other database, where dropping it would fail.
    op.drop_table("patients")
