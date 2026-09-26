/**
 * The one client for sub-project 5's `/api`.
 *
 * - The bearer token lives in `sessionStorage` (design §2), with an in-memory
 *   fallback when storage is unavailable.
 * - Identity comes from the token only: no call sends `patient_id`,
 *   `reviewer_id` or `reviewer_role`.
 * - A non-2xx response throws `ApiError {status, detail}`. A 401 on an
 *   authenticated call also clears the token and calls the `onUnauthorized` hook.
 */
import type {
  AnswerBody,
  AppointmentList,
  AuditRecord,
  CaseDetail,
  CaseListPage,
  DecisionBody,
  DecisionResult,
  EscalationKind,
  LoginResponse,
  Me,
  Metrics,
  MessageBody,
  MessageTemplate,
  PatientRequestBody,
  PatientView,
  PdfUploadResponse,
  ReviewContext,
  ReviewItem,
  ReviewQueuePage,
  State,
  StateGroup,
  SystemStatus,
  UploadDocumentBody,
} from './types'

const BASE = '/api'
const TOKEN_KEY = 'ramon-token'

export class ApiError extends Error {
  readonly status: number
  /** The `detail` code from `{"detail": "<code>"}`, or a synthetic code. */
  readonly detail: string
  /** The raw response body, when it was JSON. */
  readonly body: unknown

