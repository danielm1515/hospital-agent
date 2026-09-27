import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import type { CaseDetail, CaseListPage, CaseSummary, LlmCosts, ReviewContext } from '../../api/types'
import { CaseMonitor } from './CaseMonitor'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  listCases: vi.fn(),
  getCase: vi.fn(),
  getContext: vi.fn(),
  getLlmCosts: vi.fn(),
}))

const DONE: CaseSummary = {
  case_id: 'CASE-23FE645294B7',
  patient_id: 'P-10041',
  state: 'Completed',
  intent: 'AppointmentPreparation',
  safety_level: 'MediumRisk',
  escalation_kind: null,
  escalated_from_state: null,
  created_at: '2026-09-19T22:12:38.560531Z',
  updated_at: '2026-09-19T22:12:48.986200Z',
  llm_cost_usd: '0.00213400',
  llm_cost_partial: false,
  llm_unpriced_calls: 0,
}

const IN_REVIEW: CaseSummary = {
  case_id: 'CASE-6FFF40DFB8DA',
  patient_id: 'P-10041',
  state: 'AwaitingHumanReview',
  intent: 'AppointmentPreparation',
  safety_level: 'MediumRisk',
  escalation_kind: 'MedicalQuestion',
  escalated_from_state: 'Classifying',
  created_at: '2026-09-19T22:12:38.560531Z',
  updated_at: '2026-09-19T22:12:39.693277Z',
  llm_cost_usd: null,
  llm_cost_partial: false,
  llm_unpriced_calls: 0,
}

function page(items: CaseSummary[], next_cursor: string | null = null): CaseListPage {
  return { items, next_cursor }
}

const DETAIL: CaseDetail = {
  case_id: DONE.case_id,
  state: DONE.state,
  escalation_kind: DONE.escalation_kind,
  updated_at: DONE.updated_at,
  patient_id: 'P-10041',
  state_version: 27,
  intent: 'AppointmentPreparation',
  safety_level: 'MediumRisk',
  identity_verified: true,
  plan_hash: '70471a828372f98f7949666f569537f3bc213e64aa0d3ec84f55ec9e2ba8cb99',
  ordered_steps: [
    { step: 1, action: 'CheckAppointment' },
    { step: 2, action: 'CheckDocuments' },
  ],
  current_step: 4,
  retry_cycle: 0,
  attempt_count: 1,
  required_documents: ['referral', 'blood_test'],
  held_documents: ['referral', 'blood_test'],
  escalated_from_state: null,
  patient_deadline: null,
  created_at: '2026-09-19T22:12:38.560531Z',
  appointment_id: 'APT-8392',
  answered_appointment_id: 'APT-8392',
  department: 'Cardiology',
  exam_type_label: 'מבחן מאמץ',
  instruction_source_id: 'INSTR-CARD-STRESS',
  instruction_version: '1',
  llm_usage: {
    calls: 2,
    input_tokens: 3600,
    cached_input_tokens: 0,
    output_tokens: 52,
    cost_usd: '0.00078240',
    unpriced_calls: 0,
    by_call: [
      { call: 'Intent', calls: 1, input_tokens: 1800, output_tokens: 40, cost_usd: '0.00040800' },
      { call: 'Safety', calls: 1, input_tokens: 1800, output_tokens: 12, cost_usd: '0.00037440' },
    ],
  },
}

const COSTS: LlmCosts = {
  window: { start: '2026-08-28T00:00:00Z', end: '2026-09-27T00:00:00Z' },
  cases: 5,
  cases_with_usage: 4,
  calls: 6,
  input_tokens: 7100,
  cached_input_tokens: 1000,
  output_tokens: 333,
  total_cost_usd: '0.00130360',
  avg_cost_per_case_usd: '0.00043453',
  avg_cost_per_completed_case_usd: '0.00050200',
  unpriced_calls: 0,
  by_call: [
    { call: 'Evaluator', calls: 1, input_tokens: 500, output_tokens: 23, cost_usd: '0.00010000' },
    { call: 'Intent', calls: 2, input_tokens: 3000, output_tokens: 150, cost_usd: '0.00060000' },
    { call: 'Planner', calls: 1, input_tokens: 1000, output_tokens: 100, cost_usd: '0.00020360' },
    { call: 'Safety', calls: 2, input_tokens: 2600, output_tokens: 60, cost_usd: '0.00040000' },
  ],
  by_source: [{ source: 'agent', calls: 6, cost_usd: '0.00130360' }],
}

/** A window whose only attempts are unpriced: no priced cost at all, so total and averages are null. */
const UNPRICED_ONLY: LlmCosts = {
  ...COSTS,
  cases: 3,
  cases_with_usage: 2,
  calls: 2,
  input_tokens: 1800,
  cached_input_tokens: 0,
  output_tokens: 80,
  total_cost_usd: null,
  avg_cost_per_case_usd: null,
  avg_cost_per_completed_case_usd: null,
  unpriced_calls: 2,
  by_call: [{ call: 'DocumentClassify', calls: 2, input_tokens: 1800, output_tokens: 80, cost_usd: null }],
  by_source: [{ source: 'document_service', calls: 2, cost_usd: null }],
}

/** A window with no attempt at all: nothing to cost, so every cost is null and nothing unpriced. */
const NO_USAGE: LlmCosts = {
  ...COSTS,
  cases: 3,
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
}

/** A promise this test controls, to make two requests resolve out of send order. */
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

