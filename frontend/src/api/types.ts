/**
 * Request and response shapes of sub-project 5's `/api`.
 *
 * `docs/api.md` did not exist when this file was written, so the shapes are
 * derived from the sub-project 5 plan (`docs/superpowers/plans/2026-09-20-human-review-api.md`,
 * Task 2 dataclasses and Task 3 routes) and its design §6, plus the existing
 * Case Monitor schemas (`backend/hospital_agent/api/schemas.py`).
 * Keep every API type here so it is easy to align with `docs/api.md`.
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
  | 'in_review'
  | 'completed'
  | 'closed'

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
  /** The delivered outgoing message iff `status === 'completed'`. */
  message: string | null
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

// ---- Staff: Case Monitor --------------------------------------------------

/** `GET /api/staff/cases` item. */
export interface CaseSummary {
  case_id: string
  state: State
  escalation_kind: EscalationKind | null
  updated_at: IsoDateTime
}

/** `GET /api/staff/cases/{case_id}`. */
export interface CaseDetail extends CaseSummary {
  patient_id: string
  state_version: number
  intent: string | null
  safety_level: SafetyLevel | null
  identity_verified: boolean
  plan_hash: string | null
  ordered_steps: Array<Record<string, unknown>> | null
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
  guards: Record<string, boolean>
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
}

/** `POST /api/staff/cases/{case_id}/decision` → 200. */
export interface DecisionResult {
  case_id: string
  state: State
}

// ---- Errors ---------------------------------------------------------------

/** FastAPI's error body: `{"detail": "<code>"}` (422 bodies carry a list instead). */
export interface ErrorBody {
  detail: unknown
}
