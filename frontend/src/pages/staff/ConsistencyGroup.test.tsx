import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import type { Consistency } from '../../api/types'
import { ConsistencyGroup } from './ConsistencyGroup'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  getConsistency: vi.fn(),
}))

const PROVED: Consistency = {
  engine: 'z3',
  all_proved: true,
  queries: [
    { property: 'P1', description: 'OPA allows what Prolog blocks', result: 'unsat', proved: true },
    { property: 'P2', description: 'medical answer without approval', result: 'unsat', proved: true },
  ],
}

beforeEach(() => {
  vi.mocked(api.getConsistency).mockReset()
})

describe('ConsistencyGroup', () => {
  it('runs the Z3 proofs and shows each property in Hebrew beside its code and result', async () => {
    vi.mocked(api.getConsistency).mockResolvedValue(PROVED)
    render(<ConsistencyGroup />)

    expect(await screen.findByText('OPA לא מאשר מה ש-Prolog חוסם')).toBeInTheDocument()
    expect(screen.getByText('OPA allows what Prolog blocks')).toBeInTheDocument()
    expect(screen.getAllByText('UNSAT')).toHaveLength(2)
    expect(screen.getByText(/כל 2 השאילתות החזירו UNSAT/)).toHaveClass('proved')
  })

  it('marks a property Z3 found a counterexample for', async () => {
    vi.mocked(api.getConsistency).mockResolvedValue({
      ...PROVED,
      all_proved: false,
      queries: [{ property: 'P5', description: 'allow after attempts exhausted', result: 'sat', proved: false }],
    })
    render(<ConsistencyGroup />)

    expect(await screen.findByText('לפחות תכונה אחת לא הוכחה')).toHaveClass('failed')
    expect(screen.getByText('SAT').closest('.consistency-item')).toHaveClass('failed')
  })

  it('shows a failed run with its code and runs again on demand', async () => {
    vi.mocked(api.getConsistency).mockRejectedValueOnce(new api.ApiError(503, 'unavailable'))
    render(<ConsistencyGroup />)
    expect(await screen.findByText('unavailable')).toBeInTheDocument()

    vi.mocked(api.getConsistency).mockResolvedValue(PROVED)
    await userEvent.click(screen.getByRole('button', { name: 'הרצה חוזרת' }))
    expect(await screen.findByText('OPA לא מאשר מה ש-Prolog חוסם')).toBeInTheDocument()
    expect(api.getConsistency).toHaveBeenCalledTimes(2)
  })
})
