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
  AuditRecord,
  CaseDetail,
  CaseSummary,
  DecisionBody,
  DecisionResult,
  LoginResponse,
  Me,
  PatientView,
  ReviewContext,
  ReviewItem,
  State,
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
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = getToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  let response: Response
  try {
    response = await fetch(BASE + path, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
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

// ---- Staff ----------------------------------------------------------------

export function listCases(state?: State): Promise<CaseSummary[]> {
  const query = state ? `?state=${id(state)}` : ''
  return request<CaseSummary[]>('GET', `/staff/cases${query}`)
}

export function getCase(caseId: string): Promise<CaseDetail> {
  return request<CaseDetail>('GET', `/staff/cases/${id(caseId)}`)
}

export function getAudit(caseId: string): Promise<AuditRecord[]> {
  return request<AuditRecord[]>('GET', `/staff/cases/${id(caseId)}/audit`)
}

export function listReviews(): Promise<ReviewItem[]> {
  return request<ReviewItem[]>('GET', '/staff/reviews')
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