function llmSpanOf(call: number): number {
  const [start, end] = vi.mocked(api.getLlmCosts).mock.calls[call]
  return end.getTime() - start.getTime()
}

function stripFact(label: string): HTMLElement {
  return screen.getByText(label, { selector: '.llm-strip-fact dt' }).closest('.llm-strip-fact') as HTMLElement
}

const CONTEXT: ReviewContext = {
  case_id: DONE.case_id,
  patient_id: 'P-10041',
  state: 'Completed',
  escalation_kind: null,
  escalated_from_state: null,
  reasons: [],
  data: [
    {
      entry_id: 'DATA-0080b88ca384',
      kind: 'request_text',
      content: 'מתי התור שלי ואילו מסמכים צריך להביא?',
      content_hash: '39d813fac47b85aba91577cb8ff34c584f018d92cf083d724a3a6c366a193bf3',
      created_at: '2026-09-19T22:12:39.681696Z',
    },
    {
      entry_id: 'DATA-11b0f2c7e401',
      kind: 'outgoing_message',
      content: 'התור שלך קבוע ל-23/09/2026 בשעה 08:30.',
      content_hash: '5f2b1c0a9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a',
      created_at: '2026-09-19T22:12:48.986200Z',
    },
  ],
  trace: [
    {
      audit_id: 1,
      record_type: 'Transition',
      event: 'REQUEST_SUBMITTED',
      state_before: null,
      state_after: 'Received',
      action: null,
      policy_result: null,
      policy_reasons: [],
      recorded_at: '2026-09-19T22:12:38.560531Z',
    },
  ],
  shown_context_ref: 'ctx-ba3e0652b0ea',
  appointment_id: null,
  answered_appointment_id: null,
  department: null,
  exam_type_label: null,
  instruction_source_id: null,
  instruction_version: null,
}

function renderMonitor() {
  return render(
    <MemoryRouter>
      <CaseMonitor />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(api.listCases).mockResolvedValue(page([DONE, IN_REVIEW]))
  vi.mocked(api.getCase).mockImplementation(async (caseId: string) => ({ ...DETAIL, case_id: caseId }))
  vi.mocked(api.getContext).mockImplementation(async (caseId: string) => ({ ...CONTEXT, case_id: caseId }))
  vi.mocked(api.getLlmCosts).mockReset()
  vi.mocked(api.getLlmCosts).mockResolvedValue(COSTS)
})

