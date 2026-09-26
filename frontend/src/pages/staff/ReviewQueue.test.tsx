import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import type { ReviewContext, ReviewItem, ReviewQueuePage } from '../../api/types'
import { authValue, STAFF_USER, TestAuthProvider } from '../../test/helpers'
import { NOTICE_DISMISS_MS, QUEUE_POLL_MS, ReviewQueue } from './ReviewQueue'
import { ReviewCase } from './ReviewCase'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  listReviews: vi.fn(),
  getContext: vi.fn(),
  getReviewItem: vi.fn(),
  decide: vi.fn(),
  getMessageTemplates: vi.fn(),
  listCaseAppointments: vi.fn(),
  requestFromPatient: vi.fn(),
  answer: vi.fn(),
}))

const MEDICAL: ReviewItem = {
  case_id: 'CASE-6FFF40DFB8DA',
  patient_id: 'P-10041',
  escalation_kind: 'MedicalQuestion',
  escalated_from_state: 'Classifying',
  reasons: [],
  allowed_decisions: ['resolve', 'reject'],
  required_fields: [],
  entered_at: '2026-09-19T22:12:39.693277Z',
  human_engaged: false,
  returned_by: null,
}

const Z3: ReviewItem = {
  case_id: 'CASE-23FE645294B7',
  patient_id: 'P-20000',
  escalation_kind: 'Z3Counterexample',
  escalated_from_state: 'AssessingReadiness',
  reasons: ['hours_until:20'],
  allowed_decisions: ['approve', 'resolve', 'reject'],
  required_fields: ['patient_deadline'],
  entered_at: '2026-09-19T22:14:02.100000Z',
  human_engaged: false,
  returned_by: null,
}

function page(items: ReviewItem[], next_cursor: string | null = null): ReviewQueuePage {
  return { items, next_cursor }
}

function renderQueue(route = '/staff') {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path="/staff" element={<ReviewQueue />} />
        <Route path="/staff/cases/:caseId" element={<h1>מסך הפנייה</h1>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.useRealTimers()
})

