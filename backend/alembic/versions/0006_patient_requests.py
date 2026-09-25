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
    # NOT VALID, not op.create_check_constraint's validated form: the project never deletes
    # data (§18.4), so a downgrade must not refuse to run just because rows already use the
    # values this migration added (an approval with decision 'request', a data_log row of kind
    # 'staff_message' or 'patient_reply'). NOT VALID still refuses any new write of a removed
    # value; it only skips checking rows that are already there.
    op.drop_constraint("ck_data_log_kind", "data_log", type_="check")
    op.execute(f"ALTER TABLE data_log ADD CONSTRAINT ck_data_log_kind CHECK ({KINDS}) NOT VALID")
    op.drop_constraint("ck_approvals_decision", "approvals", type_="check")
    op.execute(f"ALTER TABLE approvals ADD CONSTRAINT ck_approvals_decision CHECK ({DECISIONS}) NOT VALID")
    op.drop_column("cases", "requested_document")
    op.drop_column("cases", "reply_kind")
    op.drop_column("cases", "human_engaged")
