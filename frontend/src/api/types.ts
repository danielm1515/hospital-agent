/**
 * Request and response shapes of sub-project 5's `/api`, as documented in `docs/api.md`.
 * Keep every API type here so it stays easy to align with that contract.
 */

// ---- Names (spec §2, §17) -------------------------------------------------

export const STATES = [
  'Received',
  'Classifying',
  'Classified',
  'Planning',
  'RetrievingData',
  'Delivering',
  'AssessingReadiness',
  'AwaitingPatientInput',
  'AwaitingHumanReview',
  'Ready',
  'Completed',
  'Failed',
  'AwaitingPatientReply', // sub-project 15's extension (docs/api.md §8)
] as const
export type State = (typeof STATES)[number]

export type EscalationKind =
  | 'PatientVerificationFailed'
  | 'MedicalQuestion'
  | 'SafetyEscalation'
  | 'ClassificationFailed'
  | 'TemporalViolation'
  | 'PlanningFailed'
  | 'PolicyDenied'
  | 'PolicyReview'
  | 'RetryExhausted'
  | 'NonIdempotentFailure'
  | 'ExecutionUnknown'
  | 'Z3Counterexample'
  | 'PatientSlaExpired'
  | 'DeliveryStepMissing'

export type SafetyLevel = 'LowRisk' | 'MediumRisk' | 'HighRisk' | 'CriticalRisk'

/**
 * The Case Monitor's five groups (staff-fixes design Task 4, `docs/api.md` §5): the
 * `?group=` value `GET /api/staff/cases` takes, mirroring the backend's
 * `hospital_agent.state_groups.STATE_GROUPS`. `group: 'staff'` alone also accepts
 * `?escalation_kind=`.
 */
export type StateGroup = 'staff' | 'patient' | 'automatic' | 'done' | 'rejected'

/** ISO 8601 datetime string, as FastAPI serializes `datetime`. */
export type IsoDateTime = string

// ---- Auth -----------------------------------------------------------------

export type Role = 'patient' | 'clinical_staff' | 'admin_staff'
export const STAFF_ROLES: readonly Role[] = ['clinical_staff', 'admin_staff']

export function isStaffRole(role: Role): boolean {
  return STAFF_ROLES.includes(role)
}

export interface LoginRequest {
  user_id: string
  password: string
}

/** `GET /api/auth/me`. */
export interface Me {
  user_id: string
  role: Role
  display_name: string
}

/** `POST /api/auth/login` → 200. */
export interface LoginResponse extends Me {
  token: string
}

// ---- Patient --------------------------------------------------------------

/** The patient-facing status (sub-project 5 `patient_status`). */
export type PatientStatus =
  | 'received'
  | 'in_progress'
  | 'needs_document'
  | 'needs_reply'
  | 'in_review'
  | 'completed'
  | 'closed'

/** One step of a case's history: an abstract status, and when the case entered it. */
export interface StatusChange {
  status: PatientStatus
  at: IsoDateTime
}

/** `PatientView`: all a patient may see of a case (§12.3: no escalation or Audit data). */
export interface PatientView {
  case_id: string
  status: PatientStatus
  created_at: IsoDateTime
  updated_at: IsoDateTime
  request_text: string | null
  /** Sorted; `[]` unless `status === 'needs_document'`. */
  missing_document_ids: string[]
  /** `'missing-document-v1'` iff `status === 'needs_document'` (D24). */
  missing_document_request_template_id: string | null
  /** The delivered message iff `status === 'completed'`; the closing message, if any, when `closed`. */
  message: string | null
  /**
   * Every status change, oldest first, with the time the case entered it. The last
   * entry's `status` is always `status`; a status the case entered twice is listed twice.
   */
  history: StatusChange[]
  /**
   * Which upload the `needs_document` screen offers (sub-project 13, `docs/api.md` §4):
   * `'file'` for a document picker (PDF, JPEG or PNG) sent to `POST .../documents/file` when the server is
   * configured with the document-service, `'text'` for the text form sent to
   * `POST .../documents` otherwise. The same for every case of a running server.
   */
  document_upload: 'file' | 'text'
  /** Sub-project 15: what the staff asked for, iff `status === 'needs_reply'`. */
  reply_request: ReplyRequest | null
  /** Staff messages the patient may see and the patient's replies, oldest first. */
  conversation: ConversationEntry[]
  /**
   * Sub-project 18 (design D11, `docs/api.md` §4): the preparation instruction the case
   * loaded and delivered, split into title and body - only on an agent-delivered
   * `completed` case, `null` otherwise.
   */
  instructions: PatientInstructions | null
}

