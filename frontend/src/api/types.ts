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
   * `'file'` for a PDF picker sent to `POST .../documents/file` when the server is
   * configured with the document-service, `'text'` for the text form sent to
   * `POST .../documents` otherwise. The same for every case of a running server.
   */
  document_upload: 'file' | 'text'
  /** Sub-project 15: what the staff asked for, iff `status === 'needs_reply'`. */
  reply_request: ReplyRequest | null
  /** Staff messages the patient may see and the patient's replies, oldest first. */
  conversation: ConversationEntry[]
}

export interface CreateRequestBody {
  text: string
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

/** `GET /api/staff/cases/{case_id}`. */
export interface CaseDetail extends CaseSummary {
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
}

/** `GET /api/staff/cases/{case_id}/audit` item. */
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
  updated_at: IsoDateTime
  /** Sub-project 15: a person has written to the patient - `approve` is never offered again. */
  human_engaged: boolean
  /** How the case last came back to review. */
  returned_by: 'patient_reply' | 'reply_timeout' | null
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
}

/** `GET /api/staff/cases/{case_id}/context`. */
export interface ReviewContext {
  case_id: string
  patient_id: string
  state: State
  escalation_kind: EscalationKind | null
  escalated_from_state: State | null
  reasons: string[]
  data: DataLogEntry[]
  trace: TraceRow[]
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

/** `GET /api/admin/metrics?from=&to=` → 200 (`docs/api.md` §7). */
export interface Metrics {
  window: { start: IsoDateTime; end: IsoDateTime }
  generated_at: IsoDateTime
  flow: MetricsFlow
  human_load: MetricsHumanLoad
  tools: MetricsTools
  patient_sla: MetricsPatientSla
  policy: MetricsPolicy
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

export interface SystemStatus {
  orchestrator: string | null
  llm: LlmStatus
}
