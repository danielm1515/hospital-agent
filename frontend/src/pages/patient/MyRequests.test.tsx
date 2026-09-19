import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import { MyRequests } from './MyRequests'
import { patientView } from './fixtures'
import { authValue, PATIENT_USER, TestAuthProvider } from '../../test/helpers'

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>()
  return { ...actual, listRequests: vi.fn() }
})

const listRequests = vi.mocked(api.listRequests)

function renderList() {
  return render(
    <MemoryRouter initialEntries={['/patient']}>
      <TestAuthProvider value={authValue({ user: PATIENT_USER })}>
        <Routes>
          <Route path="/patient" element={<MyRequests />} />
          <Route path="/patient/new" element={<h1>פנייה חדשה</h1>} />
          <Route path="/patient/requests/:caseId" element={<h1>פרטי פנייה</h1>} />
        </Routes>
      </TestAuthProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  listRequests.mockReset()
})

describe('MyRequests', () => {
  it('renders one card per request, with the status pill, the date and the text cut at 120 characters', async () => {
    const long = 'א'.repeat(200)
    listRequests.mockResolvedValue([
      patientView({ case_id: 'CASE-1', status: 'in_progress', request_text: long }),
      patientView({ case_id: 'CASE-2', status: 'completed', request_text: 'קצרה', message: 'נשלח' }),
    ])
    const { container } = renderList()

    expect(await screen.findByText(`${'א'.repeat(120)}…`)).toBeInTheDocument()
    expect(screen.getByText('קצרה')).toBeInTheDocument()
    expect(screen.getByText('בטיפול')).toBeInTheDocument()
    expect(screen.getByText('הושלמה')).toBeInTheDocument()
    expect(container.querySelectorAll('.req-card')).toHaveLength(2)
    expect(container.querySelector('time')).toHaveAttribute('datetime', '2026-09-19T22:12:47.693947Z')
    expect(screen.getAllByRole('link')[0]).toHaveAttribute('href', '/patient/requests/CASE-1')
  })

  it('shows an empty state when there are no requests', async () => {
    listRequests.mockResolvedValue([])
    renderList()
    expect(await screen.findByText(/עדיין אין פניות/)).toBeInTheDocument()
  })

  it('goes to the new-request screen', async () => {
    const user = userEvent.setup()
    listRequests.mockResolvedValue([])
    renderList()
    await user.click(await screen.findByRole('button', { name: 'פנייה חדשה' }))
    expect(screen.getByRole('heading', { name: 'פנייה חדשה' })).toBeInTheDocument()
  })

  it('shows a Hebrew error and no internal code when the call fails', async () => {
    listRequests.mockRejectedValue(new ApiError(500, 'boom'))
    renderList()
    expect(await screen.findByRole('alert')).toHaveTextContent('אירעה תקלה')
    expect(screen.queryByText(/boom/)).not.toBeInTheDocument()
  })
})

/** Fake timers freeze `waitFor`, so the pending promises are flushed by hand. */
async function flush() {
  await act(async () => {})
}

describe('MyRequests polling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('refreshes every 3 s while a request is still moving', async () => {
    listRequests.mockResolvedValue([patientView({ status: 'received' })])
    renderList()
    await flush()
    expect(listRequests).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(3000)
    })
    expect(listRequests).toHaveBeenCalledTimes(2)

    await act(async () => {
      vi.advanceTimersByTime(3000)
    })
    expect(listRequests).toHaveBeenCalledTimes(3)
  })

  it('stops polling once nothing is moving', async () => {
    listRequests.mockResolvedValue([patientView({ status: 'completed', message: 'נשלח' })])
    renderList()
    await flush()
    expect(listRequests).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(9000)
    })
    expect(listRequests).toHaveBeenCalledTimes(1)
  })
})
