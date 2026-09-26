"""LLM token usage and cost per attempt (sub-project 19, design D4).

One row per LLM attempt that reached the provider, ok or failed, attributed to its case:
the source (the agent's own four calls, or the document-service's one call per upload),
the call, the model, the outcome code, the three token counts (NULL when the provider
reported no usage) and the price in force with the attempt's cost (NULL when the model has
no price). Counts, codes and money only - never a prompt, an answer or a patient field
(§12.3).

Append-only, like audit_log: hospital_app gets SELECT, INSERT and nothing else, so the
database itself refuses to change or delete a row; hospital_reader gets nothing.

Revision ID: 0008
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

APP_ROLE = "hospital_app"
SOURCES = ("agent", "document_service")
CALLS = ("Intent", "Safety", "Planner", "Evaluator", "DocumentClassify", "DocumentVision")
TOKENS = ("input_tokens", "cached_input_tokens", "output_tokens")
MONEY = ("price_input_per_mtok", "price_output_per_mtok", "cost_usd")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("usage_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("case_id", sa.Text, sa.ForeignKey("cases.case_id"), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("call", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        *(sa.Column(name, sa.Integer) for name in TOKENS),
        *(sa.Column(name, sa.Numeric) for name in MONEY),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in("source", SOURCES), name="ck_llm_usage_source"),
        sa.CheckConstraint(_in("call", CALLS), name="ck_llm_usage_call"),
        sa.CheckConstraint(" AND ".join(f"({name} IS NULL OR {name} >= 0)" for name in TOKENS + MONEY),
                           name="ck_llm_usage_non_negative"),
        # a usage is the three counts together, or none of them
        sa.CheckConstraint(f"({' IS NULL AND '.join(TOKENS)} IS NULL) OR "
                           f"({' IS NOT NULL AND '.join(TOKENS)} IS NOT NULL)",
                           name="ck_llm_usage_tokens_together"),
        sa.CheckConstraint("cached_input_tokens IS NULL OR input_tokens IS NULL "
                           "OR cached_input_tokens <= input_tokens", name="ck_llm_usage_cached_within_input"),
    )
    op.create_index("ix_llm_usage_case_id", "llm_usage", ["case_id"])
    op.execute(f"GRANT SELECT, INSERT ON llm_usage TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("llm_usage")
