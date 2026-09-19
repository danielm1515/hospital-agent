"""Initial schema: the four tables of spec §18.2, and the hospital_app grants.

audit_log also carries `action` and `outcome` from the §12.2 record (see
docs/spec_corrections.md). hospital_app gets INSERT but no UPDATE/DELETE on
audit_log, so the database itself keeps the Audit append-only.

Revision ID: 0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "hospital_app"


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("case_id", sa.Text, primary_key=True),
        sa.Column("patient_id", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("state_version", sa.Integer, nullable=False),
        sa.Column("intent", sa.Text),
        sa.Column("safety_level", sa.Text),
        sa.Column("identity_verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("plan_hash", sa.Text),
        sa.Column("ordered_steps", JSONB),
        sa.Column("current_step", sa.Integer),
        sa.Column("retry_cycle", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("required_documents", JSONB),
        sa.Column("held_documents", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("escalation_kind", sa.Text),
        sa.Column("escalated_from_state", sa.Text),
        sa.Column("patient_deadline", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cases_state_patient_deadline", "cases", ["state", "patient_deadline"])
    op.create_index("ix_cases_state_updated_at", "cases", ["state", "updated_at"])

    op.create_table(
        "executions",
        sa.Column("execution_id", sa.Text, primary_key=True),
        sa.Column("case_id", sa.Text, sa.ForeignKey("cases.case_id"), nullable=False),
        sa.Column("patient_id", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("step", sa.Integer, nullable=False),
        sa.Column("retry_cycle", sa.Integer, nullable=False),
        sa.Column("attempt_number", sa.Integer, nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False, unique=True),
        sa.Column("decision_token", sa.Text),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('intent', 'started', 'succeeded', 'failed', 'unknown')", name="ck_executions_status"
        ),
    )
    op.create_index("ix_executions_status", "executions", ["status"])

    op.create_table(
        "audit_log",
        sa.Column("audit_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("case_id", sa.Text, sa.ForeignKey("cases.case_id"), nullable=False),
        sa.Column("patient_id", sa.Text, nullable=False),
        sa.Column("execution_id", sa.Text),
        sa.Column("record_type", sa.Text, nullable=False),
        sa.Column("event", sa.Text, nullable=False),
        sa.Column("action", sa.Text),
        sa.Column("state_before", sa.Text),
        sa.Column("state_after", sa.Text),
        sa.Column("guards", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("policy_result", sa.Text),
        sa.Column("policy_reasons", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("attempt_number", sa.Integer),
        sa.Column("retry_cycle", sa.Integer),
        sa.Column("outcome", sa.Text),
        sa.Column("approval_id", sa.Text),
        sa.Column("content_hash", sa.Text),
        sa.Column("rule_version", sa.Text, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "record_type IN ('Transition', 'ExecutionStarted', 'ExecutionSucceeded', 'ExecutionFailed', "
            "'ExecutionUnknown', 'PolicyDecision', 'Blocked')",
            name="ck_audit_log_record_type",
        ),
    )
    op.create_index("ix_audit_log_case_id_audit_id", "audit_log", ["case_id", "audit_id"])
    op.create_index("ix_audit_log_execution_id", "audit_log", ["execution_id"])

    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.Text, primary_key=True),
        sa.Column("approval_type", sa.Text, nullable=False),
        sa.Column("case_id", sa.Text, sa.ForeignKey("cases.case_id"), nullable=False),
        sa.Column("patient_id", sa.Text, nullable=False),
        sa.Column("execution_id", sa.Text),
        sa.Column("action", sa.Text),
        sa.Column("content_hash", sa.Text),
        sa.Column("reviewer_id", sa.Text, nullable=False),
        sa.Column("reviewer_role", sa.Text, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("escalation_kind", sa.Text),
        sa.Column("plan_hash", sa.Text),
        sa.Column("current_step", sa.Integer),
        sa.Column("verified_identity_ref", sa.Text),
        sa.Column("patient_deadline", sa.DateTime(timezone=True)),
        sa.Column("shown_context_ref", sa.Text, nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("approval_type IN ('WorkflowDecision', 'ContentApproval')", name="ck_approvals_type"),
        sa.CheckConstraint("decision IN ('approve', 'reject', 'resolve')", name="ck_approvals_decision"),
    )
    op.create_index("ix_approvals_case_id_consumed_at", "approvals", ["case_id", "consumed_at"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON cases, executions, approvals TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_log TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("approvals")
    op.drop_table("audit_log")
    op.drop_table("executions")
    op.drop_table("cases")