  constructor(status: number, detail: string, body?: unknown) {
    super(`${status} ${detail}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.body = body
  }
}

// ---- Token ----------------------------------------------------------------

let memoryToken: string | null = null

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return memoryToken
  }
}

export function setToken(token: string): void {
  memoryToken = token
  try {
    sessionStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* storage unavailable: keep the in-memory copy */
  }
}

export function clearToken(): void {
  memoryToken = null
  try {
    sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable */
  }
}

let unauthorizedHook: (() => void) | null = null

/** Registers the callback run after a 401 cleared the token (AuthContext logs out). */
export function setOnUnauthorized(hook: (() => void) | null): void {
  unauthorizedHook = hook
}

// ---- Transport ------------------------------------------------------------

type Method = 'GET' | 'POST' | 'DELETE'

interface RequestOptions {
  /** A JSON-serializable body, or a `FormData` body (sent as-is, no `Content-Type` set). */
  body?: unknown
  /** Send the bearer token and treat a 401 as an ended session. Default true. */
  auth?: boolean
}

function detailOf(status: number, body: unknown): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (status === 422) return 'validation_error'
  }
  return `http_${status}`
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return undefined
  try {
    return JSON.parse(text)
  } catch {
    return undefined
  }
}

async function request<T>(method: Method, path: string, options: RequestOptions = {}): Promise<T> {
  const auth = options.auth ?? true
  const isFormData = options.body instanceof FormData
  const headers: Record<string, string> = { Accept: 'application/json' }
  // A `FormData` body sets its own multipart `Content-Type` with the boundary; the
  // browser only does that when we leave the header unset.
  if (options.body !== undefined && !isFormData) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = getToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  let response: Response
  try {
    response = await fetch(BASE + path, {
      method,
      headers,
      body:
        options.body === undefined
          ? undefined
          : isFormData
            ? (options.body as FormData)
            : JSON.stringify(options.body),
    })
  } catch {
    throw new ApiError(0, 'network_error')
  }

  const body = await readJson(response)
  if (!response.ok) {
    if (response.status === 401 && auth) {
      clearToken()
      unauthorizedHook?.()
    }
    throw new ApiError(response.status, detailOf(response.status, body), body)
  }
  return body as T
}

const id = encodeURIComponent

/** Copies only the `MessageBody` fields that are actually set. */
function messagePayload(message: MessageBody): MessageBody {
  const payload: MessageBody = {}
  if (message.template_id !== undefined) payload.template_id = message.template_id
  if (message.param !== undefined) payload.param = message.param
  if (message.text !== undefined) payload.text = message.text
  return payload
}

// ---- Auth -----------------------------------------------------------------

/** Logs in and stores the token. A 401 here is `invalid_credentials`, not an ended session. */
export async function login(user_id: string, password: string): Promise<LoginResponse> {
  const result = await request<LoginResponse>('POST', '/auth/login', {
    body: { user_id, password },
    auth: false,
  })
  setToken(result.token)
  return result
}

export function me(): Promise<Me> {
  return request<Me>('GET', '/auth/me')
}

export function logout(): void {
  clearToken()
}

// ---- Patient --------------------------------------------------------------

export function createRequest(text: string): Promise<PatientView> {
  return request<PatientView>('POST', '/patient/requests', { body: { text } })
}

export function listRequests(): Promise<PatientView[]> {
  return request<PatientView[]>('GET', '/patient/requests')
}

export function getRequest(caseId: string): Promise<PatientView> {
  return request<PatientView>('GET', `/patient/requests/${id(caseId)}`)
}

export function uploadDocument(caseId: string, body: UploadDocumentBody): Promise<PatientView> {
  const { document_id, format, content } = body
  return request<PatientView>('POST', `/patient/requests/${id(caseId)}/documents`, {
    body: { document_id, format, content },
  })
}

/**
 * Sub-project 13 (`docs/api.md` §4): uploads a document (PDF, JPEG or PNG) as `multipart/form-data`, one part
 * named `file` carrying the filename. Offered only when `document_upload === 'file'`.
 */
export function uploadDocumentFile(caseId: string, file: File): Promise<PdfUploadResponse> {
  const formData = new FormData()
  formData.append('file', file)
  return request<PdfUploadResponse>('POST', `/patient/requests/${id(caseId)}/documents/file`, {
    body: formData,
  })
}

// ---- Staff ----------------------------------------------------------------

/**
 * `GET /api/staff/cases` (staff-fixes design Task 3/4): one call, keyset-paginated,
 * optionally filtered by an exact `state` or by one of the five `group`s (`group: 'staff'`
 * also accepts `escalationKind`). `options.cursor` asks for the page after the previous
 * response's `next_cursor`.
 */
export function listCases(
  options: { state?: State; group?: StateGroup; escalationKind?: EscalationKind; cursor?: string; limit?: number } = {},
): Promise<CaseListPage> {
  const params = new URLSearchParams()
  if (options.state) params.set('state', options.state)
  if (options.group) params.set('group', options.group)
  if (options.escalationKind) params.set('escalation_kind', options.escalationKind)
  if (options.cursor) params.set('cursor', options.cursor)
  if (options.limit) params.set('limit', String(options.limit))
  const query = params.toString()
  return request<CaseListPage>('GET', `/staff/cases${query ? `?${query}` : ''}`)
}

export function getCase(caseId: string): Promise<CaseDetail> {
  return request<CaseDetail>('GET', `/staff/cases/${id(caseId)}`)
}

export function getAudit(caseId: string): Promise<AuditRecord[]> {
  return request<AuditRecord[]>('GET', `/staff/cases/${id(caseId)}/audit`)
}

/**
 * `GET /api/staff/reviews` (staff-fixes design Task 5): one call, keyset-paginated, newest
 * entry into AwaitingHumanReview first. `options.cursor` asks for the page after the
 * previous response's `next_cursor`.
 */
export function listReviews(options: { cursor?: string; limit?: number } = {}): Promise<ReviewQueuePage> {
  const params = new URLSearchParams()
  if (options.cursor) params.set('cursor', options.cursor)
  if (options.limit) params.set('limit', String(options.limit))
  const query = params.toString()
  return request<ReviewQueuePage>('GET', `/staff/reviews${query ? `?${query}` : ''}`)
}

/**
 * `GET /api/staff/reviews/{case_id}` (staff-fixes design Task 3): one queue item, so
 * `ReviewCase` does not fetch the whole queue just to find its own row. `404
 * not_in_review` when the case is not (or no longer) in `AwaitingHumanReview`.
 */
export function getReviewItem(caseId: string): Promise<ReviewItem> {
  return request<ReviewItem>('GET', `/staff/reviews/${id(caseId)}`)
}

export function getContext(caseId: string): Promise<ReviewContext> {
  return request<ReviewContext>('GET', `/staff/cases/${id(caseId)}/context`)
}

/** Sends only the fields of `DecisionBody`; the reviewer comes from the token. */
export function decide(caseId: string, body: DecisionBody): Promise<DecisionResult> {
  const payload: DecisionBody = {
    decision: body.decision,
    reason: body.reason,
    shown_context_ref: body.shown_context_ref,
  }
  if (body.verified_identity_ref !== undefined) payload.verified_identity_ref = body.verified_identity_ref
  if (body.patient_deadline !== undefined) payload.patient_deadline = body.patient_deadline
  if (body.message !== undefined) payload.message = messagePayload(body.message)
  return request<DecisionResult>('POST', `/staff/cases/${id(caseId)}/decision`, { body: payload })
}

/** Sends only the fields of `AnswerBody`; the reviewer comes from the token. */
export function answer(caseId: string, body: AnswerBody): Promise<DecisionResult> {
  const payload: AnswerBody = {
    answer: body.answer,
    reason: body.reason,
    shown_context_ref: body.shown_context_ref,
  }
  return request<DecisionResult>('POST', `/staff/cases/${id(caseId)}/answer`, { body: payload })
}

export async function tombstone(caseId: string, entryId: string): Promise<void> {
  await request<void>('DELETE', `/staff/cases/${id(caseId)}/data/${id(entryId)}`)
}

// ---- Staff requests to the patient (sub-project 15) ------------------------

export function getMessageTemplates(): Promise<MessageTemplate[]> {
  return request<MessageTemplate[]>('GET', '/staff/message-templates')
}

/** Sends only the fields of `PatientRequestBody`; the reviewer comes from the token. */
export function requestFromPatient(caseId: string, body: PatientRequestBody): Promise<DecisionResult> {
  const payload: PatientRequestBody = {
    kind: body.kind,
    reason: body.reason,
    shown_context_ref: body.shown_context_ref,
  }
  if (body.template_id !== undefined) payload.template_id = body.template_id
  if (body.param !== undefined) payload.param = body.param
  if (body.text !== undefined) payload.text = body.text
  if (body.document_type !== undefined) payload.document_type = body.document_type
  if (body.deadline !== undefined) payload.deadline = body.deadline
  return request<DecisionResult>('POST', `/staff/cases/${id(caseId)}/request`, { body: payload })
}

export function replyToRequest(caseId: string, text: string): Promise<PatientView> {
  return request<PatientView>('POST', `/patient/requests/${id(caseId)}/reply`, { body: { text } })
}

export function replyWithFile(caseId: string, file: File): Promise<PdfUploadResponse> {
  const form = new FormData()
  form.append('file', file)
  return request<PdfUploadResponse>('POST', `/patient/requests/${id(caseId)}/reply/file`, { body: form })
}

// ---- Admin (sub-project 14) ------------------------------------------------

/** `GET /api/admin/metrics` - admin_staff only. `from` inclusive, `to` exclusive. */
export function getMetrics(from: Date, to: Date): Promise<Metrics> {
  const query = new URLSearchParams({ from: from.toISOString(), to: to.toISOString() })
  return request<Metrics>('GET', `/admin/metrics?${query.toString()}`)
}

// ---- Appointments (sub-project 16) -------------------------------------------

function windowQuery(from: Date, to: Date): string {
  return new URLSearchParams({ from: from.toISOString(), to: to.toISOString() }).toString()
}

/** `GET /api/patient/appointments` - the token's patient. `from` inclusive, `to` exclusive. */
export function listMyAppointments(from: Date, to: Date): Promise<AppointmentList> {
  return request<AppointmentList>('GET', `/patient/appointments?${windowQuery(from, to)}`)
}

/** `GET /api/staff/cases/{case_id}/appointments` - the case's patient. */
export function listCaseAppointments(caseId: string, from: Date, to: Date): Promise<AppointmentList> {
  return request<AppointmentList>('GET', `/staff/cases/${id(caseId)}/appointments?${windowQuery(from, to)}`)
}

// ---- System status (staff-fixes design Task 1) ------------------------------

/** `GET /api/staff/system-status` - staff only: whether the Agent Orchestrator runs and the
 * LLM's last outcome. */
export function getSystemStatus(): Promise<SystemStatus> {
  return request<SystemStatus>('GET', '/staff/system-status')
}