describe('CaseMonitor', () => {
  it('shows the shared loading status while the list is still loading', async () => {
    vi.mocked(api.listCases).mockReturnValue(new Promise(() => {})) // never resolves
    renderMonitor()
    const status = await screen.findByText('טוען פניות')
    expect(status).toHaveAttribute('role', 'status')
    expect(status.closest('.loader')).toBeInTheDocument()
  })

  it('lists every case straight from the one call, with no per-row detail call', async () => {
    renderMonitor()

    expect(await screen.findByText('CASE-23FE645294B7')).toBeInTheDocument()
    expect(screen.getByText('CASE-6FFF40DFB8DA')).toBeInTheDocument()
    expect(screen.getAllByText('Completed').length).toBeGreaterThan(0)
    expect(screen.getAllByText('AppointmentPreparation')).toHaveLength(2)
    expect(screen.getAllByText('MediumRisk')).toHaveLength(2)
    expect(screen.getAllByText('P-10041').length).toBeGreaterThan(0)

    // The N+1 this design fixes: before, 1 (listCases) + N (getCase per row); after, 1.
    expect(api.listCases).toHaveBeenCalledTimes(1)
    expect(api.getCase).not.toHaveBeenCalled()
    expect(api.getContext).not.toHaveBeenCalled()
  })

  it('filters by group, asking the API for the filtered page (staff-fixes design Task 4)', async () => {
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')

    vi.mocked(api.listCases).mockResolvedValue(page([DONE]))
    await userEvent.selectOptions(screen.getByLabelText('סינון לפי קבוצה'), 'done')

    expect(api.listCases).toHaveBeenLastCalledWith({ group: 'done', escalationKind: undefined })
    await waitFor(() => expect(screen.queryByText('CASE-6FFF40DFB8DA')).not.toBeInTheDocument())
  })

  it('asks for every case again when the group filter is cleared', async () => {
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')

    await userEvent.selectOptions(screen.getByLabelText('סינון לפי קבוצה'), 'done')
    await userEvent.selectOptions(screen.getByLabelText('סינון לפי קבוצה'), '')

    expect(api.listCases).toHaveBeenLastCalledWith({ group: undefined, escalationKind: undefined })
  })

  it('offers an escalation-kind sub-select only for the "ממתינות לצוות" group, and filters by it', async () => {
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')
    expect(screen.queryByLabelText(/סינון לפי סוג הסלמה/)).not.toBeInTheDocument()

    vi.mocked(api.listCases).mockResolvedValue(page([IN_REVIEW]))
    await userEvent.selectOptions(screen.getByLabelText('סינון לפי קבוצה'), 'staff')
    expect(api.listCases).toHaveBeenLastCalledWith({ group: 'staff', escalationKind: undefined })

    const kindSelect = await screen.findByLabelText(/סינון לפי סוג הסלמה/)
    expect(screen.getByText(/MedicalQuestion/, { selector: 'option' })).toBeInTheDocument()
    await userEvent.selectOptions(kindSelect, 'MedicalQuestion')

    expect(api.listCases).toHaveBeenLastCalledWith({ group: 'staff', escalationKind: 'MedicalQuestion' })
  })

  it('drops the escalation-kind sub-select (and its filter) when the group changes away from staff', async () => {
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')

    vi.mocked(api.listCases).mockResolvedValue(page([IN_REVIEW]))
    await userEvent.selectOptions(screen.getByLabelText('סינון לפי קבוצה'), 'staff')
    await userEvent.selectOptions(await screen.findByLabelText(/סינון לפי סוג הסלמה/), 'MedicalQuestion')

    vi.mocked(api.listCases).mockResolvedValue(page([DONE]))
    await userEvent.selectOptions(screen.getByLabelText('סינון לפי קבוצה'), 'done')

    expect(screen.queryByLabelText(/סינון לפי סוג הסלמה/)).not.toBeInTheDocument()
    expect(api.listCases).toHaveBeenLastCalledWith({ group: 'done', escalationKind: undefined })
  })

  it('expands a row to the case detail and its audit trace, fetching only that row', async () => {
    renderMonitor()

    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))

    expect(await screen.findByText('REQUEST_SUBMITTED')).toBeInTheDocument()
    expect(screen.getByText('CheckAppointment')).toBeInTheDocument()
    expect(api.getCase).toHaveBeenCalledTimes(1)
    expect(api.getCase).toHaveBeenCalledWith('CASE-23FE645294B7')
    expect(api.getContext).toHaveBeenCalledTimes(1)
    expect(api.getContext).toHaveBeenCalledWith('CASE-23FE645294B7')
  })

  it('shows the smaller inline loading status in an expanded row while its detail and context load, with distinct labels (M1)', async () => {
    vi.mocked(api.getCase).mockReturnValue(new Promise(() => {})) // never resolves
    vi.mocked(api.getContext).mockReturnValue(new Promise(() => {})) // never resolves
    renderMonitor()

    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))

    // Two live regions with the same text ("טוען…") would be indistinguishable to a screen
    // reader; each panel now names what it is loading.
    const detailStatus = await screen.findByText('טוען פרטים')
    const contextStatus = screen.getByText('טוען תכתובת')
    const statuses = [detailStatus, contextStatus]
    for (const status of statuses) expect(status.closest('.loader')).toHaveClass('loader-inline')
  })

  it('offers a "load more" button when the API says there is a next page, and appends the next page', async () => {
    vi.mocked(api.listCases).mockResolvedValue(page([DONE], 'CURSOR-1'))
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')
    expect(screen.queryByText('CASE-6FFF40DFB8DA')).not.toBeInTheDocument()

    vi.mocked(api.listCases).mockResolvedValue(page([IN_REVIEW], null))
    await userEvent.click(screen.getByRole('button', { name: 'טעינת עוד' }))

    expect(api.listCases).toHaveBeenLastCalledWith({ group: undefined, escalationKind: undefined, cursor: 'CURSOR-1' })
    expect(await screen.findByText('CASE-6FFF40DFB8DA')).toBeInTheDocument()
    // Both pages stay on screen; the first row was not replaced.
    expect(screen.getByText('CASE-23FE645294B7')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'טעינת עוד' })).not.toBeInTheDocument()
  })

  it('drops a stale "load more" response if the filter changed meanwhile (M6)', async () => {
    vi.mocked(api.listCases).mockResolvedValue(page([DONE], 'CURSOR-1'))
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')

    let resolveLoadMore: (value: CaseListPage) => void = () => {}
    vi.mocked(api.listCases).mockImplementation(
      () => new Promise((resolve) => { resolveLoadMore = resolve }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'טעינת עוד' }))

    // The group filter changes before that "load more" call resolves.
    vi.mocked(api.listCases).mockResolvedValue(page([IN_REVIEW]))
    await userEvent.selectOptions(screen.getByLabelText('סינון לפי קבוצה'), 'staff')
    await screen.findByText('CASE-6FFF40DFB8DA')
    expect(screen.queryByText('CASE-23FE645294B7')).not.toBeInTheDocument()

    // The stale "load more" now resolves - it must not resurrect the old row.
    await act(async () => {
      resolveLoadMore(page([DONE], null))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.queryByText('CASE-23FE645294B7')).not.toBeInTheDocument()
    expect(screen.getByText('CASE-6FFF40DFB8DA')).toBeInTheDocument()
  })

  it('does not leave "load more" stuck busy after a filter change races it, and load more works again (fix round 2 N1)', async () => {
    vi.mocked(api.listCases).mockResolvedValue(page([DONE], 'CURSOR-1'))
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')

    let resolveStaleLoadMore: (value: CaseListPage) => void = () => {}
    vi.mocked(api.listCases).mockImplementation(
      () => new Promise((resolve) => { resolveStaleLoadMore = resolve }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'טעינת עוד' }))
    expect(screen.getByRole('button', { name: 'טעינת עוד' })).toHaveAttribute('aria-busy', 'true')

    // The group filter changes before that "load more" call resolves.
    vi.mocked(api.listCases).mockResolvedValue(page([IN_REVIEW], 'CURSOR-2'))
    await userEvent.selectOptions(screen.getByLabelText('סינון לפי קבוצה'), 'staff')
    await screen.findByText('CASE-6FFF40DFB8DA')

    expect(screen.getByRole('button', { name: 'טעינת עוד' })).not.toHaveAttribute('aria-busy', 'true')

    // Clicking it again must actually run a new request, not stay silently stuck.
    vi.mocked(api.listCases).mockResolvedValue(page([DONE], null))
    await userEvent.click(screen.getByRole('button', { name: 'טעינת עוד' }))

    expect(await screen.findByText('CASE-23FE645294B7')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'טעינת עוד' })).not.toBeInTheDocument()

    // The stale "load more" from before the filter change may still resolve later - it
    // must not resurrect anything or throw.
    await act(async () => {
      resolveStaleLoadMore(page([IN_REVIEW], null))
      await Promise.resolve()
      await Promise.resolve()
    })
  })

  it('shows a failed "load more" as an inline error beside the button, not a replacement for the table (M6)', async () => {
    vi.mocked(api.listCases).mockResolvedValue(page([DONE], 'CURSOR-1'))
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')

    vi.mocked(api.listCases).mockRejectedValue(new api.ApiError(500, 'server_error'))
    await userEvent.click(screen.getByRole('button', { name: 'טעינת עוד' }))

    expect(await screen.findByText('טעינת פניות נוספות נכשלה')).toBeInTheDocument()
    // The table (and its already-loaded row) is still there, not replaced by the error.
    expect(screen.getByText('CASE-23FE645294B7')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('clears the context cache on a filter change, so a re-expanded case is fetched again', async () => {
    renderMonitor()
    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))
    await screen.findByText('REQUEST_SUBMITTED')
    expect(api.getContext).toHaveBeenCalledTimes(1)

    vi.mocked(api.listCases).mockResolvedValue(page([DONE]))
    await userEvent.selectOptions(screen.getByLabelText('סינון לפי קבוצה'), 'done')
    await screen.findByText('CASE-23FE645294B7')

    // The row collapsed on the filter change; expanding it again must not reuse a stale context.
    await userEvent.click(screen.getByRole('button', { name: 'CASE-23FE645294B7' }))
    await screen.findByText('REQUEST_SUBMITTED')
    expect(api.getContext).toHaveBeenCalledTimes(2)
  })

  it('reads in Hebrew and still shows every code the API returned', async () => {
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')

    // A label always sits next to its code, never instead of it.
    expect(screen.getAllByText('הושלמה').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Completed').length).toBeGreaterThan(0)
    expect(screen.getAllByText('הכנה לתור')).toHaveLength(2)
    expect(screen.getAllByText('AppointmentPreparation')).toHaveLength(2)
    expect(screen.getAllByText('סיכון בינוני')).toHaveLength(2)
    expect(screen.getAllByText('MediumRisk')).toHaveLength(2)
    expect(screen.getByText('שאלה רפואית')).toBeInTheDocument()
    expect(screen.getByText('MedicalQuestion')).toBeInTheDocument()
  })

  it('explains the expanded case in Hebrew, including where the plan stands', async () => {
    vi.mocked(api.getCase).mockImplementation(async (caseId: string) => ({
      ...DETAIL,
      case_id: caseId,
      current_step: 2,
      held_documents: ['referral'],
    }))
    const { container } = renderMonitor()

    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))
    await screen.findByText('REQUEST_SUBMITTED')

    // The current step is named, not just numbered, and counted against the plan.
    expect(screen.getByText('2 מתוך 2 · CheckDocuments')).toBeInTheDocument()
    // What is missing is worked out from the two lists, so nobody has to diff them by eye.
    const documents = screen.getByRole('heading', { name: 'מסמכים' }).closest('.fact-group')
    expect(documents).toHaveTextContent('חסרים')
    expect(documents).toHaveTextContent('blood_test')
    // Each Hebrew label carries the field name from docs/api.md beside it.
    expect(screen.getByText('שלב נוכחי')).toBeInTheDocument()
    expect(screen.getByText('current_step')).toBeInTheDocument()
    // And the plan shows which step is the current one.
    expect(container.querySelector('.plan-steps .step-current')).toHaveTextContent('CheckDocuments')
    expect(container.querySelector('.plan-steps .step-done')).toHaveTextContent('CheckAppointment')
  })

  it('shows the chosen appointment, its exam type and the instruction source, labels beside codes (sub-project 18, D13)', async () => {
    renderMonitor()
    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))
    await screen.findByText('REQUEST_SUBMITTED')

    const group = screen.getByRole('heading', { name: 'תור והוראות הכנה' }).closest('.fact-group') as HTMLElement
    for (const code of ['appointment_id', 'answered_appointment_id', 'department', 'exam_type_label', 'instruction_source_id']) {
      expect(within(group).getByText(code)).toHaveClass('fact-code')
    }
    expect(within(group).getByText('התור שנבחר')).toBeInTheDocument()
    expect(within(group).getByText('מבחן מאמץ')).toBeInTheDocument()
    expect(group).toHaveTextContent('קרדיולוגיה')
    expect(group).toHaveTextContent('גרסה 1')
    // Every code is its own isolated element, never folded into a Hebrew string.
    const codes = Array.from(group.querySelectorAll('.code')).map((el) => el.textContent)
    expect(codes).toEqual(['APT-8392', 'APT-8392', 'Cardiology', 'INSTR-CARD-STRESS'])
  })

  it('says the patient chose no appointment, and dashes the rest, on a mock-path case', async () => {
    vi.mocked(api.getCase).mockImplementation(async (caseId: string) => ({
      ...DETAIL,
      case_id: caseId,
      appointment_id: null,
      answered_appointment_id: null,
      department: null,
      exam_type_label: null,
      instruction_source_id: 'INSTR-PREP-COLONOSCOPY',
      instruction_version: '3',
    }))
    renderMonitor()
    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))
    await screen.findByText('REQUEST_SUBMITTED')

    const group = screen.getByRole('heading', { name: 'תור והוראות הכנה' }).closest('.fact-group') as HTMLElement
    expect(group).toHaveTextContent('לא נבחר (התור הקרוב ביותר)')
    expect(within(group).getByText('INSTR-PREP-COLONOSCOPY')).toHaveClass('code')
    expect(group).toHaveTextContent('גרסה 3')
  })

  it('shows a Hebrew error, not a blank panel, when the row detail call fails', async () => {
    vi.mocked(api.getCase).mockRejectedValue(new api.ApiError(404, 'case_not_found'))
    renderMonitor()

    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))

    expect(await screen.findByText('טעינת פרטי הפנייה נכשלה')).toBeInTheDocument()
    expect(screen.getByText('case_not_found')).toBeInTheDocument()
  })

  it('shows the sub-project 11 catalog codes with their Hebrew label beside them', async () => {
    vi.mocked(api.getCase).mockImplementation(async (caseId: string) => ({
      ...DETAIL,
      case_id: caseId,
      required_documents: ['CBC', 'ECG'],
      held_documents: ['CBC'],
    }))
    const { container } = renderMonitor()

    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))
    await screen.findByText('REQUEST_SUBMITTED')

    const documents = screen.getByRole('heading', { name: 'מסמכים' }).closest('.fact-group') as HTMLElement
    // The code stays on screen, and its Hebrew label sits beside it, never instead of it.
    expect(documents).toHaveTextContent('CBC')
    expect(documents).toHaveTextContent('ספירת דם מלאה')
    expect(documents).toHaveTextContent('ECG')
    expect(documents).toHaveTextContent('תרשים פעילות חשמלית של הלב')

    // Each code is its own isolated element (a Latin run inside Hebrew text must not pick
    // its own bidi direction, CLAUDE.md), not one joined string - the same pattern the
    // patient screen uses (RequestDetail.tsx: span.code[dir=ltr]).
    const codes = Array.from(documents.querySelectorAll('.code'))
    const codeTexts = codes.map((el) => el.textContent)
    expect(codeTexts).toEqual(expect.arrayContaining(['CBC', 'ECG']))
    for (const el of codes) {
      expect(el).toHaveAttribute('dir', 'ltr')
    }
    expect(container.querySelector('.fact-group')?.innerHTML).not.toMatch(/CBC \(/)
  })

  it('isolates the missing-document codes too, each in its own element', async () => {
    vi.mocked(api.getCase).mockImplementation(async (caseId: string) => ({
      ...DETAIL,
      case_id: caseId,
      required_documents: ['CBC', 'ECG'],
      held_documents: ['CBC'],
    }))
    renderMonitor()

    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))
    await screen.findByText('REQUEST_SUBMITTED')

    // "ECG" is missing (required but not held) - it still renders as its own isolated code,
    // with its label, inside the "חסרים" fact.
    const missingFact = screen.getByText('חסרים').closest('.fact') as HTMLElement
    expect(missingFact).toHaveTextContent('ECG')
    expect(missingFact).toHaveTextContent('תרשים פעילות חשמלית של הלב')
    const missingCode = missingFact.querySelector('.code')
    expect(missingCode).toHaveAttribute('dir', 'ltr')
    expect(missingCode?.textContent).toBe('ECG')
  })

  it('shows the correspondence with the patient, and which way each message went', async () => {
    const { container } = renderMonitor()

    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))
    await screen.findByText('REQUEST_SUBMITTED')

    expect(screen.getByText('מתי התור שלי ואילו מסמכים צריך להביא?')).toBeInTheDocument()
    expect(screen.getByText('התור שלך קבוע ל-23/09/2026 בשעה 08:30.')).toBeInTheDocument()
    const items = [...container.querySelectorAll('.thread-item')]
    expect(items.map((item) => item.className)).toEqual([
      'thread-item from-patient',
      'thread-item to-patient',
    ])
    expect(items[0]).toHaveTextContent('המטופל כתב')
    expect(items[1]).toHaveTextContent('נשלח למטופל')
    // The start of the hash is what ties a message to its audit row.
    expect(items[0]).toHaveTextContent('39d813fac47b…')
  })

  it('says so plainly when a case has no content left to show', async () => {
    vi.mocked(api.getContext).mockResolvedValue({ ...CONTEXT, data: [] })
    renderMonitor()

    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))

    expect(await screen.findByText(/לא נשמר תוכן לפנייה הזו/)).toBeInTheDocument()
  })

  it('shows an empty state when no case matches, naming the group (M7)', async () => {
    vi.mocked(api.listCases).mockResolvedValue(page([]))
    renderMonitor()

    expect(await screen.findByText('אין פניות להצגה')).toBeInTheDocument()
    expect(screen.getByText('אין פניות בקבוצה שנבחרה.')).toBeInTheDocument()
  })
})

