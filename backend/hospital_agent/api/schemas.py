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


class ReplyRequestView(BaseModel):
    """Sub-project 15: what the staff asked for (design §10). Never a reason or an escalation kind."""

    model_config = ConfigDict(from_attributes=True)

    kind: Literal["question", "document"]
    message: str | None
    document_type: str | None
    deadline: datetime | None


class ConversationEntryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sender: Literal["staff", "patient"]
    text: str
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
    document_upload: Literal["file", "text"]
    reply_request: ReplyRequestView | None
    conversation: list[ConversationEntryView]


class UploadResult(BaseModel):
    """The outcome of one PDF (sub-project 13, design §5.3): an abstract code only."""

    model_config = ConfigDict(from_attributes=True)

    code: Literal["accepted", "not_required", "already_received", "not_medical", "unreadable", "expired",
                  "not_yours", "wrong_document_type"]
    document_type: str | None


class PdfUploadResponse(BaseModel):
    upload: UploadResult
    request: PatientCaseView


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
    human_engaged: bool
    returned_by: Literal["patient_reply", "reply_timeout"] | None


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


class MessageBody(BaseModel):
    """Sub-project 15 (design §7.2): a template (with its parameter) or clinical free text -
    the Human Review Service judges which, so the reason code reaches the reviewer."""

    template_id: str | None = Field(default=None, max_length=64)
    param: str | None = Field(default=None, max_length=64)
    text: str | None = Field(default=None, max_length=2000)


class DecisionRequest(BaseModel):
    """`decision` and `reason` are strings, not an enum: the Human Review Service is the one
    that judges them, and its reason code (invalid_decision, reason_required) reaches the
    reviewer as a 409 instead of a schema error. reviewer_id is never accepted (§18.3)."""

    decision: str = Field(max_length=32)
    reason: str = Field(max_length=2000)
    shown_context_ref: str = Field(max_length=200)
    verified_identity_ref: str | None = Field(default=None, max_length=200)
    patient_deadline: AwareDatetime | None = None
    message: MessageBody | None = None  # sub-project 15: a closing message on resolve / reject


class AnswerRequest(BaseModel):
    """§5 AnswerClinicalQuestion. `answer` is the exact text the ContentApproval covers."""

    answer: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    reason: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    shown_context_ref: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class DecisionResponse(BaseModel):
    case_id: str
    state: str


class PatientRequestBody(MessageBody):
    """Sub-project 15 (design §10): POST /api/staff/cases/{id}/request."""

    kind: str = Field(max_length=16)
    reason: str = Field(max_length=2000)
    shown_context_ref: str = Field(max_length=200)
    document_type: str | None = Field(default=None, max_length=64)
    deadline: AwareDatetime | None = None


class MessageTemplateView(BaseModel):
    template_id: str
    purpose: Literal["question", "document", "closing"]
    text: str
    param: str | None
    options: dict[str, str]


class ReplyBody(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


# --- sub-project 14: the admin metrics screen (design 2026-09-24 §5) ----------------------
# Built from hospital_agent.metrics' dataclasses (from_attributes). Aggregates only: no model
# below has a patient_id, a case_id or any request content (§12.3).


class _FromMetrics(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MetricsWindow(_FromMetrics):
    start: datetime
    end: datetime


class DurationsView(_FromMetrics):
    """Seconds; every field but count is null when nothing was measured."""

    count: int
    p50: float | None
    p95: float | None
    max: float | None


class FlowView(_FromMetrics):
    opened: int
    by_state: dict[str, int]
    by_outcome: dict[str, int]
    completion: dict[str, DurationsView]


class HumanLoadView(_FromMetrics):
    escalations_entered: int
    decisions: dict[str, int]
    decided_by_kind: dict[str, int]
    open_by_kind: dict[str, int]
    time_to_decision: DurationsView
    open_now: int
    oldest_open_seconds: float | None


class ToolActionView(_FromMetrics):
    action: str
    by_status: dict[str, int]
    success_rate: float | None
    latency: DurationsView


class FailureReasonView(_FromMetrics):
    outcome: str
    reason: str | None
    count: int


class ToolsView(_FromMetrics):
    actions: list[ToolActionView]
    failure_events: dict[str, int]
    failure_reasons: list[FailureReasonView]
    retried_calls: int
    sources: dict[str, str | None]


class PatientSlaView(_FromMetrics):
    requests: int
    met: int
    breached: int
    other: int
    waiting: int
    rate: float | None


class PolicyView(_FromMetrics):
    decisions: dict[str, int]
    blocked: int
    blocked_by_reason: dict[str, int]
    blocked_by_event: dict[str, int]


class MetricsResponse(_FromMetrics):
    window: MetricsWindow
    generated_at: datetime
    flow: FlowView
    human_load: HumanLoadView
    tools: ToolsView
    patient_sla: PatientSlaView
    policy: PolicyView


# --- sub-project 16: the patient's appointments (design D5, D6) ---------------------------
# Read live from the appointment-service and never stored (docs/spec_corrections.md row 89).


class AppointmentView(BaseModel):
    """One appointment, as the appointment-service answered it (sub-project 16, design D6)."""

    model_config = ConfigDict(from_attributes=True)

    appointment_id: str
    appointment_at: datetime
    department: str
    doctor_name: str | None
    location: str | None
    status: Literal["Scheduled", "Cancelled"]
    required_documents: list[str]


class AppointmentsView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    window_from: datetime = Field(alias="from")
    window_to: datetime = Field(alias="to")
    appointments: list[AppointmentView]
    truncated: bool