describe('ReviewQueue', () => {
  it('shows the shared loading status while the queue is still loading', async () => {
    vi.mocked(api.listReviews).mockReturnValue(new Promise(() => {})) // never resolves
    renderQueue()
    expect(await screen.findByText('טוען פניות')).toHaveAttribute('role', 'status')
  })

  it('renders a row per queue item, with the Hebrew label and the code', async () => {
    vi.mocked(api.listReviews).mockResolvedValue(page([MEDICAL, Z3]))
    renderQueue()

    expect(await screen.findByText('CASE-6FFF40DFB8DA')).toBeInTheDocument()
    expect(screen.getByText('שאלה רפואית')).toBeInTheDocument()
    expect(screen.getByText('MedicalQuestion')).toBeInTheDocument()
    expect(screen.getByText('Classifying')).toBeInTheDocument()
    expect(screen.getByText('hours_until:20')).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(3) // header + two cases
  })

  it('shows the returned_by mark with its Hebrew label and the code', async () => {
    vi.mocked(api.listReviews).mockResolvedValue(page([{ ...MEDICAL, returned_by: 'patient_reply' }]))
    renderQueue()

    expect(await screen.findByText('התקבלה תשובת מטופל')).toBeInTheDocument()
    const code = screen.getByText('patient_reply')
    expect(code).toBeInTheDocument()
    expect(code.className).toContain('mono')
  })

  it('opens the case when its row is clicked', async () => {
    vi.mocked(api.listReviews).mockResolvedValue(page([MEDICAL]))
    renderQueue()

    await userEvent.click(await screen.findByText('P-10041'))

    expect(await screen.findByRole('heading', { name: 'מסך הפנייה' })).toBeInTheDocument()
  })

  it('shows the empty state when nothing waits for a decision', async () => {
    vi.mocked(api.listReviews).mockResolvedValue(page([]))
    renderQueue()

    expect(await screen.findByText('אין פניות הממתינות להכרעה')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows the notice a finished decision navigated back with, titled as the decision said', async () => {
    vi.mocked(api.listReviews).mockResolvedValue(page([]))
    render(
      <MemoryRouter
        initialEntries={[
          { pathname: '/staff', state: { title: 'ההכרעה נשמרה', notice: 'הפנייה CASE-1 עברה למצב Completed.' } },
        ]}
      >
        <Routes>
          <Route path="/staff" element={<ReviewQueue />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText('הפנייה CASE-1 עברה למצב Completed.')).toBeInTheDocument()
    expect(screen.getByText('ההכרעה נשמרה')).toHaveClass('title')
  })

  it('auto-dismisses the notice after 8 s', async () => {
    vi.useFakeTimers()
    vi.mocked(api.listReviews).mockResolvedValue(page([]))
    render(
      <MemoryRouter initialEntries={[{ pathname: '/staff', state: { notice: 'X', title: 'ההכרעה נשמרה' } }]}>
        <Routes>
          <Route path="/staff" element={<ReviewQueue />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getByText('X')).toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(NOTICE_DISMISS_MS))

    expect(screen.queryByText('X')).not.toBeInTheDocument()
  })

  it('closes the notice with the close button', async () => {
    vi.mocked(api.listReviews).mockResolvedValue(page([]))
    render(
      <MemoryRouter initialEntries={[{ pathname: '/staff', state: { notice: 'X', title: 'ההכרעה נשמרה' } }]}>
        <Routes>
          <Route path="/staff" element={<ReviewQueue />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getByText('X')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'סגירה' }))

    expect(screen.queryByText('X')).not.toBeInTheDocument()
  })

  it('does not show the notice again on remount - the history entry was replaced (Task 7)', async () => {
    vi.mocked(api.listReviews).mockResolvedValue(page([]))

    function Harness({ show }: { show: boolean }) {
      return show ? <ReviewQueue /> : null
    }

    const { rerender } = render(
      <MemoryRouter initialEntries={[{ pathname: '/staff', state: { notice: 'X', title: 'ההכרעה נשמרה' } }]}>
        <Routes>
          <Route path="/staff" element={<Harness show={true} />} />
        </Routes>
      </MemoryRouter>,
    )
    // The notice shows once, from the entry's original state.
    expect(screen.getByText('X')).toBeInTheDocument()

    // Unmount, then remount at the same route: the mount effect already replaced the
    // history entry's state with `null`, so a fresh ReviewQueue (a reload, or Back to
    // this same entry) reads no notice at all - this is what a real reload would see.
    rerender(
      <MemoryRouter initialEntries={[{ pathname: '/staff' }]}>
        <Routes>
          <Route path="/staff" element={<Harness show={false} />} />
        </Routes>
      </MemoryRouter>,
    )
    rerender(
      <MemoryRouter initialEntries={[{ pathname: '/staff' }]}>
        <Routes>
          <Route path="/staff" element={<Harness show={true} />} />
        </Routes>
      </MemoryRouter>,
    )
    await act(async () => {}) // flushes the remounted queue's own listReviews call

    expect(screen.queryByText('X')).not.toBeInTheDocument()
  })

  it('reports a failed load instead of showing an empty queue', async () => {
    vi.mocked(api.listReviews).mockRejectedValue(new api.ApiError(401, 'not_authenticated'))
    renderQueue()

    expect(await screen.findByRole('alert')).toHaveTextContent('not_authenticated')
    expect(screen.queryByText('אין פניות הממתינות להכרעה')).not.toBeInTheDocument()
  })

  it('shows entered_at, the queue order key, in the last column', async () => {
    vi.mocked(api.listReviews).mockResolvedValue(page([MEDICAL]))
    renderQueue()

    await screen.findByText('CASE-6FFF40DFB8DA')
    const headers = screen.getAllByRole('columnheader').map((cell) => cell.textContent)
    expect(headers).toContain('נכנסה לתור')
    expect(headers).not.toContain('עדכון אחרון')
  })

  it('offers a "load more" button when the API says there is a next page, and appends the next page', async () => {
    vi.mocked(api.listReviews).mockResolvedValue(page([MEDICAL], 'CURSOR-1'))
    renderQueue()
    await screen.findByText('CASE-6FFF40DFB8DA')
    expect(screen.queryByText('CASE-23FE645294B7')).not.toBeInTheDocument()

    vi.mocked(api.listReviews).mockResolvedValue(page([Z3], null))
    await userEvent.click(screen.getByRole('button', { name: 'טעינת עוד' }))

    expect(api.listReviews).toHaveBeenLastCalledWith({ cursor: 'CURSOR-1' })
    expect(await screen.findByText('CASE-23FE645294B7')).toBeInTheDocument()
    expect(screen.getByText('CASE-6FFF40DFB8DA')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'טעינת עוד' })).not.toBeInTheDocument())
  })

  it('keeps rows loaded by "load more" across a poll tick, with no duplicate keys (I1)', async () => {
    vi.useFakeTimers()
    vi.mocked(api.listReviews)
      .mockResolvedValueOnce(page([MEDICAL], 'CURSOR-1')) // initial mount
      .mockResolvedValueOnce(page([Z3], null)) // "load more" click
      .mockResolvedValueOnce(page([MEDICAL, Z3], null)) // poll tick after "load more"
    renderQueue()

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.getByText('CASE-6FFF40DFB8DA')).toBeInTheDocument()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'טעינת עוד' }))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.getByText('CASE-23FE645294B7')).toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(QUEUE_POLL_MS))

    // The poll's own request covers both rows (limit sized to what was already loaded),
    // and both are still on screen afterwards - no row was dropped, and none duplicated.
    expect(api.listReviews).toHaveBeenNthCalledWith(3, { limit: 50 })
    expect(screen.getByText('CASE-6FFF40DFB8DA')).toBeInTheDocument()
    expect(screen.getByText('CASE-23FE645294B7')).toBeInTheDocument()
    const rows = screen.getAllByRole('row')
    expect(rows).toHaveLength(3) // header + exactly one row per case - no duplicate keys
  })
})

