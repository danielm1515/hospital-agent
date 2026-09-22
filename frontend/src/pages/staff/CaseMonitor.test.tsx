import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import type { CaseDetail, CaseSummary, ReviewContext } from '../../api/types'
import { CaseMonitor } from './CaseMonitor'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  listCases: vi.fn(),
  getCase: vi.fn(),
  getContext: vi.fn(),
}))

const DONE: CaseSummary = {
  case_id: 'CASE-23FE645294B7',
  state: 'Completed',
  escalation_kind: null,
  updated_at: '2026-09-19T22:12:48.986200Z',
}

const IN_REVIEW: CaseSummary = {
  case_id: 'CASE-6FFF40DFB8DA',
  state: 'AwaitingHumanReview',
  escalation_kind: 'MedicalQuestion',
  updated_at: '2026-09-19T22:12:39.693277Z',
}

const DETAIL: CaseDetail = {
  ...DONE,
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
}

function renderMonitor() {
  return render(
    <MemoryRouter>
      <CaseMonitor />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(api.listCases).mockResolvedValue([DONE, IN_REVIEW])
  vi.mocked(api.getCase).mockImplementation(async (caseId: string) => ({ ...DETAIL, case_id: caseId }))
  vi.mocked(api.getContext).mockImplementation(async (caseId: string) => ({ ...CONTEXT, case_id: caseId }))
})

describe('CaseMonitor', () => {
  it('lists every case with the details the API returns', async () => {
    renderMonitor()

    expect(await screen.findByText('CASE-23FE645294B7')).toBeInTheDocument()
    expect(screen.getByText('CASE-6FFF40DFB8DA')).toBeInTheDocument()
    expect(screen.getAllByText('Completed').length).toBeGreaterThan(0)
    await waitFor(() => expect(screen.getAllByText('AppointmentPreparation')).toHaveLength(2))
    expect(screen.getAllByText('MediumRisk')).toHaveLength(2)
    expect(screen.getAllByText('P-10041').length).toBeGreaterThan(0)
  })

  it('filters by state', async () => {
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')

    vi.mocked(api.listCases).mockResolvedValue([DONE])
    await userEvent.selectOptions(screen.getByLabelText('סינון לפי State'), 'Completed')

    expect(api.listCases).toHaveBeenLastCalledWith('Completed')
    await waitFor(() => expect(screen.queryByText('CASE-6FFF40DFB8DA')).not.toBeInTheDocument())
  })

  it('asks for every case again when the filter is cleared', async () => {
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')

    await userEvent.selectOptions(screen.getByLabelText('סינון לפי State'), 'Completed')
    await userEvent.selectOptions(screen.getByLabelText('סינון לפי State'), '')

    expect(api.listCases).toHaveBeenLastCalledWith(undefined)
  })

  it('expands a row to the case detail and its audit trace', async () => {
    renderMonitor()

    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))

    expect(await screen.findByText('REQUEST_SUBMITTED')).toBeInTheDocument()
    expect(screen.getByText('CheckAppointment')).toBeInTheDocument()
    expect(api.getContext).toHaveBeenCalledWith('CASE-23FE645294B7')
  })

  it('reads in Hebrew and still shows every code the API returned', async () => {
    renderMonitor()
    await screen.findByText('CASE-23FE645294B7')

    // A label always sits next to its code, never instead of it.
    expect(screen.getAllByText('הושלמה').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Completed').length).toBeGreaterThan(0)
    await waitFor(() => expect(screen.getAllByText('הכנה לתור')).toHaveLength(2))
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

  it('shows the sub-project 11 catalog codes with their Hebrew label beside them', async () => {
    vi.mocked(api.getCase).mockImplementation(async (caseId: string) => ({
      ...DETAIL,
      case_id: caseId,
      required_documents: ['CBC', 'ECG'],
      held_documents: ['CBC'],
    }))
    renderMonitor()

    await userEvent.click(await screen.findByRole('button', { name: 'CASE-23FE645294B7' }))
    await screen.findByText('REQUEST_SUBMITTED')

    const documents = screen.getByRole('heading', { name: 'מסמכים' }).closest('.fact-group')
    // The code stays on screen, and its Hebrew label sits beside it, never instead of it.
    expect(documents).toHaveTextContent('CBC (ספירת דם מלאה)')
    expect(documents).toHaveTextContent('ECG (תרשים פעילות חשמלית של הלב)')
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

  it('shows an empty state when no case matches', async () => {
    vi.mocked(api.listCases).mockResolvedValue([])
    renderMonitor()

    expect(await screen.findByText('אין פניות להצגה')).toBeInTheDocument()
  })
})