/** `PatientView.instructions` (sub-project 18): exactly what was approved and shown. */
export interface PatientInstructions {
  title: string
  text: string
}

export interface CreateRequestBody {
  text: string
  /**
   * Sub-project 18 (design D5): the appointment the patient picked from their own upcoming
   * list. Omitted entirely for "the nearest appointment" - there is no value meaning that.
   */
  appointment_id?: string
}

export type DocumentFormat = 'pdf' | 'jpg' | 'png'
export const DOCUMENT_FORMATS: readonly DocumentFormat[] = ['pdf', 'jpg', 'png']

export interface UploadDocumentBody {
  /** 1–64 characters of `[A-Za-z0-9_-]`. */
  document_id: string
  format: DocumentFormat
  /** 1–20000 characters. */
  content: string
}

/** The document catalog (sub-project 11, `docs/api.md` §4). */
export type DocumentType = 'CBC' | 'COAGULATION_TESTS' | 'ECG' | 'URINALYSIS' | 'PREOP_SUMMARY'
export const DOCUMENT_TYPES: readonly DocumentType[] = [
  'CBC',
  'COAGULATION_TESTS',
  'ECG',
  'URINALYSIS',
  'PREOP_SUMMARY',
]

/** `PdfUploadResponse.upload.code` (`docs/api.md` §4). */
export type UploadCode =
  | 'accepted'
  | 'not_required'
  | 'already_received'
  | 'not_medical'
  | 'unreadable'
  | 'expired'
  | 'not_yours'
  | 'wrong_document_type'
  | 'unrecognised_type'
  | 'unreadable_scan'
  | 'bad_date'
  | 'no_date'
  | 'unsupported_format'
  | 'too_large'

export interface UploadResult {
  code: UploadCode
  /**
   * The catalog type for `accepted`, `not_required` and `already_received`, and (sub-project
   * 15, `docs/api.md` §8) for `wrong_document_type` - the type the document actually was, not
   * the one that was requested; `null` otherwise.
   */
  document_type: DocumentType | null
}

/** `POST /api/patient/requests/{case_id}/documents/file` → 200 (`docs/api.md` §4). */
export interface PdfUploadResponse {
  upload: UploadResult
  /** The patient view as it now stands - unchanged from before the upload for every code but `accepted`. */
  request: PatientView
}

// ---- Staff: Case Monitor --------------------------------------------------

/**
 * `GET /api/staff/cases` item (staff-fixes design Task 3): every column the Case Monitor
 * table shows, so the client renders each row straight from the list.
 */
export interface CaseSummary {
  case_id: string
  patient_id: string
  state: State
  intent: string | null
  safety_level: SafetyLevel | null
  escalation_kind: EscalationKind | null
  escalated_from_state: State | null
  created_at: IsoDateTime
  updated_at: IsoDateTime
  /**
   * Sub-project 19 (`docs/api.md` §5, §10): the case's LLM cost so far, a money string or
   * `null` under §10's NULL rule. Only the list carries it; the detail has `llm_usage`.
   */
  llm_cost_usd: Money | null
  /**
   * True when the case has at least one unpriced call and a non-null `llm_cost_usd`: the cost
   * shown is then a lower bound (§10), not the whole of it.
   */
  llm_cost_partial: boolean
  /** The case's unpriced attempts (§10), `0` for none: tells an unpriced `null` from "no attempt". */
  llm_unpriced_calls: number
}

