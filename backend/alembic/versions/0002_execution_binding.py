"""Execution binding (Execution design decision 1; an addition to spec §18.2).

executions gains what a Policy decision was bound to, so the Tool Executor can re-verify
it right before the call (ExecutorReverified, §3.1): state_version, plan_hash, the
ContentApproval it relies on (approval_id, content_hash) and medical_content_flag.
cases gains appointment_at, the time CheckAppointment returned; the Readiness Check
derives hours_until from it (§9.1). Existing grants on both tables cover new columns.

Revision ID: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("state_version", sa.Integer))
    op.add_column("executions", sa.Column("plan_hash", sa.Text))
    op.add_column("executions", sa.Column("approval_id", sa.Text))
    op.add_column("executions", sa.Column("content_hash", sa.Text))
    op.add_column("executions", sa.Column("medical_content_flag", sa.Boolean, nullable=False,
                                          server_default=sa.false()))
    op.add_column("cases", sa.Column("appointment_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("cases", "appointment_at")
    for column in ("medical_content_flag", "content_hash", "approval_id", "plan_hash", "state_version"):
        op.drop_column("executions", column)
