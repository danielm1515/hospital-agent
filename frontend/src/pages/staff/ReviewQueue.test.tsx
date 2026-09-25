import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import type { ReviewItem } from '../../api/types'
import { ReviewQueue } from './ReviewQueue'

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
  updated_at: '2026-09-19T22:12:39.693277Z',
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
  updated_at: '2026-09-19T22:14:02.100000Z',
  human_engaged: false,
  returned_by: null,
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

describe('ReviewQueue', () => {
  it('renders a row per queue item, with the Hebrew label and the code', async () => {
    vi.mocked(api.listReviews).mockResolvedValue([MEDICAL, Z3])
    renderQueue()

    expect(await screen.findByText('CASE-6FFF40DFB8DA')).toBeInTheDocument()
    expect(screen.getByText('שאלה רפואית')).toBeInTheDocument()
    expect(screen.getByText('MedicalQuestion')).toBeInTheDocument()
    expect(screen.getByText('Classifying')).toBeInTheDocument()
    expect(screen.getByText('hours_until:20')).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(3) // header + two cases
  })

  it('opens the case when its row is clicked', async () => {
    vi.mocked(api.listReviews).mockResolvedValue([MEDICAL])
    renderQueue()

    await userEvent.click(await screen.findByText('P-10041'))

    expect(await screen.findByRole('heading', { name: 'מסך הפנייה' })).toBeInTheDocument()
  })

  it('shows the empty state when nothing waits for a decision', async () => {
    vi.mocked(api.listReviews).mockResolvedValue([])
    renderQueue()

    expect(await screen.findByText('אין פניות הממתינות להכרעה')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows the notice a finished decision navigated back with', async () => {
    vi.mocked(api.listReviews).mockResolvedValue([])
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
})
