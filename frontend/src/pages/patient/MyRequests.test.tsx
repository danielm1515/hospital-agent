import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import type { Appointment, AppointmentList } from '../../api/types'
import { MyRequests } from './MyRequests'
import { patientView } from './fixtures'
import { authValue, PATIENT_USER, TestAuthProvider } from '../../test/helpers'

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>()
  return { ...actual, listRequests: vi.fn(), listMyAppointments: vi.fn(), getPatientInstruction: vi.fn() }
})

const listRequests = vi.mocked(api.listRequests)
const listMyAppointments = vi.mocked(api.listMyAppointments)
const getPatientInstruction = vi.mocked(api.getPatientInstruction)

function appointment(overrides: Partial<Appointment> = {}): Appointment {
  return {
    appointment_id: 'APT-1',
    appointment_at: '2026-10-03T07:30:00Z',
    department: 'Cardiology',
    doctor_name: 'ד"ר לוי',
    location: 'בניין א, קומה 1',
    status: 'Scheduled',
    required_documents: [],
    ...overrides,
  }
}

function appointmentList(overrides: Partial<AppointmentList> = {}): AppointmentList {
  return {
    from: '2026-09-26T00:00:00Z',
    to: '2026-10-26T00:00:00Z',
    appointments: [appointment()],
    truncated: false,
    ...overrides,
  }
}

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
  listMyAppointments.mockReset()
  listMyAppointments.mockResolvedValue(appointmentList())
})

