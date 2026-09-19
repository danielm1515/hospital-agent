"""Response models of the read-only Case Monitor API (design §9)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..naming import EscalationKind, SafetyLevel, State


class CaseSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: str
    state: State
    escalation_kind: EscalationKind | None
    updated_at: datetime


class CaseDetail(CaseSummary):
    patient_id: str
    state_version: int
    intent: str | None
    safety_level: SafetyLevel | None
    identity_verified: bool
    plan_hash: str | None
    ordered_steps: list[dict[str, Any]] | None
    current_step: int | None
    retry_cycle: int
    attempt_count: int
    required_documents: list[str] | None
    held_documents: list[str]
    escalated_from_state: State | None
    patient_deadline: datetime | None
    created_at: datetime


class AuditRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    audit_id: int
    record_type: str
    event: str
    state_before: str | None
    state_after: str | None
    action: str | None
    guards: dict[str, bool]
    policy_result: str | None
    policy_reasons: list[str]
    execution_id: str | None
    attempt_number: int | None
    retry_cycle: int | None
    approval_id: str | None
    rule_version: str
    recorded_at: datetime
