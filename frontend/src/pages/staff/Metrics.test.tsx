import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import type { Metrics as MetricsData } from '../../api/types'
import { Metrics } from './Metrics'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  getMetrics: vi.fn(),
}))

const NONE = { count: 0, p50: null, p95: null, max: null }

const FIXTURE: MetricsData = {
  window: { start: '2026-09-17T00:00:00Z', end: '2026-09-24T00:00:00Z' },
  generated_at: '2026-09-24T10:00:00Z',
  flow: {
    opened: 12,
    by_state: { Completed: 7, Failed: 1, AwaitingHumanReview: 2, AwaitingPatientInput: 1, Planning: 1 },
    by_outcome: { AppointmentPreparation: 8, MedicalQuestion: 3, SomethingNew: 1 },
    completion: { CASE_RESOLVED: { count: 6, p50: 2.6, p95: 3.1, max: 3.4 }, HUMAN_RESOLVED_CASE: NONE },
  },
  human_load: {
    escalations_entered: 5,
    decisions: { HUMAN_APPROVED: 1, HUMAN_RESOLVED_CASE: 1, HUMAN_REJECTED: 1 },
    decided_by_kind: { MedicalQuestion: 2, TemporalViolation: 1 },
    open_by_kind: { RetryExhausted: 2 },
    time_to_decision: { count: 3, p50: 600, p95: 1740, max: 1800 },
    open_now: 2,
    oldest_open_seconds: 7200,
  },
  tools: {
    actions: [
      {
        action: 'CheckDocuments',
        by_status: { succeeded: 2, failed: 3 },
        success_rate: 0.4,
        latency: { count: 5, p50: 0.008, p95: 0.009, max: 0.009 },
      },
    ],
    failure_events: { TOOL_TRANSIENT_FAILURE: 2, RETRY_EXHAUSTED: 1 },
    failure_reasons: [{ outcome: 'ExecutionFailed', reason: 'tool:transient_failure:timeout', count: 3 }],
    retried_calls: 2,
    sources: { appointments: 'appointment-service', documents: null },
  },
  patient_sla: { requests: 5, met: 3, breached: 2, other: 0, waiting: 0, rate: 0.6 },
  policy: {
    decisions: { POLICY_ALLOWED: 11, POLICY_DENIED: 1, POLICY_HUMAN_REVIEW_REQUIRED: 0 },
    blocked: 2,
    blocked_by_reason: { guard_failed: 2 },
    blocked_by_event: { HUMAN_APPROVED: 2 },
  },
}

function tile(label: string): HTMLElement {
  return screen.getByText(label, { selector: '.metrics-tile-k' }).closest('.metrics-tile') as HTMLElement
}

function spanOf(call: number): number {
  const [start, end] = vi.mocked(api.getMetrics).mock.calls[call]
  return end.getTime() - start.getTime()
}

/** A promise this test controls, to make two requests resolve out of send order. */
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

beforeEach(() => {
  vi.mocked(api.getMetrics).mockReset()
})