/** `GET /api/staff/cases` → 200 (staff-fixes design Task 3): keyset-paginated. */
export interface CaseListPage {
  items: CaseSummary[]
  next_cursor: string | null
}

/** One step of the plan, as `ordered_steps` carries it (`docs/api.md` §5). */
export interface PlanStep {
  step: number
  action: string
}

/**
 * `GET /api/staff/cases/{case_id}`. `docs/api.md` §5 lists no `llm_cost_usd` /
 * `llm_cost_partial` / `llm_unpriced_calls` on the detail (it carries the fuller `llm_usage` instead), so those
 * list-only fields are left out here.
 */
export interface CaseDetail
  extends Omit<CaseSummary, 'llm_cost_usd' | 'llm_cost_partial' | 'llm_unpriced_calls'>,
    CaseAppointmentFacts {
  patient_id: string
  state_version: number
  intent: string | null
  safety_level: SafetyLevel | null
  identity_verified: boolean
  plan_hash: string | null
  ordered_steps: PlanStep[] | null
  current_step: number | null
  retry_cycle: number
  attempt_count: number
  required_documents: string[] | null
  held_documents: string[]
  escalated_from_state: State | null
  patient_deadline: IsoDateTime | null
  created_at: IsoDateTime
  /** Sub-project 19 (`docs/api.md` §5): always an object, `by_call` is `[]` with no attempt. */
  llm_usage: CaseLlmUsage
}

/**
 * Sub-project 18 (design D13, `docs/api.md` §5): the chosen and the resolved appointment,
 * its department and exam type, and the instruction source the case loaded - read-only,
 * staff-only, on both `CaseDetail` and `ReviewContext`. The first four are `null` on the
 * mock path and on a case that never resolved an appointment.
 */
export interface CaseAppointmentFacts {
  /** The appointment the patient chose, or `null` for "the nearest appointment". */
  appointment_id: string | null
  /** The appointment the appointment-service actually answered about. */
  answered_appointment_id: string | null
  department: string | null
  /** A Hebrew label; the API carries no exam code here. */
  exam_type_label: string | null
  instruction_source_id: string | null
  instruction_version: string | null
}
export interface AuditRecord {
  audit_id: number
  record_type: string
  event: string
  state_before: string | null
  state_after: string | null
  action: string | null
  /** Guard results and the evidence recorded with them (booleans, ids, hashes). */
  guards: Record<string, unknown>
  policy_result: string | null
  policy_reasons: string[]
  execution_id: string | null
  attempt_number: number | null
  retry_cycle: number | null
  approval_id: string | null
  rule_version: string
  recorded_at: IsoDateTime
}

// ---- Staff: Human Review --------------------------------------------------

export type Decision = 'approve' | 'resolve' | 'reject'
export type RequiredField = 'verified_identity_ref' | 'patient_deadline'

/** `GET /api/staff/reviews` item. */
export interface ReviewItem {
  case_id: string
  patient_id: string
  escalation_kind: EscalationKind
  escalated_from_state: State | null
  reasons: string[]
  allowed_decisions: Decision[]
  required_fields: RequiredField[]
  /**
   * When the case entered AwaitingHumanReview (staff-fixes design Task 5) - the queue's
   * order key, newest first. Replaces `updated_at`, which agreed with it on every case
   * that had never re-entered review, but meant the wrong thing for one that had.
   */
  entered_at: IsoDateTime
  /** Sub-project 15: a person has written to the patient - `approve` is never offered again. */
  human_engaged: boolean
  /** How the case last came back to review. */
  returned_by: 'patient_reply' | 'reply_timeout' | null
}

