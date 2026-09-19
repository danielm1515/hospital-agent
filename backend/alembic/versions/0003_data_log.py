"""Data Log (spec §12.3; LLM design §7, decision 2 - a fifth table beside §18.2's four).

The case's own content - the request text, uploaded documents, instructions shown and
outgoing messages - lives here, never in audit_log, which keeps only content_hash as the
reference. A patient's deletion request leaves a tombstone: content becomes NULL and
deleted_at is set, while content_hash stays (§18.4). The application role may therefore
UPDATE but never DELETE.

Revision ID: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = "hospital_app"


def upgrade() -> None:
    op.create_table(
        "data_log",
        sa.Column("entry_id", sa.Text, primary_key=True),
        sa.Column("case_id", sa.Text, sa.ForeignKey("cases.case_id"), nullable=False),
        sa.Column("patient_id", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("content", sa.Text),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "kind IN ('request_text', 'uploaded_document', 'instructions', 'outgoing_message')",
            name="ck_data_log_kind",
        ),
    )
    op.create_index("ix_data_log_case_id_kind", "data_log", ["case_id", "kind"])
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON data_log TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("data_log")
