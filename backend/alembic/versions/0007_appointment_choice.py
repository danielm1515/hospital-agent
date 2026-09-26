"""The patient-chosen appointment and CheckAppointment's new facts (sub-project 18 task 3).

cases gains seven nullable columns: appointment_id (the patient's chosen appointment, D5,
stored when the case is opened - REQUEST_SUBMITTED's own effect, before any plan runs;
write-once, never touched again) and six facts CheckAppointment's answer may now carry (D3,
D6) - answered_appointment_id (the service's own echo of the appointment it resolved -
deliberately a separate column from appointment_id, fix round 1/I2: the two must never be
conflated, since only appointment_id is ever sent back as the request's chosen id),
department, exam_type_label, instruction_source_id, instruction_version and upcoming_count -
stored by RECORD_RETRIEVAL on DATA_RETRIEVED, exactly like appointment_at (migration 0002).
Existing grants on cases cover new columns.

Revision ID: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

COLUMNS = ("appointment_id", "answered_appointment_id", "department", "exam_type_label",
          "instruction_source_id", "instruction_version")


def upgrade() -> None:
    # Final review M7: ADD COLUMN takes an ACCESS EXCLUSIVE lock on cases; on a live database
    # give up after 5 s rather than queue behind a long transaction and block every case
    # behind it. SET LOCAL lasts only for this migration's transaction. PostgreSQL only -
    # SQLite has no lock_timeout.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    for name in COLUMNS:
        op.add_column("cases", sa.Column(name, sa.Text))
    op.add_column("cases", sa.Column("upcoming_count", sa.Integer))


def downgrade() -> None:
    op.drop_column("cases", "upcoming_count")
    for name in reversed(COLUMNS):
        op.drop_column("cases", name)
