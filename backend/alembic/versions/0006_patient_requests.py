"""Staff requests to the patient (sub-project 15, design §11).

Three cases columns - human_engaged (set once a staff member writes to the patient, never
cleared: the case never goes back to the agent), reply_kind and requested_document (what the
open request asks for) - and two CHECK constraints widened, without which the database itself
refuses the new writes: a WorkflowDecision may now be a 'request', and the Data Log holds
staff messages and patient replies. Additive only.

Revision ID: 0006
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

DECISIONS = "decision IN ('approve', 'reject', 'resolve')"
KINDS = "kind IN ('request_text', 'uploaded_document', 'instructions', 'outgoing_message')"


def upgrade() -> None:
    op.add_column("cases", sa.Column("human_engaged", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("cases", sa.Column("reply_kind", sa.Text))
    op.add_column("cases", sa.Column("requested_document", sa.Text))
    op.drop_constraint("ck_approvals_decision", "approvals", type_="check")
    op.create_check_constraint("ck_approvals_decision", "approvals",
                               "decision IN ('approve', 'reject', 'resolve', 'request')")
    op.drop_constraint("ck_data_log_kind", "data_log", type_="check")
    op.create_check_constraint(
        "ck_data_log_kind", "data_log",
        "kind IN ('request_text', 'uploaded_document', 'instructions', 'outgoing_message', "
        "'staff_message', 'patient_reply')")


def downgrade() -> None:
    op.drop_constraint("ck_data_log_kind", "data_log", type_="check")
    op.create_check_constraint("ck_data_log_kind", "data_log", KINDS)
    op.drop_constraint("ck_approvals_decision", "approvals", type_="check")
    op.create_check_constraint("ck_approvals_decision", "approvals", DECISIONS)
    op.drop_column("cases", "requested_document")
    op.drop_column("cases", "reply_kind")
    op.drop_column("cases", "human_engaged")
