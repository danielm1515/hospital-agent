import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from './client'
import { ApiError } from './client'

type FetchMock = ReturnType<typeof vi.fn>

function jsonResponse(status: number, body: unknown): Response {
  return new Response(body === undefined ? '' : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

let fetchMock: FetchMock

function mockOnce(status: number, body?: unknown) {
  fetchMock.mockResolvedValueOnce(jsonResponse(status, body))
}

function lastCall(): [string, RequestInit] {
  const call = fetchMock.mock.calls.at(-1)!
  return [call[0] as string, call[1] as RequestInit]
}

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  api.clearToken()
  api.setOnUnauthorized(null)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('token handling', () => {
  it('stores the token from a successful login and sends it afterwards', async () => {
    mockOnce(200, { token: 'tok-1', user_id: 'P-10041', role: 'patient', display_name: 'דנה כהן' })
    const result = await api.login('P-10041', 'demo')
    expect(result.role).toBe('patient')
    expect(api.getToken()).toBe('tok-1')
    const [loginUrl, loginInit] = lastCall()
    expect(loginUrl).toBe('/api/auth/login')
    expect(loginInit.method).toBe('POST')
    expect(JSON.parse(loginInit.body as string)).toEqual({ user_id: 'P-10041', password: 'demo' })
    expect((loginInit.headers as Record<string, string>).Authorization).toBeUndefined()

    mockOnce(200, { user_id: 'P-10041', role: 'patient', display_name: 'דנה כהן' })
    await api.me()
    const [meUrl, meInit] = lastCall()
    expect(meUrl).toBe('/api/auth/me')
    expect((meInit.headers as Record<string, string>).Authorization).toBe('Bearer tok-1')
  })

  it('clears the token on logout', () => {
    api.setToken('tok-1')
    api.logout()
    expect(api.getToken()).toBeNull()
  })
})

describe('errors', () => {
  it('maps a non-2xx response to ApiError with the detail code', async () => {
    api.setToken('tok-1')
    mockOnce(409, { detail: 'context_changed' })
    const error = await api
      .decide('CASE-1', { decision: 'approve', reason: 'ok', shown_context_ref: 'ctx-1' })
      .catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(409)
    expect((error as ApiError).detail).toBe('context_changed')
  })

  it('maps a 422 validation body to validation_error', async () => {
    api.setToken('tok-1')
    mockOnce(422, { detail: [{ loc: ['body', 'text'], msg: 'too short' }] })
    const error = (await api.createRequest('').catch((caught: unknown) => caught)) as ApiError
    expect(error.status).toBe(422)
    expect(error.detail).toBe('validation_error')
  })

  it('maps a failed fetch to a network error', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('offline'))
    const error = (await api.listRequests().catch((caught: unknown) => caught)) as ApiError
    expect(error.status).toBe(0)
    expect(error.detail).toBe('network_error')
  })

  it('clears the token and calls onUnauthorized on a 401', async () => {
    api.setToken('tok-1')
    const onUnauthorized = vi.fn()
    api.setOnUnauthorized(onUnauthorized)
    mockOnce(401, { detail: 'not_authenticated' })
    const error = (await api.listRequests().catch((caught: unknown) => caught)) as ApiError
    expect(error.status).toBe(401)
    expect(api.getToken()).toBeNull()
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })

  it('treats a failed login as invalid credentials, not an ended session', async () => {
    const onUnauthorized = vi.fn()
    api.setOnUnauthorized(onUnauthorized)
    mockOnce(401, { detail: 'invalid_credentials' })
    const error = (await api.login('P-10041', 'wrong').catch((caught: unknown) => caught)) as ApiError
    expect(error.detail).toBe('invalid_credentials')
    expect(onUnauthorized).not.toHaveBeenCalled()
  })
})

describe('patient routes', () => {
  beforeEach(() => api.setToken('tok-1'))

  it('creates a request', async () => {
    mockOnce(201, { case_id: 'CASE-1' })
    await api.createRequest('מתי התור שלי?')
    const [url, init] = lastCall()
    expect(url).toBe('/api/patient/requests')
    expect(JSON.parse(init.body as string)).toEqual({ text: 'מתי התור שלי?' })
  })

  it('lists and reads requests', async () => {
    mockOnce(200, [])
    await api.listRequests()
    expect(lastCall()[0]).toBe('/api/patient/requests')

    mockOnce(200, { case_id: 'CASE-1' })
    await api.getRequest('CASE-1')
    expect(lastCall()[0]).toBe('/api/patient/requests/CASE-1')
  })

  it('uploads a document with exactly the three body fields', async () => {
    mockOnce(200, { case_id: 'CASE-1' })
    await api.uploadDocument('CASE-1', { document_id: 'blood_test', format: 'pdf', content: 'x' })
    const [url, init] = lastCall()
    expect(url).toBe('/api/patient/requests/CASE-1/documents')
    expect(JSON.parse(init.body as string)).toEqual({
      document_id: 'blood_test',
      format: 'pdf',
      content: 'x',
    })
  })

  it('uploads a PDF as multipart form data, with the file and the bearer token', async () => {
    mockOnce(200, { upload: { code: 'accepted', document_type: 'CBC' }, request: { case_id: 'CASE-1' } })
    const file = new File(['%PDF-1.4 ...'], 'results.pdf', { type: 'application/pdf' })
    const result = await api.uploadDocumentFile('CASE-1', file)

    expect(result).toEqual({ upload: { code: 'accepted', document_type: 'CBC' }, request: { case_id: 'CASE-1' } })
    const [url, init] = lastCall()
    expect(url).toBe('/api/patient/requests/CASE-1/documents/file')
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-1')
    // The browser sets the multipart boundary itself: no Content-Type is set by hand.
    expect((init.headers as Record<string, string>)['Content-Type']).toBeUndefined()
    expect(init.body).toBeInstanceOf(FormData)
    const sent = (init.body as FormData).get('file')
    expect(sent).toBeInstanceOf(File)
    expect((sent as File).name).toBe('results.pdf')
  })
})

