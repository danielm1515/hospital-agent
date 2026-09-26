"""The patient-chosen appointment and CheckAppointment's new facts (sub-project 18 task 3).

cases gains six nullable columns: appointment_id (the patient's chosen appointment, D5,
stored when the case is opened - REQUEST_SUBMITTED's own effect, before any plan runs) and
five facts CheckAppointment's answer may now carry (D3, D6) - department, exam_type_label,
instruction_source_id, instruction_version and upcoming_count - stored by RECORD_RETRIEVAL
on DATA_RETRIEVED, exactly like appointment_at (migration 0002). Existing grants on cases
cover new columns.

Revision ID: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

COLUMNS = ("appointment_id", "department", "exam_type_label", "instruction_source_id", "instruction_version")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column("cases", sa.Column(name, sa.Text))
    op.add_column("cases", sa.Column("upcoming_count", sa.Integer))


def downgrade() -> None:
    op.drop_column("cases", "upcoming_count")
    for name in reversed(COLUMNS):
        op.drop_column("cases", name)
