"""The request and response models of the API (Core design §9; sub-project 5 design §6).

Every model is explicit: a response model is what the client is allowed to see, and a
request model is the only shape the server accepts - it never carries an identity (§18.3).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints

from ..llm_costs import money
from ..naming import EscalationKind, SafetyLevel, State

# Sub-project 19 (design D6): money is a Decimal here and a decimal string with exactly 8
# places in JSON ("0.00213400") - never a float, which would drift.
Money = Annotated[Decimal, PlainSerializer(lambda value: format(money(value), "f"), return_type=str)]


class CaseSummary(BaseModel):
    """One `GET /api/staff/cases` item (staff-fixes design Task 3): every column the Case
    Monitor table shows, so the client renders each row straight from the list and makes
    no per-row follow-up call."""

    model_config = ConfigDict(from_attributes=True)

    case_id: str
    patient_id: str
    state: State
    intent: str | None
    safety_level: SafetyLevel | None
    escalation_kind: EscalationKind | None
    escalated_from_state: State | None
    created_at: datetime
    updated_at: datetime
    # Sub-project 19 (design D6): the case's LLM cost so far (docs/api.md for the NULL rule).
    llm_cost_usd: Money | None = None
    # true when llm_cost_usd is known but a lower bound: the case also has unpriced attempts.
    llm_cost_partial: bool = False
    # the case's unpriced attempts (price_input_per_mtok IS NULL); 0 with no row at all.
    llm_unpriced_calls: int = 0


class LlmCallView(BaseModel):
    """Sub-project 19 (design D6): one call code's attempts and their cost."""

    model_config = ConfigDict(from_attributes=True)

    call: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Money | None


class LlmSourceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    calls: int
    cost_usd: Money | None


class CaseLlmUsageView(BaseModel):
    """`CaseDetail.llm_usage` (llm_costs.CaseUsage)."""

    model_config = ConfigDict(from_attributes=True)

    calls: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: Money | None
    unpriced_calls: int
    by_call: list[LlmCallView]


class CaseListPage(BaseModel):
    """`GET /api/staff/cases` (staff-fixes design Task 3): keyset-paginated."""

    items: list[CaseSummary]
    next_cursor: str | None


class CaseDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: str
    state: State
    escalation_kind: EscalationKind | None
    updated_at: datetime
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
    # Sub-project 18 (design D13): the chosen appointment, its exam type/department and the
    # instruction source - staff-only, read-only, never shown to the patient.
    appointment_id: str | None = None
    answered_appointment_id: str | None = None
    department: str | None = None
    exam_type_label: str | None = None
    instruction_source_id: str | None = None
    instruction_version: str | None = None
    # Sub-project 19 (design D6): staff-only, and deliberately not in ReviewContext - its
    # `shown` is hashed into shown_context_ref, and bookkeeping must never refuse a decision.
    llm_usage: CaseLlmUsageView | None = None


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
    # Sub-project 18 (design D5): the appointment the patient picked, from the sub-project 16
    # appointments list - optional; "the nearest appointment" (no id) omits it entirely.
    appointment_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")] | None = None


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


class PatientInstructionsView(BaseModel):
    """Sub-project 18 (design D11): the case's own Data Log `instructions` entry, split into
    its title and text."""

    model_config = ConfigDict(from_attributes=True)

    title: str
    text: str


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
    instructions: PatientInstructionsView | None = None


class UploadResult(BaseModel):
    """The outcome of one document upload (sub-project 13, design §5.3; sub-project 17 task 2
    for the image and per-reason codes): an abstract code only."""

    model_config = ConfigDict(from_attributes=True)

    code: Literal["accepted", "not_required", "already_received", "not_medical", "unreadable", "expired",
                  "not_yours", "wrong_document_type", "unrecognised_type", "unreadable_scan", "bad_date",
                  "no_date", "unsupported_format", "too_large"]
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
    # Staff-fixes design Task 5: when the case entered AwaitingHumanReview - the queue's
    # order key (newest first), replacing `updated_at`.
    entered_at: datetime
    human_engaged: bool
    returned_by: Literal["patient_reply", "reply_timeout"] | None