describe('staff routes', () => {
  beforeEach(() => api.setToken('tok-1'))

  it('filters the monitor by state', async () => {
    mockOnce(200, [])
    await api.listCases()
    expect(lastCall()[0]).toBe('/api/staff/cases')

    mockOnce(200, [])
    await api.listCases('AwaitingHumanReview')
    expect(lastCall()[0]).toBe('/api/staff/cases?state=AwaitingHumanReview')
  })

  it('reads a case, its audit, the queue and the context', async () => {
    mockOnce(200, {})
    await api.getCase('CASE-1')
    expect(lastCall()[0]).toBe('/api/staff/cases/CASE-1')

    mockOnce(200, [])
    await api.getAudit('CASE-1')
    expect(lastCall()[0]).toBe('/api/staff/cases/CASE-1/audit')

    mockOnce(200, [])
    await api.listReviews()
    expect(lastCall()[0]).toBe('/api/staff/reviews')

    mockOnce(200, {})
    await api.getContext('CASE-1')
    expect(lastCall()[0]).toBe('/api/staff/cases/CASE-1/context')
  })

  it('sends a decision without any reviewer identity', async () => {
    mockOnce(200, { case_id: 'CASE-1', state: 'Received' })
    await api.decide('CASE-1', {
      decision: 'approve',
      reason: 'זוהה טלפונית',
      shown_context_ref: 'ctx-abc',
      verified_identity_ref: 'ID-9',
    })
    const [url, init] = lastCall()
    expect(url).toBe('/api/staff/cases/CASE-1/decision')
    const body = JSON.parse(init.body as string)
    expect(body).toEqual({
      decision: 'approve',
      reason: 'זוהה טלפונית',
      shown_context_ref: 'ctx-abc',
      verified_identity_ref: 'ID-9',
    })
    expect(body.reviewer_id).toBeUndefined()
    expect(body.reviewer_role).toBeUndefined()
    expect(body.patient_id).toBeUndefined()
  })

  it('tombstones a Data Log entry', async () => {
    // jsdom's Response refuses status 204, so stand in for an empty response.
    fetchMock.mockResolvedValueOnce({ ok: true, status: 204, text: async () => '' } as Response)
    await expect(api.tombstone('CASE-1', 'entry-7')).resolves.toBeUndefined()
    const [url, init] = lastCall()
    expect(url).toBe('/api/staff/cases/CASE-1/data/entry-7')
    expect(init.method).toBe('DELETE')
  })
})

describe('patient requests (sub-project 15)', () => {
  beforeEach(() => api.setToken('tok-1'))

  it('posts a staff request to the case', async () => {
    mockOnce(200, { case_id: 'C-1', state: 'AwaitingPatientReply' })
    await api.requestFromPatient('C-1', {
      kind: 'question',
      template_id: 'clarify_general',
      reason: 'unclear',
      shown_context_ref: 'ref-1',
    })
    const [url, init] = lastCall()
    expect(url).toBe('/api/staff/cases/C-1/request')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toMatchObject({ kind: 'question', template_id: 'clarify_general' })
  })

  it('posts the patient text reply', async () => {
    mockOnce(200, {})
    await api.replyToRequest('C-1', 'כן')
    const [url, init] = lastCall()
    expect(url).toBe('/api/patient/requests/C-1/reply')
    expect(JSON.parse(init.body as string)).toEqual({ text: 'כן' })
  })

  it('posts the requested PDF as multipart', async () => {
    mockOnce(200, {})
    await api.replyWithFile('C-1', new File(['%PDF'], 'a.pdf', { type: 'application/pdf' }))
    const [url, init] = lastCall()
    expect(url).toBe('/api/patient/requests/C-1/reply/file')
    expect(init.body).toBeInstanceOf(FormData)
  })

  it('reads the message templates', async () => {
    mockOnce(200, [])
    await api.getMessageTemplates()
    expect(lastCall()[0]).toBe('/api/staff/message-templates')
  })
})

describe('getMetrics', () => {
  it('sends the window as ISO instants, URL-encoded', async () => {
    mockOnce(200, {})
    await api.getMetrics(new Date('2026-09-17T00:00:00Z'), new Date('2026-09-24T00:00:00Z'))
    const [url, init] = lastCall()
    expect(url).toBe(
      '/api/admin/metrics?from=2026-09-17T00%3A00%3A00.000Z&to=2026-09-24T00%3A00%3A00.000Z',
    )
    expect(init.method).toBe('GET')
  })
})