/** `GET /api/staff/reviews` → 200 (staff-fixes design Task 5): keyset-paginated. */
export interface ReviewQueuePage {
  items: ReviewItem[]
  next_cursor: string | null
}

/** A non-tombstoned Data Log entry in the review context. */
export interface DataLogEntry {
  entry_id: string
  kind: string
  content: string
  content_hash: string
  created_at: IsoDateTime
}

/** An Audit row as shown in the review context. */
export interface TraceRow {
  audit_id: number
  record_type: string
  event: string
  state_before: string | null
  state_after: string | null
  action: string | null
  policy_result: string | null
  policy_reasons: string[]
  recorded_at: IsoDateTime
  /** Guard results and policy evidence of the row (`{}` on a Blocked row). */
  guards: Record<string, boolean>
  outcome: 'success' | 'failed' | 'unknown' | null
  attempt_number: number | null
  retry_cycle: number | null
  execution_id: string | null
  approval_id: string | null
}

/** One patient upload attempt (`upload_attempts`, row 98). */
export interface UploadAttempt {
  kind: 'upload' | 'reply' | string
  /** The outcome code the patient was shown (`accepted`, `not_medical`, `document_service_unavailable`…). */
  outcome: string
  /** The detail behind it: the document-service's reason, or why it could not be reached. */
  reason: string | null
  created_at: IsoDateTime
}

/** One LLM attempt of a case (`llm_usage`), as the review context shows it. */
export interface LlmCall {
  call: string
  source: string
  outcome: string
  created_at: IsoDateTime
}

/** `GET /api/staff/cases/{case_id}/context`. */
export interface ReviewContext extends CaseAppointmentFacts {
  case_id: string
  patient_id: string
  state: State
  escalation_kind: EscalationKind | null
  escalated_from_state: State | null
  reasons: string[]
  data: DataLogEntry[]
  trace: TraceRow[]
  /** The case's latest classification (`cases.intent` / `safety_level`); the Audit row holds neither. */
  intent?: string | null
  safety_level?: string | null
  /** The case's LLM attempts in order - codes and times only. */
  llm_calls?: LlmCall[]
  /** Row 98: every upload attempt, including refused and unanswered ones - codes and times only. */
  upload_attempts?: UploadAttempt[]
  /** Must be sent back unchanged with the decision (409 `context_changed` otherwise). */
  shown_context_ref: string
}

/**
 * `POST /api/staff/cases/{case_id}/decision` body. The reviewer's identity and
 * role come from the token only: the client never sends `reviewer_id` or `reviewer_role`.
 */
export interface DecisionBody {
  decision: Decision
  reason: string
  shown_context_ref: string
  verified_identity_ref?: string
  /** ISO datetime with a timezone offset. */
  patient_deadline?: IsoDateTime
  /** Sub-project 15: a closing message, on resolve / reject only. */
  message?: MessageBody
}

/** `POST /api/staff/cases/{case_id}/decision` → 200. */
export interface DecisionResult {
  case_id: string
  state: State
}

/** `POST /api/staff/cases/{case_id}/answer` body (`docs/api.md` §5). */
export interface AnswerBody {
  /** The exact text the ContentApproval covers, 1-2000 characters. */
  answer: string
  /** Internal, like a decision's reason: recorded, never sent to the patient. */
  reason: string
  shown_context_ref: string
}

// ---- Staff requests to the patient (sub-project 15, docs/api.md §8) --------

/** A fixed template (with its parameter) or clinical staff's free text - exactly one. */
export interface MessageBody {
  template_id?: string
  param?: string
  text?: string
}

export interface MessageTemplate {
  template_id: string
  purpose: 'question' | 'document' | 'closing'
  text: string
  /** The `{placeholder}` the text takes, if any. */
  param: string | null
  /** The closed list for `param`: code -> Hebrew. */
  options: Record<string, string>
}

