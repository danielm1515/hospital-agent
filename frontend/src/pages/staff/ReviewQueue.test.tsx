import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import type { ReviewItem, ReviewQueuePage } from '../../api/types'
import { QUEUE_POLL_MS, ReviewQueue } from './ReviewQueue'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  listReviews: vi.fn(),
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

  it('shows the notice a finished decision navigated back with', async () => {
    vi.mocked(api.listReviews).mockResolvedValue(page([]))
    render(
      <MemoryRouter initialEntries={[{ pathname: '/staff', state: { notice: 'הפנייה CASE-1 עברה למצב Completed.' } }]}>
        <Routes>
          <Route path="/staff" element={<ReviewQueue />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText('הפנייה CASE-1 עברה למצב Completed.')).toBeInTheDocument()
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
