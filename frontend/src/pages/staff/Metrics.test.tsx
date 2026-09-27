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
    opened: 13,
    by_state: { Completed: 7, Failed: 1, AwaitingHumanReview: 2, AwaitingPatientInput: 1, AwaitingPatientReply: 1, Planning: 1 },
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
    failure_reasons: [
      { outcome: 'ExecutionFailed', reason: 'tool:transient_failure:timeout', count: 3 },
      { outcome: 'ExecutionUnknown', reason: 'restart', count: 1 },
      { outcome: 'ExecutionUnknown', reason: 'exception:ValueError', count: 1 },
    ],
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
  llm: {
    cases: 5,
    cases_with_usage: 4,
    calls: 6,
    input_tokens: 7100,
    cached_input_tokens: 1000,
    output_tokens: 333,
    total_cost_usd: '0.00130360',
    avg_cost_per_case_usd: '0.00043453',
    avg_cost_per_completed_case_usd: '0.00050200',
    unpriced_calls: 2,
    by_call: [
      { call: 'DocumentClassify', calls: 1, input_tokens: 900, output_tokens: 40, cost_usd: null },
      { call: 'DocumentVision', calls: 1, input_tokens: 1200, output_tokens: 40, cost_usd: null },
      { call: 'Intent', calls: 2, input_tokens: 4000, output_tokens: 183, cost_usd: '0.00101960' },
      { call: 'Planner', calls: 1, input_tokens: 1000, output_tokens: 70, cost_usd: '0.00028400' },
      { call: 'Safety', calls: 1, input_tokens: 0, output_tokens: 0, cost_usd: '0.00000000' },
    ],
    by_source: [
      { source: 'agent', calls: 4, cost_usd: '0.00130360' },
      { source: 'document_service', calls: 2, cost_usd: null },
    ],
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
  it('shows the shared loading status while the metrics are still loading', async () => {
    vi.mocked(api.getMetrics).mockReturnValue(new Promise(() => {})) // never resolves
    render(<Metrics />)
    const status = await screen.findByText('טוען…')
    expect(status).toHaveAttribute('role', 'status')
    expect(status.closest('.loader')).toBeInTheDocument()
  })

  it('loads the last 7 days and shows every group', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(FIXTURE)
    render(<Metrics />)

    expect(await screen.findByRole('heading', { name: 'זרימת פניות' })).toBeInTheDocument()
    expect(spanOf(0)).toBe(7 * 24 * 3600_000)
    for (const heading of ['עומס על הצוות', 'כלים חיצוניים', 'SLA מטופל', 'מדיניות', 'עלות LLM']) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    }
    expect(tile('נפתחו')).toHaveTextContent('13')
    expect(tile('נדחו ע״י צוות')).toHaveTextContent('1')
    expect(tile('בטיפול')).toHaveTextContent('1') // 13 - 7 - 1 - 2 - 1 - 1
    expect(tile('ממתינות לתשובת מטופל')).toHaveTextContent('1')
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
    const sourceDd = screen.getByText('appointment-service').closest('dd') as HTMLElement
    expect(sourceDd.textContent?.replace(/\s+/g, ' ').trim()).toBe('שירות התורים appointment-service')
    expect(screen.getByText('לא דווח')).toBeInTheDocument()
  })

  it('labels each failure reason in pure Hebrew, the code once inside .mono', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(FIXTURE)
    render(<Metrics />)
    await screen.findByRole('heading', { name: 'זרימת פניות' })

    const codeText = (text: string) => {
      const matches = screen.getAllByText(text)
      expect(matches).toHaveLength(1)
      expect(matches[0]).toHaveClass('mono')
      return matches[0].closest('.metrics-bar-k')?.textContent?.replace(/\s+/g, ' ').trim()
    }

    expect(codeText('tool:transient_failure:timeout')).toBe('פסק זמן tool:transient_failure:timeout')
    expect(codeText('restart')).toBe('הפעלה מחדש באמצע קריאה (תוצאה לא ידועה) restart')
    expect(codeText('exception:ValueError')).toBe('תוצאה לא ידועה exception:ValueError')
  })

  it('shows a source code that is its own label just once, inside .mono', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue({
      ...FIXTURE,
      tools: { ...FIXTURE.tools, sources: { appointments: 'mock', documents: 'document-service' } },
    })
    render(<Metrics />)
    await screen.findByRole('heading', { name: 'זרימת פניות' })

    const mockMatches = screen.getAllByText('mock')
    expect(mockMatches).toHaveLength(1)
    expect(mockMatches[0]).toHaveClass('mono')

    expect(screen.getByText('שירות המסמכים')).toBeInTheDocument()
    expect(screen.getByText('document-service')).toHaveClass('mono')
  })

  it('refetches for a preset, keeping the previous numbers dimmed meanwhile', async () => {
    vi.mocked(api.getMetrics).mockResolvedValueOnce(FIXTURE).mockReturnValueOnce(new Promise(() => {}))
    const { container } = render(<Metrics />)
    await screen.findByRole('heading', { name: 'זרימת פניות' })

    await userEvent.click(screen.getByRole('button', { name: '24 שעות' }))
    expect(spanOf(1)).toBe(24 * 3600_000)
    expect(container.querySelector('.metrics-body')).toHaveClass('is-stale')
    expect(tile('נפתחו')).toHaveTextContent('13')
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

  it('shows the LLM cost of the window: totals, averages, tokens and the splits by call and by source', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(FIXTURE)
    render(<Metrics />)
    const group = (await screen.findByRole('heading', { name: 'עלות LLM' })).closest('.metrics-group') as HTMLElement

    expect(tile('עלות כוללת')).toHaveTextContent('$0.0013')
    // A cost beside unpriced calls is a lower bound (docs/api.md §10).
    expect(tile('עלות כוללת')).toHaveTextContent('לא כולל קריאות ללא מחיר ידוע')
    expect(tile('ממוצע לפנייה')).toHaveTextContent('$0.0004')
    expect(tile('ממוצע לפנייה שהושלמה')).toHaveTextContent('$0.0005')
    expect(tile('קריאות')).toHaveTextContent('6')
    expect(tile('קריאות ללא מחיר ידוע')).toHaveTextContent('2')
    expect(tile('טוקני קלט')).toHaveTextContent('7,100')
    expect(tile('טוקני קלט')).toHaveTextContent('1,000 מהמטמון')
    expect(tile('טוקני פלט')).toHaveTextContent('333')

    const rows = within(group).getAllByRole('row').slice(1)
    const cells = (row: HTMLElement) => within(row).getAllByRole('cell').map((cell) => cell.textContent?.replace(/\s+/g, ' ').trim())
    expect(rows.map(cells)).toEqual([
      ['סיווג מסמך DocumentClassify', '1', '900', '40', 'מחיר לא ידוע'],
      ['קריאת מסמך סרוק DocumentVision', '1', '1,200', '40', 'מחיר לא ידוע'],
      ['סיווג כוונה Intent', '2', '4,000', '183', '$0.0010'],
      ['תכנון Planner', '1', '1,000', '70', '$0.0003'],
      ['סיווג סיכון Safety', '1', '0', '0', '$0.0000'],
    ])
    expect(within(group).getByText('Intent')).toHaveClass('mono')

    const source = (code: string) => within(group).getByText(code).closest('.metrics-fact') as HTMLElement
    expect(within(group).getByText('agent')).toHaveClass('mono')
    expect(source('agent')).toHaveTextContent('הסוכן')
    expect(source('agent')).toHaveTextContent('4 קריאות · $0.0013')
    expect(source('document_service')).toHaveTextContent('מערכת המסמכים')
    expect(source('document_service')).toHaveTextContent('2 קריאות · מחיר לא ידוע')
  })

  it('renders a null total beside zero averages, and dashes what nothing was measured for', async () => {
    // One case with only priced API errors and one with only unpriced rows: the averages cover
    // the priced case only ("0.00000000"), the total is null with an unpriced call beside it.
    vi.mocked(api.getMetrics).mockResolvedValue({
      ...FIXTURE,
      llm: {
        ...FIXTURE.llm,
        cases: 2,
        cases_with_usage: 2,
        calls: 2,
        input_tokens: 900,
        cached_input_tokens: 0,
        output_tokens: 40,
        total_cost_usd: null,
        avg_cost_per_case_usd: '0.00000000',
        avg_cost_per_completed_case_usd: null,
        unpriced_calls: 1,
        by_call: [
          { call: 'DocumentClassify', calls: 1, input_tokens: 900, output_tokens: 40, cost_usd: null },
          { call: 'Intent', calls: 1, input_tokens: 0, output_tokens: 0, cost_usd: '0.00000000' },
        ],
        by_source: [
          { source: 'agent', calls: 1, cost_usd: '0.00000000' },
          { source: 'document_service', calls: 1, cost_usd: null },
        ],
      },
    })
    render(<Metrics />)
    await screen.findByRole('heading', { name: 'עלות LLM' })

    expect(tile('עלות כוללת')).toHaveTextContent('מחיר לא ידוע')
    expect(tile('עלות כוללת')).not.toHaveTextContent('לא כולל')
    expect(tile('ממוצע לפנייה')).toHaveTextContent('$0.0000')
    expect(tile('ממוצע לפנייה שהושלמה').querySelector('.metrics-tile-v')?.textContent).toBe('—')
  })

  it('says so when no LLM call was made in the window', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue({
      ...FIXTURE,
      llm: {
        ...FIXTURE.llm,
        cases: 0,
        cases_with_usage: 0,
        calls: 0,
        input_tokens: 0,
        cached_input_tokens: 0,
        output_tokens: 0,
        total_cost_usd: null,
        avg_cost_per_case_usd: null,
        avg_cost_per_completed_case_usd: null,
        unpriced_calls: 0,
        by_call: [],
        by_source: [],
      },
    })
    render(<Metrics />)
    const group = (await screen.findByRole('heading', { name: 'עלות LLM' })).closest('.metrics-group') as HTMLElement

    expect(tile('עלות כוללת').querySelector('.metrics-tile-v')?.textContent).toBe('—')
    expect(within(group).getAllByText('אין קריאות בטווח.')).toHaveLength(2)
  })

  it('keeps every dollar amount in its own LTR run (I2)', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue(FIXTURE)
    render(<Metrics />)
    const group = (await screen.findByRole('heading', { name: 'עלות LLM' })).closest('.metrics-group') as HTMLElement

    const ltr = (element: HTMLElement) => element.closest('[dir="ltr"]')
    expect(ltr(within(tile('עלות כוללת')).getByText('$0.0013'))).not.toBeNull()
    expect(ltr(within(tile('ממוצע לפנייה')).getByText('$0.0004'))).not.toBeNull()
    expect(ltr(within(tile('ממוצע לפנייה שהושלמה')).getByText('$0.0005'))).not.toBeNull()
    expect(ltr(within(group).getByText('$0.0010'))).not.toBeNull()
    for (const amount of within(group).getAllByText('$0.0013')) expect(ltr(amount)).not.toBeNull()
    // A Hebrew text is left in the page's own direction.
    for (const unknown of within(group).getAllByText('מחיר לא ידוע')) expect(ltr(unknown)).toBeNull()
  })

  it('says "unknown price" for a null average beside unpriced calls, "—" for one per completed case', async () => {
    vi.mocked(api.getMetrics).mockResolvedValue({
      ...FIXTURE,
      llm: {
        ...FIXTURE.llm,
        cases: 2,
        cases_with_usage: 1,
        calls: 1,
        input_tokens: 900,
        cached_input_tokens: 0,
        output_tokens: 40,
        total_cost_usd: null,
        avg_cost_per_case_usd: null,
        avg_cost_per_completed_case_usd: null,
        unpriced_calls: 1,
        by_call: [{ call: 'DocumentClassify', calls: 1, input_tokens: 900, output_tokens: 40, cost_usd: null }],
        by_source: [{ source: 'document_service', calls: 1, cost_usd: null }],
      },
    })
    render(<Metrics />)
    await screen.findByRole('heading', { name: 'עלות LLM' })

    expect(tile('ממוצע לפנייה').querySelector('.metrics-tile-v')?.textContent).toBe('מחיר לא ידוע')
    expect(tile('ממוצע לפנייה שהושלמה').querySelector('.metrics-tile-v')?.textContent).toBe('—')
  })
})