describe('Metrics', () => {
  it('loads the last 7 days and shows every group', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(FIXTURE)
    render(<Metrics />)

    expect(await screen.findByRole('heading', { name: 'זרימת פניות' })).toBeInTheDocument()
    expect(spanOf(0)).toBe(7 * 24 * 3600_000)
    for (const heading of ['עומס על הצוות', 'כלים חיצוניים', 'SLA מטופל', 'מדיניות']) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    }
    expect(tile('נפתחו')).toHaveTextContent('12')
    expect(tile('נדחו ע״י צוות')).toHaveTextContent('1')
    expect(tile('בטיפול')).toHaveTextContent('1') // 12 - 7 - 1 - 2 - 1
    expect(tile('פתוחות עכשיו')).toHaveTextContent('2')
    expect(tile('פתוחות עכשיו')).toHaveTextContent('2.0 h')
    expect(tile('עמידה בזמן')).toHaveTextContent('60%')
    expect(tile('חסימות')).toHaveTextContent('2')
  })

  it('shows a code the screen has no label for as itself', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(FIXTURE)
    render(<Metrics />)
    expect(await screen.findByText('SomethingNew')).toBeInTheDocument()
  })

  it('lists each tool with its calls, success rate and latency', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(FIXTURE)
    render(<Metrics />)
    const row = (await screen.findByText('CheckDocuments')).closest('tr') as HTMLElement
    const cells = within(row).getAllByRole('cell')
    expect(cells.map((cell) => cell.textContent)).toEqual(['CheckDocuments', '5', '2', '3', '0', '40%', '8 ms', '9 ms', '9 ms'])
    expect(screen.getByText('שירות התורים')).toBeInTheDocument()
    expect(screen.getByText('appointment-service')).toHaveClass('mono')
    expect(screen.getByText('לא דווח')).toBeInTheDocument()
  })

  it('refetches for a preset, keeping the previous numbers dimmed meanwhile', async () => {
    vi.mocked(api.getMetrics).mockResolvedValueOnce(FIXTURE).mockReturnValueOnce(new Promise(() => {}))
    const { container } = render(<Metrics />)
    await screen.findByRole('heading', { name: 'זרימת פניות' })

    await userEvent.click(screen.getByRole('button', { name: '24 שעות' }))
    expect(spanOf(1)).toBe(24 * 3600_000)
    expect(container.querySelector('.metrics-body')).toHaveClass('is-stale')
    expect(tile('נפתחו')).toHaveTextContent('12')
  })

  it('lets only the most recently requested range land, even when an older request resolves later', async () => {
    const d24 = deferred<MetricsData>()
    const d30 = deferred<MetricsData>()
    vi.mocked(api.getMetrics)
      .mockResolvedValueOnce(FIXTURE)
      .mockReturnValueOnce(d24.promise)
      .mockReturnValueOnce(d30.promise)
    const { container } = render(<Metrics />)
    await screen.findByRole('heading', { name: 'זרימת פניות' })

    await userEvent.click(screen.getByRole('button', { name: '24 שעות' }))
    await userEvent.click(screen.getByRole('button', { name: '30 יום' }))

    d30.resolve({ ...FIXTURE, flow: { ...FIXTURE.flow, opened: 30 } })
    await waitFor(() => expect(tile('נפתחו')).toHaveTextContent('30'))

    d24.resolve({ ...FIXTURE, flow: { ...FIXTURE.flow, opened: 24 } })
    await waitFor(() => expect(container.querySelector('.metrics-body')).not.toHaveClass('is-stale'))
    expect(tile('נפתחו')).toHaveTextContent('30')
  })

  it('shows a custom range when submitted', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(FIXTURE)
    render(<Metrics />)
    await screen.findByRole('heading', { name: 'זרימת פניות' })

    fireEvent.change(screen.getByLabelText('מ־'), { target: { value: '2026-09-01T08:00' } })
    fireEvent.change(screen.getByLabelText('עד'), { target: { value: '2026-09-02T08:00' } })
    await userEvent.click(screen.getByRole('button', { name: 'הצג' }))

    const [start, end] = vi.mocked(api.getMetrics).mock.calls[1]
    expect(start.getTime()).toBe(new Date('2026-09-01T08:00').getTime())
    expect(end.getTime()).toBe(new Date('2026-09-02T08:00').getTime())
  })

  it('explains a refusal, shows its code and retries', async () => {
    vi.mocked(api.getMetrics).mockRejectedValueOnce(new ApiError(403, 'admin_only')).mockResolvedValueOnce(FIXTURE)
    render(<Metrics />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('אין הרשאה לצפות במדדים.')
    expect(alert).toHaveTextContent('admin_only')

    await userEvent.click(within(alert).getByRole('button', { name: 'נסה שוב' }))
    expect(await screen.findByRole('heading', { name: 'זרימת פניות' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