class ReviewQueuePage(BaseModel):
    """`GET /api/staff/reviews` (staff-fixes design Task 5): keyset-paginated, the same
    shape as `CaseListPage`."""

    items: list[ReviewItem]
    next_cursor: str | None


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
    intent: str | None = None
    safety_level: str | None = None
    llm_calls: list[dict[str, Any]] = []
    upload_attempts: list[dict[str, Any]] = []
    shown_context_ref: str
    # Sub-project 18 (design D13): read-only, same as CaseDetail above.
    appointment_id: str | None = None
    answered_appointment_id: str | None = None
    department: str | None = None
    exam_type_label: str | None = None
    instruction_source_id: str | None = None
    instruction_version: str | None = None


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


class LlmCostsView(_FromMetrics):
    """Sub-project 19 (design D6): metrics.LlmCosts - the admin metrics' `llm` group."""

    cases: int
    cases_with_usage: int
    calls: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_cost_usd: Money | None
    avg_cost_per_case_usd: Money | None
    avg_cost_per_completed_case_usd: Money | None
    unpriced_calls: int
    by_call: list[LlmCallView]
    by_source: list[LlmSourceView]


class LlmCostsResponse(LlmCostsView):
    """`GET /api/staff/llm-costs`: the same numbers, with their window."""

    window: MetricsWindow


class MetricsResponse(_FromMetrics):
    window: MetricsWindow
    generated_at: datetime
    flow: FlowView
    human_load: HumanLoadView
    tools: ToolsView
    patient_sla: PatientSlaView
    policy: PolicyView
    llm: LlmCostsView


# --- sub-project 16: the patient's appointments (design D5, D6) ---------------------------
# Read live from the appointment-service and never stored (docs/spec_corrections.md row 89).


class ExamTypeView(BaseModel):
    """Sub-project 18 (design D3): one appointment's exam type, as the appointment-service
    answered it - never null in the real service, but optional here (an older service simply
    omits it)."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    label: str


class InstructionSummaryView(BaseModel):
    """Sub-project 18 (design D3, D12): which preparation instruction belongs to the
    appointment - never its text (the panel loads that separately, through
    GET /api/patient/instructions/{source_id})."""

    model_config = ConfigDict(from_attributes=True)

    source_id: str
    version: str
    title: str


class AppointmentView(BaseModel):
    """One appointment, as the appointment-service answered it (sub-project 16, design D6;
    sub-project 18 design D3 adds exam_type/instruction)."""

    model_config = ConfigDict(from_attributes=True)

    appointment_id: str
    appointment_at: datetime
    department: str
    doctor_name: str | None
    location: str | None
    status: Literal["Scheduled", "Cancelled"]
    required_documents: list[str]
    exam_type: ExamTypeView | None = None
    instruction: InstructionSummaryView | None = None


class InstructionView(BaseModel):
    """Sub-project 18 (design D12): `GET /api/patient/instructions/{source_id}` and its staff
    counterpart - the approved text behind one appointment's `instruction` summary above."""

    model_config = ConfigDict(from_attributes=True)

    source_id: str
    version: str
    title: str
    text: str


class AppointmentsView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    window_from: datetime = Field(alias="from")
    window_to: datetime = Field(alias="to")
    appointments: list[AppointmentView]
    truncated: bool


# --- staff-fixes design Task 1: is the LLM (still) called at all? --------------------------


class LlmStatusView(BaseModel):
    last_ok_at: str | None
    last_error: str | None
    last_error_at: str | None


class DocumentsStatusView(BaseModel):
    """Row 98: the document-service as the staff banner sees it."""
    configured: bool
    health: str | None  # ok | degraded | unreachable; None when not configured
    last_ok_at: str | None
    last_error: str | None
    last_error_at: str | None


class SystemStatusView(BaseModel):
    orchestrator: str | None
    llm: LlmStatusView
    documents: DocumentsStatusView


class ConsistencyQuery(BaseModel):
    property: str
    description: str
    result: str  # unsat | sat | unknown
    proved: bool


class ConsistencyResponse(BaseModel):
    """`GET /api/admin/consistency`: the spec §9.2 proofs (`policy/consistency.py`)."""
    engine: str
    all_proved: bool
    queries: list[ConsistencyQuery]
