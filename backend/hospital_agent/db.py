"""Postgres schema (spec §18.2, plus the §12.3 Data Log and the sub-project 9 patients registry) as SQLAlchemy Core tables, and engine creation.

alembic/versions/ holds the migrations that create these tables; this module
mirrors them for queries. tests/test_schema.py fails if the two drift apart.
"""
from __future__ import annotations

import os

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

metadata = MetaData()

cases = Table(
    "cases",
    metadata,
    Column("case_id", Text, primary_key=True),
    Column("patient_id", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("state_version", Integer, nullable=False),
    Column("intent", Text),
    Column("safety_level", Text),
    Column("identity_verified", Boolean, nullable=False),
    Column("plan_hash", Text),
    Column("ordered_steps", JSONB),
    Column("current_step", Integer),
    Column("retry_cycle", Integer, nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("required_documents", JSONB),
    Column("held_documents", JSONB, nullable=False),
    Column("escalation_kind", Text),
    Column("escalated_from_state", Text),
    Column("patient_deadline", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("appointment_at", DateTime(timezone=True)),  # migration 0002
    Column("human_engaged", Boolean, nullable=False),  # migration 0006
    Column("reply_kind", Text),  # migration 0006
    Column("requested_document", Text),  # migration 0006
    Column("appointment_id", Text),  # migration 0007
    Column("answered_appointment_id", Text),  # migration 0007
    Column("department", Text),  # migration 0007
    Column("exam_type_label", Text),  # migration 0007
    Column("instruction_source_id", Text),  # migration 0007
    Column("instruction_version", Text),  # migration 0007
    Column("upcoming_count", Integer),  # migration 0007
)

executions = Table(
    "executions",
    metadata,
    Column("execution_id", Text, primary_key=True),
    Column("case_id", Text, ForeignKey("cases.case_id"), nullable=False),
    Column("patient_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("step", Integer, nullable=False),
    Column("retry_cycle", Integer, nullable=False),
    Column("attempt_number", Integer, nullable=False),
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column("decision_token", Text),
    Column("status", Text, nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    # migration 0002: what the Policy decision was bound to (ExecutorReverified)
    Column("state_version", Integer),
    Column("plan_hash", Text),
    Column("approval_id", Text),
    Column("content_hash", Text),
    Column("medical_content_flag", Boolean, nullable=False),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("audit_id", BigInteger, Identity(), primary_key=True),
    Column("case_id", Text, ForeignKey("cases.case_id"), nullable=False),
    Column("patient_id", Text, nullable=False),
    Column("execution_id", Text),
    Column("record_type", Text, nullable=False),
    Column("event", Text, nullable=False),
    Column("action", Text),
    Column("state_before", Text),
    Column("state_after", Text),
    Column("guards", JSONB, nullable=False),
    Column("policy_result", Text),
    Column("policy_reasons", JSONB, nullable=False),
    Column("attempt_number", Integer),
    Column("retry_cycle", Integer),
    Column("outcome", Text),
    Column("approval_id", Text),
    Column("content_hash", Text),
    Column("rule_version", Text, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)

approvals = Table(
    "approvals",
    metadata,
    Column("approval_id", Text, primary_key=True),
    Column("approval_type", Text, nullable=False),
    Column("case_id", Text, ForeignKey("cases.case_id"), nullable=False),
    Column("patient_id", Text, nullable=False),
    Column("execution_id", Text),
    Column("action", Text),
    Column("content_hash", Text),
    Column("reviewer_id", Text, nullable=False),
    Column("reviewer_role", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("escalation_kind", Text),
    Column("plan_hash", Text),
    Column("current_step", Integer),
    Column("verified_identity_ref", Text),
    Column("patient_deadline", DateTime(timezone=True)),
    Column("shown_context_ref", Text, nullable=False),
    Column("granted_at", DateTime(timezone=True), nullable=False),
    Column("valid_until", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True)),
)


# Migration 0003 (LLM design §7): the §12.3 Data Log - the case's own content, never in audit_log.
data_log = Table(
    "data_log",
    metadata,
    Column("entry_id", Text, primary_key=True),
    Column("case_id", Text, ForeignKey("cases.case_id"), nullable=False),
    Column("patient_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("content", Text),
    Column("content_hash", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
)


# Migration 0004 (sub-project 9): the registry other systems read via hospital_reader.
patients = Table(
    "patients",
    metadata,
    Column("patient_id", Text, primary_key=True),
    Column("full_name", Text, nullable=False),
    Column("phone", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def make_engine(url: str | None = None) -> Engine:
    """Engine for the application role (hospital_app). Defaults to $DATABASE_URL.

    hide_parameters=True: a SQLAlchemy StatementError otherwise includes bound parameters
    (e.g. patient_id) in its message, which would leak into the application log (§12.3).
    """
    return create_engine(url or os.environ["DATABASE_URL"], pool_pre_ping=True, hide_parameters=True)