describe('MyRequests', () => {
  it('shows the appointments panel above the requests list, with the loaded appointment', async () => {
    listRequests.mockResolvedValue([])
    renderList()

    const appointmentsHeading = await screen.findByRole('heading', { name: 'התורים שלי' })
    const requestsHeading = await screen.findByRole('heading', { name: 'הפניות שלי' })
    expect(
      appointmentsHeading.compareDocumentPosition(requestsHeading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(screen.getByText('קרדיולוגיה')).toBeInTheDocument()
  })

  it('reads an appointment’s preparation text through the patient route (sub-project 18, D12)', async () => {
    listRequests.mockResolvedValue([])
    listMyAppointments.mockResolvedValue(
      appointmentList({
        appointments: [
          appointment({
            exam_type: { code: 'CARD_ECHO', label: 'אקו לב' },
            instruction: { source_id: 'INSTR-CARD-ECHO', version: '1', title: 'לפני אקו לב' },
          }),
        ],
      }),
    )
    getPatientInstruction.mockResolvedValue({
      source_id: 'INSTR-CARD-ECHO',
      version: '1',
      title: 'לפני אקו לב',
      text: 'אין צורך בהכנה מיוחדת.',
    })
    renderList()

    await userEvent.click(await screen.findByRole('button', { name: 'הצגת הוראות ההכנה' }))
    expect(await screen.findByText('אין צורך בהכנה מיוחדת.')).toBeInTheDocument()
    expect(getPatientInstruction).toHaveBeenCalledWith('INSTR-CARD-ECHO', '1')
    expect(document.body).not.toHaveTextContent(/INSTR-|CARD_ECHO/)
  })

  it('does not hide the requests list when the appointments load fails', async () => {
    listRequests.mockResolvedValue([patientView({ case_id: 'CASE-1' })])
    listMyAppointments.mockRejectedValue(new ApiError(500, 'boom'))
    const { container } = renderList()

    expect(await screen.findByRole('heading', { name: 'הפניות שלי' })).toBeInTheDocument()
    expect(container.querySelectorAll('.req-card')).toHaveLength(1)
  })

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
    expect(container.querySelector('.req-date')).toHaveAttribute('datetime', '2026-09-19T22:12:47.693947Z')
    expect(container.querySelector('.req-date')).toHaveTextContent('נפתחה ב־')
    expect(screen.getAllByRole('link')[0]).toHaveAttribute('href', '/patient/requests/CASE-1')
  })

  it('says in one line what each status means for the patient', async () => {
    listRequests.mockResolvedValue([
      patientView({ case_id: 'CASE-1', status: 'needs_document', missing_document_ids: ['blood_test'] }),
      patientView({ case_id: 'CASE-2', status: 'in_review' }),
    ])
    const { container } = renderList()
    await screen.findByText('ממתינה למסמך')
    expect([...container.querySelectorAll('.req-note')].map((note) => note.textContent)).toEqual([
      'כדי להמשיך נדרש מסמך שעדיין לא הועלה.',
      'איש צוות בודק את הפנייה. מידע רפואי אינו נמסר אוטומטית.',
    ])
    // §12.3: the line is about the abstract status alone - never a kind or a reason.
    expect(screen.queryByText(/MedicalQuestion|AwaitingHumanReview/)).not.toBeInTheDocument()
  })

  it('shows the shared loading status while the list is still loading', async () => {
    listRequests.mockReturnValue(new Promise(() => {})) // never resolves
    renderList()
    const status = await screen.findByText('טוען פניות')
    expect(status).toHaveAttribute('role', 'status')
    expect(status.closest('.loader')).toBeInTheDocument()
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

describe('only the newest five at first', () => {
  const many = (n: number) =>
    Array.from({ length: n }, (_, i) =>
      patientView({ case_id: `CASE-${i}`, request_text: `פנייה מספר ${i}`, status: 'completed' }),
    )

  it('asks for one more than it shows, and offers the rest when there are others', async () => {
    listRequests.mockResolvedValue(many(6))
    renderList()
    expect(await screen.findByText('פנייה מספר 4')).toBeInTheDocument()
    expect(listRequests).toHaveBeenCalledWith({ limit: 6 })
    expect(screen.queryByText('פנייה מספר 5')).not.toBeInTheDocument()
    expect(screen.getAllByRole('listitem').filter((li) => li.classList.contains('req-card'))).toHaveLength(5)
    expect(screen.getByRole('button', { name: 'הצגת כל הפניות' })).toBeInTheDocument()
  })

  it('loads every request when asked', async () => {
    listRequests.mockResolvedValueOnce(many(6)).mockResolvedValue(many(9))
    renderList()
    await userEvent.click(await screen.findByRole('button', { name: 'הצגת כל הפניות' }))
    expect(await screen.findByText('פנייה מספר 8')).toBeInTheDocument()
    expect(listRequests).toHaveBeenLastCalledWith()
    expect(screen.queryByRole('button', { name: 'הצגת כל הפניות' })).not.toBeInTheDocument()
  })

  it('offers nothing more when five or fewer exist', async () => {
    listRequests.mockResolvedValue(many(5))
    renderList()
    expect(await screen.findByText('פנייה מספר 4')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'הצגת כל הפניות' })).not.toBeInTheDocument()
  })

  it('shows the button busy while every request is loading', async () => {
    let finish!: (value: ReturnType<typeof many>) => void
    listRequests
      .mockResolvedValueOnce(many(6))
      .mockReturnValueOnce(new Promise((resolve) => (finish = resolve)))
    renderList()
    await userEvent.click(await screen.findByRole('button', { name: 'הצגת כל הפניות' }))
    const busy = await screen.findByRole('button', { name: /טוען את כל הפניות/ })
    expect(busy).toHaveAttribute('aria-busy', 'true')
    await act(async () => finish(many(9)))
    expect(await screen.findByText('פנייה מספר 8')).toBeInTheDocument()
  })

  it('offers the button again when loading every request fails', async () => {
    listRequests.mockResolvedValueOnce(many(6)).mockRejectedValueOnce(new ApiError(503, 'unavailable')).mockResolvedValue(many(6))
    renderList()
    await userEvent.click(await screen.findByRole('button', { name: 'הצגת כל הפניות' }))
    expect(await screen.findByText('לא הצלחנו לטעון את הפניות')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'הצגת כל הפניות' })).not.toHaveAttribute('aria-busy')
  })
})