describe('CaseMonitor: LLM cost (sub-project 19, design D7)', () => {
  it('shows the average LLM cost per case for the last 30 days, with the total, cases and calls', async () => {
    renderMonitor()

    expect(await screen.findByText('עלות LLM ממוצעת לפנייה')).toBeInTheDocument()
    expect(api.getLlmCosts).toHaveBeenCalledTimes(1)
    expect(llmSpanOf(0)).toBe(30 * 24 * 3600_000)
    expect(stripFact('עלות LLM ממוצעת לפנייה')).toHaveTextContent('$0.0004')
    expect(stripFact('סה״כ')).toHaveTextContent('$0.0013')
    expect(stripFact('פניות')).toHaveTextContent('5')
    expect(stripFact('קריאות')).toHaveTextContent('6')
    expect(screen.getByRole('button', { name: '30 יום' })).toHaveAttribute('aria-pressed', 'true')
    // Every price is known here, so there is nothing to caution about.
    expect(screen.queryByText(/אין מחיר ידוע/)).not.toBeInTheDocument()
  })

  it('says the average is over the cases with a known cost, keeping the amount in its LTR run (M4)', async () => {
    renderMonitor()

    const note = await screen.findByText('הממוצע מחושב על פני הפניות שיש להן עלות ידועה.')
    expect(note).toHaveClass('llm-strip-note')
    // The note is its own line, outside the average's value: the amount is still one LTR run.
    const average = stripFact('עלות LLM ממוצעת לפנייה').querySelector('dd')
    expect(average?.textContent).toBe('$0.0004')
    expect(average?.querySelector('.usd')).toHaveAttribute('dir', 'ltr')
    expect(average).not.toContainElement(note)
  })

  it('shows the shared inline loader while the costs load', async () => {
    vi.mocked(api.getLlmCosts).mockReturnValue(new Promise(() => {})) // never resolves
    renderMonitor()
    const status = await screen.findByText('טוען עלות LLM')
    expect(status).toHaveAttribute('role', 'status')
    expect(status.closest('.loader')).toHaveClass('loader-inline')
  })

  it('says "unknown price" for a null average and total beside unpriced calls, and notes it quietly', async () => {
    vi.mocked(api.getLlmCosts).mockResolvedValue(UNPRICED_ONLY)
    renderMonitor()

    // A null cost beside unpriced calls is "unknown", not "nothing" (docs/api.md §10).
    await waitFor(() => expect(stripFact('עלות LLM ממוצעת לפנייה')).toHaveTextContent('מחיר לא ידוע'))
    expect(stripFact('סה״כ')).toHaveTextContent('מחיר לא ידוע')
    expect(screen.getByText(/ל־2 מהקריאות אין מחיר ידוע/)).toHaveClass('llm-strip-note')
  })

  it('renders a null total beside zero averages: the averages cover only the priced cases', async () => {
    // One case with only priced API errors (cost "0.00000000") and one with only unpriced rows:
    // the total is null (an unpriced row and no priced cost), the averages are over the first.
    vi.mocked(api.getLlmCosts).mockResolvedValue({
      ...COSTS,
      cases: 2,
      cases_with_usage: 2,
      calls: 2,
      input_tokens: 900,
      cached_input_tokens: 0,
      output_tokens: 40,
      total_cost_usd: null,
      avg_cost_per_case_usd: '0.00000000',
      avg_cost_per_completed_case_usd: '0.00000000',
      unpriced_calls: 1,
      by_call: [
        { call: 'DocumentClassify', calls: 1, input_tokens: 900, output_tokens: 40, cost_usd: null },
        { call: 'Intent', calls: 1, input_tokens: 0, output_tokens: 0, cost_usd: '0.00000000' },
      ],
      by_source: [
        { source: 'agent', calls: 1, cost_usd: '0.00000000' },
        { source: 'document_service', calls: 1, cost_usd: null },
      ],
    })
    renderMonitor()

    await waitFor(() => expect(stripFact('עלות LLM ממוצעת לפנייה')).toHaveTextContent('$0.0000'))
    expect(stripFact('סה״כ')).toHaveTextContent('מחיר לא ידוע')
  })

  it('dashes a null average and total when nothing was processed yet', async () => {
    vi.mocked(api.getLlmCosts).mockResolvedValue(NO_USAGE)
    renderMonitor()

    await waitFor(() => expect(stripFact('פניות')).toHaveTextContent('3'))
    expect(stripFact('עלות LLM ממוצעת לפנייה').querySelector('dd')?.textContent).toBe('—')
    expect(stripFact('סה״כ').querySelector('dd')?.textContent).toBe('—')
    expect(screen.queryByText(/אין מחיר ידוע/)).not.toBeInTheDocument()
  })

  it('shows a failed load with its code, and retries the same range', async () => {
    vi.mocked(api.getLlmCosts)
      .mockRejectedValueOnce(new api.ApiError(503, 'llm_costs_unavailable'))
      .mockResolvedValueOnce(COSTS)
    renderMonitor()

    const alert = (await screen.findByText('טעינת עלות ה־LLM נכשלה')).closest('.alert') as HTMLElement
    expect(alert).toHaveTextContent('חישוב העלות ארך יותר מדי. נסו טווח קצר יותר.')
    expect(within(alert).getByText('llm_costs_unavailable')).toHaveClass('mono')
    // The case list is independent of the strip and still loads.
    expect(await screen.findByText('CASE-23FE645294B7')).toBeInTheDocument()

    await userEvent.click(within(alert).getByRole('button', { name: 'נסה שוב' }))
    await waitFor(() => expect(stripFact('עלות LLM ממוצעת לפנייה')).toHaveTextContent('$0.0004'))
    expect(api.getLlmCosts).toHaveBeenCalledTimes(2)
    expect(llmSpanOf(1)).toBe(30 * 24 * 3600_000)
    expect(screen.queryByText('טעינת עלות ה־LLM נכשלה')).not.toBeInTheDocument()
  })

  it('asks again for a preset, and lets only the newest range land', async () => {
    const d7 = deferred<LlmCosts>()
    const d24 = deferred<LlmCosts>()
    vi.mocked(api.getLlmCosts)
      .mockResolvedValueOnce(COSTS)
      .mockReturnValueOnce(d7.promise)
      .mockReturnValueOnce(d24.promise)
    renderMonitor()
    await waitFor(() => expect(stripFact('פניות')).toHaveTextContent('5'))

    await userEvent.click(screen.getByRole('button', { name: '7 ימים' }))
    await userEvent.click(screen.getByRole('button', { name: '24 שעות' }))
    expect(llmSpanOf(1)).toBe(7 * 24 * 3600_000)
    expect(llmSpanOf(2)).toBe(24 * 3600_000)
    expect(screen.getByRole('button', { name: '24 שעות' })).toHaveAttribute('aria-pressed', 'true')

    d24.resolve({ ...COSTS, cases: 24 })
    await waitFor(() => expect(stripFact('פניות')).toHaveTextContent('24'))

    // The superseded 7-day answer arrives last and must not overwrite the 24-hour one.
    await act(async () => {
      d7.resolve({ ...COSTS, cases: 7 })
      await d7.promise
    })
    expect(stripFact('פניות')).toHaveTextContent('24')
  })

  it.each([
    ['range_too_large', 422, 'הטווח ארוך מ־90 יום.'],
    ['staff_only', 403, 'אין הרשאה לצפות בעלויות.'],
    ['server_error', 500, 'אירעה שגיאה בטעינת הנתונים.'],
  ])('explains a %s refusal in Hebrew, the code beside it', async (code, status, text) => {
    vi.mocked(api.getLlmCosts).mockRejectedValue(new api.ApiError(status, code))
    renderMonitor()

    const alert = (await screen.findByText('טעינת עלות ה־LLM נכשלה')).closest('.alert') as HTMLElement
    expect(alert).toHaveTextContent(text)
    expect(within(alert).getByText(code)).toHaveClass('mono')
  })

  it('does not throw when an answer arrives after the screen is gone', async () => {
    const late = deferred<LlmCosts>()
    vi.mocked(api.getLlmCosts).mockReturnValue(late.promise)
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { unmount } = renderMonitor()
    await screen.findByText('טוען עלות LLM')
    unmount()

    await act(async () => {
      late.resolve(COSTS)
      await late.promise
    })
    expect(errors).not.toHaveBeenCalled()
    errors.mockRestore()
  })

  it('adds an "עלות LLM" column, dashing a case with no cost and marking a partial one', async () => {
    const PARTIAL: CaseSummary = {
      ...DONE,
      case_id: 'CASE-PARTIAL00001',
      llm_cost_usd: '0.00100000',
      llm_cost_partial: true,
      llm_unpriced_calls: 1,
    }
    vi.mocked(api.listCases).mockResolvedValue(page([DONE, IN_REVIEW, PARTIAL]))
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')

    expect(screen.getByRole('columnheader', { name: 'עלות LLM' })).toBeInTheDocument()
    const rowOf = (caseId: string) => screen.getByRole('button', { name: caseId }).closest('tr') as HTMLElement
    const lastCell = (caseId: string) => within(rowOf(caseId)).getAllByRole('cell').at(-1) as HTMLElement
    expect(lastCell('CASE-23FE645294B7').textContent).toBe('$0.0021')
    expect(lastCell('CASE-6FFF40DFB8DA').textContent).toBe('—')
    // A lower bound reads "$0.0010+", and the "+" says why.
    expect(lastCell('CASE-PARTIAL00001').textContent).toBe('$0.0010+')
    const marker = within(lastCell('CASE-PARTIAL00001')).getByRole('img', { name: 'חלק מהקריאות ללא מחיר ידוע' })
    expect(marker).toHaveAttribute('title', 'חלק מהקריאות ללא מחיר ידוע')
    expect(within(lastCell('CASE-23FE645294B7')).queryByRole('img')).not.toBeInTheDocument()
  })

  it("shows the case's LLM usage in the expanded row, labels beside codes and a row per call", async () => {
    vi.mocked(api.getCase).mockImplementation(async (caseId: string) => ({
      ...DETAIL,
      case_id: caseId,
      llm_usage: {
        ...DETAIL.llm_usage,
        by_call: [
          ...DETAIL.llm_usage.by_call,
          { call: 'DocumentVision', calls: 1, input_tokens: 1200, output_tokens: 40, cost_usd: null },
          { call: 'SomethingNew', calls: 1, input_tokens: 0, output_tokens: 0, cost_usd: '0.00000000' },
        ],
      },
    }))
    renderMonitor()
    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))
    await screen.findByText('REQUEST_SUBMITTED')

    const group = screen.getByRole('heading', { name: 'עלות LLM', level: 3 }).closest('.fact-group') as HTMLElement
    for (const code of ['calls', 'input_tokens', 'cached_input_tokens', 'output_tokens', 'cost_usd', 'unpriced_calls']) {
      expect(within(group).getByText(code)).toHaveClass('fact-code')
    }
    const fact = (label: string) => within(group).getByText(label, { selector: 'dt' }).closest('.fact') as HTMLElement
    expect(fact('קריאות')).toHaveTextContent('2')
    expect(fact('טוקני קלט')).toHaveTextContent('3,600')
    expect(fact('טוקני פלט')).toHaveTextContent('52')
    expect(fact('עלות')).toHaveTextContent('$0.0008')

    const rows = within(group).getAllByRole('row').slice(1)
    const cells = (row: HTMLElement) => within(row).getAllByRole('cell').map((cell) => cell.textContent)
    expect(rows.map(cells)).toEqual([
      ['סיווג כוונהIntent', '1', '1,800', '40', '$0.0004'],
      ['סיווג סיכוןSafety', '1', '1,800', '12', '$0.0004'],
      ['קריאת מסמך סרוקDocumentVision', '1', '1,200', '40', 'מחיר לא ידוע'],
      ['SomethingNew', '1', '0', '0', '$0.0000'],
    ])
    for (const code of ['Intent', 'Safety', 'DocumentVision', 'SomethingNew']) {
      expect(within(group).getByText(code)).toHaveClass('mono')
    }
  })

  it('tells "no call yet" ("—") apart from "only unpriced calls" ("מחיר לא ידוע")', async () => {
    vi.mocked(api.getCase).mockImplementation(async (caseId: string) => ({
      ...DETAIL,
      case_id: caseId,
      llm_usage:
        caseId === DONE.case_id
          ? { calls: 0, input_tokens: 0, cached_input_tokens: 0, output_tokens: 0, cost_usd: null, unpriced_calls: 0, by_call: [] }
          : {
              calls: 1,
              input_tokens: 900,
              cached_input_tokens: 0,
              output_tokens: 40,
              cost_usd: null,
              unpriced_calls: 1,
              by_call: [{ call: 'DocumentClassify', calls: 1, input_tokens: 900, output_tokens: 40, cost_usd: null }],
            },
    }))
    renderMonitor()

    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))
    await screen.findByText('REQUEST_SUBMITTED')
    const costOf = () => {
      const group = screen.getByRole('heading', { name: 'עלות LLM', level: 3 }).closest('.fact-group') as HTMLElement
      return within(group).getByText('עלות', { selector: 'dt' }).closest('.fact')?.querySelector('dd')?.textContent
    }
    expect(costOf()).toBe('—')
    expect(screen.getByText('לא נעשו קריאות LLM בפנייה הזו.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'CASE-6FFF40DFB8DA' }))
    expect(await screen.findByText('DocumentClassify')).toHaveClass('mono')
    expect(costOf()).toBe('מחיר לא ידוע')
    expect(screen.getByText('סיווג מסמך')).toBeInTheDocument()
  })

  it('says "unknown price" in the column for a null cost beside unpriced calls, "—" for none (I1)', async () => {
    const UNPRICED: CaseSummary = {
      ...DONE,
      case_id: 'CASE-UNPRICED0001',
      llm_cost_usd: null,
      llm_cost_partial: false,
      llm_unpriced_calls: 2,
    }
    vi.mocked(api.listCases).mockResolvedValue(page([IN_REVIEW, UNPRICED]))
    renderMonitor()
    await screen.findByText('CASE-UNPRICED0001')

    const lastCell = (caseId: string) =>
      within(screen.getByRole('button', { name: caseId }).closest('tr') as HTMLElement).getAllByRole('cell').at(-1) as HTMLElement
    expect(lastCell('CASE-UNPRICED0001').textContent).toBe('מחיר לא ידוע')
    expect(lastCell('CASE-6FFF40DFB8DA').textContent).toBe('—')
  })

  it('keeps every dollar amount in its own LTR run, the "+" and the "<" inside it (I2)', async () => {
    const TINY: CaseSummary = { ...DONE, case_id: 'CASE-TINY00000001', llm_cost_usd: '0.00000100' }
    const PARTIAL: CaseSummary = {
      ...DONE,
      case_id: 'CASE-PARTIAL00001',
      llm_cost_usd: '0.00100000',
      llm_cost_partial: true,
      llm_unpriced_calls: 1,
    }
    vi.mocked(api.listCases).mockResolvedValue(page([DONE, TINY, PARTIAL]))
    renderMonitor()
    await screen.findByText('CASE-TINY00000001')

    const ltrOf = (element: Element | null | undefined) => element?.closest('[dir="ltr"]')

    // The table cells.
    const lastCell = (caseId: string) =>
      within(screen.getByRole('button', { name: caseId }).closest('tr') as HTMLElement).getAllByRole('cell').at(-1) as HTMLElement
    for (const [caseId, amount] of [
      ['CASE-23FE645294B7', '$0.0021'],
      ['CASE-TINY00000001', '<$0.0001'],
      ['CASE-PARTIAL00001', '$0.0010+'],
    ]) {
      const run = lastCell(caseId).querySelector('[dir="ltr"]')
      expect(run?.textContent).toBe(amount)
    }
    const marker = within(lastCell('CASE-PARTIAL00001')).getByRole('img', { name: 'חלק מהקריאות ללא מחיר ידוע' })
    expect(ltrOf(marker)?.textContent).toBe('$0.0010+')

    // The strip.
    await waitFor(() => expect(stripFact('סה״כ')).toHaveTextContent('$0.0013'))
    expect(ltrOf(within(stripFact('סה״כ')).getByText('$0.0013'))).not.toBeNull()
    expect(ltrOf(within(stripFact('עלות LLM ממוצעת לפנייה')).getByText('$0.0004'))).not.toBeNull()

    // The expanded row's fact group and its per-call table.
    await userEvent.click(screen.getByRole('button', { name: 'CASE-23FE645294B7' }))
    await screen.findByText('REQUEST_SUBMITTED')
    const group = screen.getByRole('heading', { name: 'עלות LLM', level: 3 }).closest('.fact-group') as HTMLElement
    expect(ltrOf(within(group).getByText('$0.0008'))).not.toBeNull()
    for (const amount of within(group).getAllByText('$0.0004')) expect(ltrOf(amount)).not.toBeNull()
  })
})