export interface PatientRequestBody extends MessageBody {
  kind: 'question' | 'document'
  reason: string
  shown_context_ref: string
  /** A catalog type - `kind: 'document'` only. */
  document_type?: DocumentType
  /** ISO datetime with a timezone offset; the server defaults to 24 hours. */
  deadline?: IsoDateTime
}

export interface ReplyRequest {
  kind: 'question' | 'document'
  message: string | null
  /** The requested catalog type for `kind: 'document'`, else `null`. */
  document_type: DocumentType | null
  deadline: IsoDateTime
}

export interface ConversationEntry {
  sender: 'staff' | 'patient'
  text: string
  at: IsoDateTime
}

// ---- Errors ---------------------------------------------------------------

/** FastAPI's error body: `{"detail": "<code>"}`, including for a `422`. */
export interface ErrorBody {
  detail: unknown
}

// ---- Admin metrics (sub-project 14) ---------------------------------------

/** Seconds; every field but `count` is null when nothing was measured. */
export interface Durations {
  count: number
  p50: number | null
  p95: number | null
  max: number | null
}

/** The cases opened in the window, in their current state. */
export interface MetricsFlow {
  opened: number
  by_state: Record<string, number>
  by_outcome: Record<string, number>
  completion: Record<string, Durations>
}

export interface MetricsHumanLoad {
  escalations_entered: number
  decisions: Record<string, number>
  decided_by_kind: Record<string, number>
  /** The queue now, whatever the window - as are `open_now` and `oldest_open_seconds`. */
  open_by_kind: Record<string, number>
  time_to_decision: Durations
  open_now: number
  oldest_open_seconds: number | null
}

export interface MetricsToolAction {
  action: string
  by_status: Record<string, number>
  success_rate: number | null
  latency: Durations
}

export interface MetricsFailureReason {
  outcome: string
  reason: string | null
  count: number
}

export interface MetricsTools {
  actions: MetricsToolAction[]
  failure_events: Record<string, number>
  failure_reasons: MetricsFailureReason[]
  retried_calls: number
  sources: Record<string, string | null>
}

export interface MetricsPatientSla {
  requests: number
  met: number
  breached: number
  other: number
  waiting: number
  rate: number | null
}

export interface MetricsPolicy {
  decisions: Record<string, number>
  blocked: number
  blocked_by_reason: Record<string, number>
  blocked_by_event: Record<string, number>
}

/** The presentation's success metrics. An appointment is (patient, the appointment the case is about). */
export interface MetricsSuccess {
  /** Appointments in the window that have passed: ready = a case resolved before the appointment. */
  readiness: { judged: number; ready: number; rate: number | null; upcoming: number; upcoming_ready: number }
  /** Cases opened in the window: opening to the first of an end or a hand-off to staff. */
  handling_time: { overall: Durations; closed: Durations; handed_off: Durations; open: number }
  /** Cases opened in the window, per appointment: every case after the first is a repeat. */
  repeat_requests: {
    appointments: number
    repeat_requests: number
    appointments_with_repeats: number
    max_requests: number
    no_appointment: number
  }
}

/** `GET /api/admin/metrics?from=&to=` → 200 (`docs/api.md` §7). */
export interface Metrics {
  window: { start: IsoDateTime; end: IsoDateTime }
  generated_at: IsoDateTime
  flow: MetricsFlow
  human_load: MetricsHumanLoad
  tools: MetricsTools
  patient_sla: MetricsPatientSla
  policy: MetricsPolicy
  /** Sub-project 19: exactly `GET /api/staff/llm-costs`'s body without its `window` (§7, §10). */
  llm: LlmCostSummary
  success: MetricsSuccess
}

// ---- LLM cost (sub-project 19, docs/api.md §10) ------------------------------
// Staff only: no patient type carries a cost, a token count or a model.

/**
 * Money: a decimal string with exactly 8 places, never a JSON number (`"0.00213400"`,
 * `"0.00000000"`), in USD. `null` wherever it appears follows §10's NULL rule.
 */
