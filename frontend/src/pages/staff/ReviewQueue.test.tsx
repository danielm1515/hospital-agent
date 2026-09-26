import { useState } from 'react'
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

/** `count` distinct queue items, for pagination tests that need more than a handful. */
function makeItems(prefix: string, count: number): ReviewItem[] {
  return Array.from({ length: count }, (_, i) => ({ ...MEDICAL, case_id: `CASE-${prefix}${i}` }))
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
    const status = await screen.findByText('טוען פניות')
    expect(status).toHaveAttribute('role', 'status')
    expect(status.closest('.loader')).toBeInTheDocument()
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

  it('auto-dismisses the notice after exactly 8 s, not a moment before (M3)', async () => {
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

    await act(() => vi.advanceTimersByTimeAsync(7999))
    expect(screen.getByText('X')).toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(1))
    expect(screen.queryByText('X')).not.toBeInTheDocument()
  })

  it('renders no title when the decision state carried none (M4: the fallback is gone)', async () => {
    vi.mocked(api.listReviews).mockResolvedValue(page([]))
    render(
      <MemoryRouter initialEntries={[{ pathname: '/staff', state: { notice: 'X' } }]}>
        <Routes>
          <Route path="/staff" element={<ReviewQueue />} />
        </Routes>
      </MemoryRouter>,
    )
    const status = await screen.findByText('X')
    expect(status.closest('[role="status"]')?.querySelector('.title')).toBeNull()
  })

  it('closes the notice with the close button, and moves focus to the page heading (M8)', async () => {
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
    expect(screen.getByRole('heading', { name: 'תור הסלמות' })).toHaveFocus()
  })

  it('pauses the auto-dismiss while the notice is hovered, and resumes it on mouse-leave (M8)', async () => {
    vi.useFakeTimers()
    vi.mocked(api.listReviews).mockResolvedValue(page([]))
    render(
      <MemoryRouter initialEntries={[{ pathname: '/staff', state: { notice: 'X', title: 'ההכרעה נשמרה' } }]}>
        <Routes>
          <Route path="/staff" element={<ReviewQueue />} />
        </Routes>
      </MemoryRouter>,
    )
    const notice = screen.getByText('X')

    fireEvent.mouseEnter(notice)
    await act(() => vi.advanceTimersByTimeAsync(NOTICE_DISMISS_MS + 1000))
    expect(screen.getByText('X')).toBeInTheDocument() // paused - would have dismissed by now

    fireEvent.mouseLeave(notice)
    await act(() => vi.advanceTimersByTimeAsync(NOTICE_DISMISS_MS))
    expect(screen.queryByText('X')).not.toBeInTheDocument() // resumed, and ran to completion
  })

  it('pauses the auto-dismiss while the close button has focus, and resumes it on blur (M8)', async () => {
    vi.useFakeTimers()
    vi.mocked(api.listReviews).mockResolvedValue(page([]))
    render(
      <MemoryRouter initialEntries={[{ pathname: '/staff', state: { notice: 'X', title: 'ההכרעה נשמרה' } }]}>
        <Routes>
          <Route path="/staff" element={<ReviewQueue />} />
        </Routes>
      </MemoryRouter>,
    )
    const closeButton = screen.getByRole('button', { name: 'סגירה' })

    // A real `.focus()`/`.blur()` call, not a manually-fired synthetic event: it dispatches
    // the whole native cascade (`focus`/`focusin`, then `blur`/`focusout`), which is what
    // React's onFocus/onBlur delegation actually listens for.
    act(() => closeButton.focus())
    await act(() => vi.advanceTimersByTimeAsync(NOTICE_DISMISS_MS + 1000))
    expect(screen.getByText('X')).toBeInTheDocument() // paused - would have dismissed by now

    act(() => closeButton.blur())
    await act(() => vi.advanceTimersByTimeAsync(NOTICE_DISMISS_MS))
    expect(screen.queryByText('X')).not.toBeInTheDocument() // resumed, and ran to completion
  })

  it('does not show the notice again on remount - the history entry was replaced (Task 7, fix round 1 M9)', async () => {
    vi.mocked(api.listReviews).mockResolvedValue(page([]))

    // Fix round 1 (M9): one `render()` call, one `MemoryRouter`/history instance for the
    // whole test - the unmount/remount is driven by the harness's own state, through real
    // button clicks, not by swapping in a new router element across `rerender()` calls.
    function RemountHarness() {
      const [mounted, setMounted] = useState(true)
      return (
        <>
          {mounted && <ReviewQueue />}
          <button type="button" onClick={() => setMounted(false)}>
            test-unmount
          </button>
          <button type="button" onClick={() => setMounted(true)}>
            test-remount
          </button>
        </>
      )
    }

    render(
      <MemoryRouter initialEntries={[{ pathname: '/staff', state: { notice: 'X', title: 'ההכרעה נשמרה' } }]}>
        <Routes>
          <Route path="/staff" element={<RemountHarness />} />
        </Routes>
      </MemoryRouter>,
    )
    // The notice shows once, from the entry's original state.
    expect(screen.getByText('X')).toBeInTheDocument()

    // Unmount, then remount at the same route: the mount effect already replaced the
    // history entry's state with `null`, so a fresh ReviewQueue (a reload, or Back to
    // this same entry) reads no notice at all - this is what a real reload would see.
    await userEvent.click(screen.getByRole('button', { name: 'test-unmount' }))
    await userEvent.click(screen.getByRole('button', { name: 'test-remount' }))

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

  it('resizes the poll\'s limit to cover every row loaded via "load more" (I1, fix round 2 N2)', async () => {
    // 2 rows would still pass with a poll hard-coded to `limit: 50` (max(50, 2) is 50
    // either way) - 50 + 10 pins the actual resize, not just "some limit was sent".
    vi.useFakeTimers()
    const first = makeItems('A', 50)
    const more = makeItems('B', 10)
    vi.mocked(api.listReviews)
      .mockResolvedValueOnce(page(first, 'CURSOR-1')) // initial mount: 50 rows, more available
      .mockResolvedValueOnce(page(more, null)) // "load more": +10 rows, no next page
      .mockResolvedValueOnce(page([...first, ...more], null)) // poll tick: must ask limit 60
    renderQueue()

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.getByText(first[0].case_id)).toBeInTheDocument()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'טעינת עוד' }))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.getByText(more[0].case_id)).toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(QUEUE_POLL_MS))

    expect(api.listReviews).toHaveBeenNthCalledWith(3, { limit: 60 })
    expect(screen.getByText(first[0].case_id)).toBeInTheDocument()
    expect(screen.getByText(more[9].case_id)).toBeInTheDocument()
    const rows = screen.getAllByRole('row')
    expect(rows).toHaveLength(61) // header + all 60 cases - no row dropped, none duplicated
  })

  it('does not leave "load more" stuck busy after a poll races it, and load more works again (fix round 2 N1)', async () => {
    vi.useFakeTimers()
    const THIRD: ReviewItem = { ...Z3, case_id: 'CASE-THIRD00000001' }
    let resolveStaleLoadMore: (value: ReviewQueuePage) => void = () => {}
    vi.mocked(api.listReviews)
      .mockResolvedValueOnce(page([MEDICAL], 'CURSOR-1')) // initial mount
      .mockImplementationOnce(() => new Promise((resolve) => { resolveStaleLoadMore = resolve })) // "load more"
      .mockResolvedValueOnce(page([MEDICAL, Z3], 'CURSOR-2')) // poll tick, before that resolves
      .mockResolvedValueOnce(page([THIRD], null)) // "load more" clicked again afterwards
    renderQueue()

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.getByText('CASE-6FFF40DFB8DA')).toBeInTheDocument()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'טעינת עוד' }))
      await Promise.resolve()
    })
    expect(screen.getByRole('button', { name: 'טעינת עוד' })).toHaveAttribute('aria-busy', 'true')

    // A poll tick fires while that "load more" is still in flight (and never resolves).
    await act(() => vi.advanceTimersByTimeAsync(QUEUE_POLL_MS))

    expect(screen.getByRole('button', { name: 'טעינת עוד' })).not.toHaveAttribute('aria-busy', 'true')
    expect(screen.getByText('CASE-23FE645294B7')).toBeInTheDocument() // the poll's own (replaced) list

    // Clicking it again must actually run a new request, not stay silently stuck.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'טעינת עוד' }))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.getByText(THIRD.case_id)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'טעינת עוד' })).not.toBeInTheDocument()

    // The stale "load more" from before the poll may still resolve later - it must not
    // resurrect anything or throw.
    await act(async () => {
      resolveStaleLoadMore(page([MEDICAL, Z3], null))
      await Promise.resolve()
    })
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

  it('decides a case from the queue: the notice shows once, and a reload finds no notice with the case gone (I1)', async () => {
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
    // Step 1: the queue's very first call still carries the case; the decision commits
    // server-side before the queue is ever asked again, so every call after that does not.
    vi.mocked(api.listReviews).mockResolvedValueOnce(page([MEDICAL])).mockResolvedValue(page([]))

    // Toggled by real button clicks below, so "unmount, then remount" (step 4) is an honest
    // reload simulation under the one router this test renders (fix round 1 M9) - not a
    // `rerender()` swap of a whole new router element.
    function StaffHarness() {
      const [mounted, setMounted] = useState(true)
      return (
        <>
          {mounted && <ReviewQueue />}
          <button type="button" onClick={() => setMounted(false)}>
            test-unmount
          </button>
          <button type="button" onClick={() => setMounted(true)}>
            test-remount
          </button>
        </>
      )
    }

    render(
      <MemoryRouter initialEntries={['/staff']}>
        <TestAuthProvider value={authValue({ user: STAFF_USER })}>
          <Routes>
            <Route path="/staff" element={<StaffHarness />} />
            <Route path="/staff/cases/:caseId" element={<ReviewCase />} />
          </Routes>
        </TestAuthProvider>
      </MemoryRouter>,
    )

    // Step 2: click the row, then decide.
    await userEvent.click(await screen.findByText(MEDICAL.case_id))
    await userEvent.type(await screen.findByLabelText(/סיבת ההכרעה/), 'טופלה מול הרופא.')
    await userEvent.click(screen.getByRole('button', { name: 'סגירת הפנייה' }))

    // Step 3: the notice shows (title and text), and the row is gone.
    expect(await screen.findByText('ההכרעה נשמרה')).toHaveClass('title')
    // Step 6: the new State is visible right here, through the notice's own "(Completed)" -
    // the case genuinely changed state, not merely vanished from the table.
    expect(screen.getByText(`הפנייה ${MEDICAL.case_id} עברה למצב הושלמה (Completed).`)).toBeInTheDocument()
    expect(await screen.findByText('אין פניות הממתינות להכרעה')).toBeInTheDocument()
    expect(screen.queryByText(MEDICAL.case_id)).not.toBeInTheDocument()
    expect(api.listReviews).toHaveBeenCalledTimes(2)

    // Step 4: unmount and remount ReviewQueue under the same router, as a reload would.
    await userEvent.click(screen.getByRole('button', { name: 'test-unmount' }))
    await userEvent.click(screen.getByRole('button', { name: 'test-remount' }))

    // Step 5: the notice is absent, and the queue was asked again (a third call).
    expect(screen.queryByText('ההכרעה נשמרה')).not.toBeInTheDocument()
    expect(screen.queryByText(`הפנייה ${MEDICAL.case_id} עברה למצב הושלמה (Completed).`)).not.toBeInTheDocument()
    expect(api.listReviews).toHaveBeenCalledTimes(3)
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
