"""The request and response models of the API (Core design §9; sub-project 5 design §6).

Every model is explicit: a response model is what the client is allowed to see, and a
request model is the only shape the server accepts - it never carries an identity (§18.3).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

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


# --- authentication (§18.3) ------------------------------------------------------------------

class LoginRequest(BaseModel):
    user_id: str = Field(max_length=64)
    password: str = Field(max_length=256)


class Identity(BaseModel):
    user_id: str
    role: str
    display_name: str


class LoginResponse(Identity):
    token: str


# --- the patient's side --------------------------------------------------------------------

class NewRequest(BaseModel):
    """§3.1 RequestValid also checks the text; this is the first, cheap gate."""

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class DocumentUpload(BaseModel):
    document_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    format: Literal["pdf", "jpg", "png"]
    content: Annotated[str, StringConstraints(min_length=1, max_length=20000)]


class PatientStatusChange(BaseModel):
    """One step of the case's history: an abstract status, and when the case entered it."""

    model_config = ConfigDict(from_attributes=True)

    status: str
    at: datetime


class PatientCaseView(BaseModel):
    """What a patient may see (design decision 8): never an escalation kind, a reason or Audit."""

    model_config = ConfigDict(from_attributes=True)

    case_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    request_text: str | None
    missing_document_ids: list[str]
    missing_document_request_template_id: str | None
    message: str | None
    history: list[PatientStatusChange]


# --- the staff's side -----------------------------------------------------------------------

class ReviewItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: str
    patient_id: str
    escalation_kind: str
    escalated_from_state: str | None
    reasons: list[str]
    allowed_decisions: list[str]
    required_fields: list[str]
    updated_at: datetime


class ReviewContext(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: str
    patient_id: str
    state: str
    escalation_kind: str | None
    escalated_from_state: str | None
    reasons: list[str]
    data: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    shown_context_ref: str


class DecisionRequest(BaseModel):
    """`decision` and `reason` are strings, not an enum: the Human Review Service is the one
    that judges them, and its reason code (invalid_decision, reason_required) reaches the
    reviewer as a 409 instead of a schema error. reviewer_id is never accepted (§18.3)."""

    decision: str = Field(max_length=32)
    reason: str = Field(max_length=2000)
    shown_context_ref: str = Field(max_length=200)
    verified_identity_ref: str | None = Field(default=None, max_length=200)
    patient_deadline: AwareDatetime | None = None


class DecisionResponse(BaseModel):
    case_id: str
    state: str