export type Money = string

/** One `by_call` entry: one per call code present, ordered by code. */
export interface LlmCallCost {
  /** `Intent`, `Safety`, `Planner`, `Evaluator`, `DocumentClassify` or `DocumentVision`. */
  call: string
  calls: number
  input_tokens: number
  output_tokens: number
  cost_usd: Money | null
}

/** One `by_source` entry: `agent` or `document_service`. */
export interface LlmSourceCost {
  source: string
  calls: number
  cost_usd: Money | null
}

/** `CaseDetail.llm_usage` (`docs/api.md` §5). */
export interface CaseLlmUsage {
  calls: number
  input_tokens: number
  cached_input_tokens: number
  output_tokens: number
  cost_usd: Money | null
  unpriced_calls: number
  by_call: LlmCallCost[]
}

/** The cohort of the cases opened in a window, with all of their usage (`docs/api.md` §10). */
export interface LlmCostSummary {
  cases: number
  cases_with_usage: number
  calls: number
  input_tokens: number
  cached_input_tokens: number
  output_tokens: number
  total_cost_usd: Money | null
  /** `null` when no cohort case has a priced row. */
  avg_cost_per_case_usd: Money | null
  /** `null` when no cohort case now in `Completed` has a priced row. */
  avg_cost_per_completed_case_usd: Money | null
  unpriced_calls: number
  by_call: LlmCallCost[]
  by_source: LlmSourceCost[]
}

/** `GET /api/staff/llm-costs?from=&to=` → 200 (`docs/api.md` §10). */
export interface LlmCosts extends LlmCostSummary {
  window: { start: IsoDateTime; end: IsoDateTime }
}

// ---- Appointments (sub-project 16, docs/api.md §9) --------------------------

export type AppointmentStatus = 'Scheduled' | 'Cancelled'

export interface Appointment {
  appointment_id: string
  appointment_at: IsoDateTime
  department: string
  doctor_name: string | null
  location: string | null
  status: AppointmentStatus
  required_documents: string[]
  /** Sub-project 18 (design D3): optional - an older appointment-service omits it. */
  exam_type?: AppointmentExamType | null
  /** Sub-project 18: the approved instruction's title only; the text is a separate read. */
  instruction?: AppointmentInstruction | null
}

export interface AppointmentExamType {
  code: string
  label: string
}

export interface AppointmentInstruction {
  source_id: string
  version: string
  title: string
}

/**
 * `GET /api/patient/instructions/{source_id}?version=` and the staff twin (sub-project 18,
 * design D12, `docs/api.md` §9): only a source the registry currently approves.
 */
export interface InstructionText {
  source_id: string
  version: string
  title: string
  text: string
}

export interface AppointmentList {
  from: IsoDateTime
  to: IsoDateTime
  appointments: Appointment[]
  truncated: boolean
}

// ---- System status (staff-fixes design Task 1, docs/api.md §5) -------------

export interface LlmStatus {
  last_ok_at: IsoDateTime | null
  last_error: string | null
  last_error_at: IsoDateTime | null
}

/** Row 98: the document-service as the staff banner sees it. */
export interface DocumentsStatus {
  configured: boolean
  /** `ok` | `degraded` | `unreachable`; `null` when not configured. */
  health: string | null
  last_ok_at: IsoDateTime | null
  last_error: string | null
  last_error_at: IsoDateTime | null
}

export interface SystemStatus {
  orchestrator: string | null
  llm: LlmStatus
  documents?: DocumentsStatus
}

/** `GET /api/admin/consistency` (docs/api.md §7). */
export interface ConsistencyQuery {
  property: string
  description: string
  result: 'unsat' | 'sat' | 'unknown' | string
  proved: boolean
}

export interface Consistency {
  engine: string
  all_proved: boolean
  queries: ConsistencyQuery[]
}