describe('ReviewCase -> ReviewQueue: the decision notice (Task 7)', () => {
  function reviewContext(overrides: Partial<ReviewContext> = {}): ReviewContext {
    return {
      case_id: MEDICAL.case_id,
      patient_id: MEDICAL.patient_id,
      state: 'AwaitingHumanReview',
      escalation_kind: 'MedicalQuestion',
      escalated_from_state: 'Classifying',
      reasons: [],
      data: [],
      trace: [],
      shown_context_ref: 'ctx-1',
      ...overrides,
    }
  }

  it('shows a titled notice once and the queue no longer carries the decided case', async () => {
    vi.mocked(api.getContext).mockResolvedValue(reviewContext())
    vi.mocked(api.getReviewItem).mockResolvedValue(MEDICAL)
    vi.mocked(api.getMessageTemplates).mockResolvedValue([])
    vi.mocked(api.listCaseAppointments).mockResolvedValue({
      from: '2026-09-26T00:00:00Z',
      to: '2026-10-26T00:00:00Z',
      appointments: [],
      truncated: false,
    })
    vi.mocked(api.decide).mockResolvedValue({ case_id: MEDICAL.case_id, state: 'Completed' })
    // The decision already committed server-side before the queue is asked again, so the
    // one call the queue makes on mount (Task 3/5) already reflects it - no stale row.
    vi.mocked(api.listReviews).mockResolvedValue(page([]))

    render(
      <MemoryRouter initialEntries={[`/staff/cases/${MEDICAL.case_id}`]}>
        <TestAuthProvider value={authValue({ user: STAFF_USER })}>
          <Routes>
            <Route path="/staff" element={<ReviewQueue />} />
            <Route path="/staff/cases/:caseId" element={<ReviewCase />} />
          </Routes>
        </TestAuthProvider>
      </MemoryRouter>,
    )

    await userEvent.type(await screen.findByLabelText(/סיבת ההכרעה/), 'טופלה מול הרופא.')
    await userEvent.click(screen.getByRole('button', { name: 'סגירת הפנייה' }))

    // The queue screen, reached through the decision's own navigate.
    expect(await screen.findByText('ההכרעה נשמרה')).toHaveClass('title')
    expect(screen.getByText(`הפנייה ${MEDICAL.case_id} עברה למצב הושלמה (Completed).`)).toBeInTheDocument()
    expect(await screen.findByText('אין פניות הממתינות להכרעה')).toBeInTheDocument()
    expect(screen.queryByText(MEDICAL.case_id)).not.toBeInTheDocument()
  })

  it('titles the notice "הבקשה נשלחה" when a request to the patient is sent (PatientRequest onSent)', async () => {
    vi.mocked(api.getContext).mockResolvedValue(reviewContext())
    vi.mocked(api.getReviewItem).mockResolvedValue(MEDICAL)
    vi.mocked(api.getMessageTemplates).mockResolvedValue([
      { template_id: 'clarify_general', purpose: 'question', text: 'לא הצלחנו להבין', param: null, options: {} },
    ])
    vi.mocked(api.listCaseAppointments).mockResolvedValue({
      from: '2026-09-26T00:00:00Z',
      to: '2026-10-26T00:00:00Z',
      appointments: [],
      truncated: false,
    })
    vi.mocked(api.requestFromPatient).mockResolvedValue({
      case_id: MEDICAL.case_id,
      state: 'AwaitingPatientReply',
    })
    vi.mocked(api.listReviews).mockResolvedValue(page([]))

    render(
      <MemoryRouter initialEntries={[`/staff/cases/${MEDICAL.case_id}`]}>
        <TestAuthProvider value={authValue({ user: STAFF_USER })}>
          <Routes>
            <Route path="/staff" element={<ReviewQueue />} />
            <Route path="/staff/cases/:caseId" element={<ReviewCase />} />
          </Routes>
        </TestAuthProvider>
      </MemoryRouter>,
    )

    // Scoped to the "בקשה מהמטופל" panel: the decision form's own closing-message picker
    // uses the same "הודעה" label, so an unscoped query would be ambiguous.
    const panel = within((await screen.findByRole('heading', { name: 'בקשה מהמטופל' })).closest('section')!)
    await userEvent.selectOptions(panel.getByLabelText('הודעה'), 'clarify_general')
    await userEvent.type(panel.getByLabelText('סיבת הבקשה (פנימית)'), 'לא ברור מה נשאל')
    await userEvent.click(panel.getByRole('button', { name: 'שליחה למטופל' }))

    expect(await screen.findByText('הבקשה נשלחה')).toHaveClass('title')
    expect(screen.getByText(`נשלחה בקשה למטופל בפנייה ${MEDICAL.case_id}.`)).toBeInTheDocument()
  })

  it('titles the notice "התשובה נשלחה" when a clinical answer is sent (ClinicalAnswer onAnswered)', async () => {
    vi.mocked(api.getContext).mockResolvedValue(reviewContext())
    vi.mocked(api.getReviewItem).mockResolvedValue(MEDICAL)
    vi.mocked(api.getMessageTemplates).mockResolvedValue([])
    vi.mocked(api.listCaseAppointments).mockResolvedValue({
      from: '2026-09-26T00:00:00Z',
      to: '2026-10-26T00:00:00Z',
      appointments: [],
      truncated: false,
    })
    vi.mocked(api.answer).mockResolvedValue({ case_id: MEDICAL.case_id, state: 'Completed' })
    vi.mocked(api.listReviews).mockResolvedValue(page([]))

    render(
      <MemoryRouter initialEntries={[`/staff/cases/${MEDICAL.case_id}`]}>
        <TestAuthProvider value={authValue({ user: STAFF_USER })}>
          <Routes>
            <Route path="/staff" element={<ReviewQueue />} />
            <Route path="/staff/cases/:caseId" element={<ReviewCase />} />
          </Routes>
        </TestAuthProvider>
      </MemoryRouter>,
    )

    await userEvent.type(await screen.findByLabelText(/התשובה למטופל/), 'אין להפסיק את הטיפול.')
    await userEvent.type(screen.getByLabelText(/סיבה/), 'נענתה טלפונית')
    await userEvent.click(screen.getByRole('button', { name: 'אישור ושליחת התשובה' }))

    expect(await screen.findByText('התשובה נשלחה')).toHaveClass('title')
    expect(screen.getByText(`נשלחה תשובה למטופל בפנייה ${MEDICAL.case_id}, והפנייה נסגרה.`)).toBeInTheDocument()
  })
})
