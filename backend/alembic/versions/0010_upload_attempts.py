"""upload_attempts: one row per patient upload attempt and how it ended (docs/spec_corrections.md
row 98).

A refused upload, or one the document-service never answered, adds no event to the case, so the
Audit never saw it. This table keeps the attempt for the staff audit journal: the case, the path
(`upload` | `reply`), the outcome code the patient was shown and the detail code behind it -
codes only, never the file, its name or a document id (§12.3).

Bookkeeping outside Audit, like llm_usage (migration 0008, row 94): hospital_app gets SELECT,
INSERT only (append-only, like audit_log); hospital_reader gets nothing.

Revision ID: 0010
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

APP_ROLE = "hospital_app"


def upgrade() -> None:
    op.create_table(
        "upload_attempts",
        sa.Column("attempt_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("case_id", sa.Text, sa.ForeignKey("cases.case_id"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('upload', 'reply')", name="ck_upload_attempts_kind"),
    )
    op.create_index("ix_upload_attempts_case", "upload_attempts", ["case_id", "created_at"])
    op.execute(f"GRANT SELECT, INSERT ON upload_attempts TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_upload_attempts_case", table_name="upload_attempts")
    op.drop_table("upload_attempts")
