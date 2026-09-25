"""The admin metrics screen's window indexes (sub-project 14, design §6).

Every metric filters one timestamp to the requested window: audit_log.recorded_at for the
event groups, cases.created_at for the cohort group and executions.started_at for the tools.
None of them was indexed. Additive only; the metrics read, they never write.

Revision ID: 0005
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_audit_log_recorded_at", "audit_log", ["recorded_at"])
    op.create_index("ix_cases_created_at", "cases", ["created_at"])
    op.create_index("ix_executions_started_at", "executions", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_executions_started_at", table_name="executions")
    op.drop_index("ix_cases_created_at", table_name="cases")
    op.drop_index("ix_audit_log_recorded_at", table_name="audit_log")
