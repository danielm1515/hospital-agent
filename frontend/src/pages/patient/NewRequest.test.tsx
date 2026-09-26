import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import type { Appointment, AppointmentList } from '../../api/types'
import { NewRequest } from './NewRequest'
import { patientView } from './fixtures'
import { authValue, PATIENT_USER, TestAuthProvider } from '../../test/helpers'

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>()
  return { ...actual, createRequest: vi.fn(), listMyAppointments: vi.fn() }
})

const createRequest = vi.mocked(api.createRequest)
const listMyAppointments = vi.mocked(api.listMyAppointments)

function appointment(overrides: Partial<Appointment> = {}): Appointment {
  return {
    appointment_id: 'APT-8391',
    appointment_at: '2026-10-03T07:30:00Z',
    department: 'Neurology',
    doctor_name: 'Dr. Cohen',
    location: null,
    status: 'Scheduled',
    required_documents: [],
    exam_type: { code: 'NEURO_VISIT', label: 'ביקור במרפאה נוירולוגית' },
    instruction: { source_id: 'INSTR-NEURO-VISIT', version: '1', title: 'לפני הביקור' },
    ...overrides,
  }
}

const neuro = appointment()
const stress = appointment({
  appointment_id: 'APT-8392',
  appointment_at: '2026-10-07T06:00:00Z',
  department: 'Cardiology',
  exam_type: { code: 'CARD_STRESS', label: 'מבחן מאמץ' },
  instruction: { source_id: 'INSTR-CARD-STRESS', version: '1', title: 'לפני מבחן מאמץ' },
})

function list(appointments: Appointment[]): AppointmentList {
  return { from: '2026-09-26T00:00:00Z', to: '2026-12-25T00:00:00Z', appointments, truncated: false }
}

function renderNew() {
  return render(
    <MemoryRouter initialEntries={['/patient/new']}>
      <TestAuthProvider value={authValue({ user: PATIENT_USER })}>
        <Routes>
          <Route path="/patient" element={<h1>הפניות שלי</h1>} />
          <Route path="/patient/new" element={<NewRequest />} />
          <Route path="/patient/requests/:caseId" element={<h1>פרטי פנייה</h1>} />
        </Routes>
      </TestAuthProvider>
    </MemoryRouter>,
  )
}

const picker = () => screen.getByLabelText('לאיזה תור הפנייה?') as HTMLSelectElement
const optionTexts = () => within(picker()).getAllByRole('option').map((option) => option.textContent)

beforeEach(() => {
  createRequest.mockReset()
  listMyAppointments.mockReset()
  listMyAppointments.mockResolvedValue(list([]))
})

describe('NewRequest', () => {
  it('posts the trimmed text and goes to the new case', async () => {
    const user = userEvent.setup()
    createRequest.mockResolvedValue(patientView({ case_id: 'CASE-NEW' }))
    renderNew()
    await screen.findByLabelText('לאיזה תור הפנייה?')

    await user.type(screen.getByLabelText('תוכן הפנייה'), '  מתי התור שלי?  ')
    await user.click(screen.getByRole('button', { name: 'שליחת הפנייה' }))

    expect(createRequest).toHaveBeenCalledWith('מתי התור שלי?', undefined)
    expect(await screen.findByRole('heading', { name: 'פרטי פנייה' })).toBeInTheDocument()
  })

  it('counts the characters, up to 2000', async () => {
    const user = userEvent.setup()
    renderNew()
    const field = screen.getByLabelText('תוכן הפנייה')
    expect(field).toHaveAttribute('maxlength', '2000')
    await user.type(field, 'שלום')
    expect(screen.getByText('4/2000')).toBeInTheDocument()
  })

  it('refuses an empty request without calling the API', async () => {
    const user = userEvent.setup()
    renderNew()
    await screen.findByLabelText('לאיזה תור הפנייה?')
    await user.type(screen.getByLabelText('תוכן הפנייה'), '   ')
    await user.click(screen.getByRole('button', { name: 'שליחת הפנייה' }))

    expect(createRequest).not.toHaveBeenCalled()
    expect(screen.getByText('יש לכתוב את תוכן הפנייה.')).toBeInTheDocument()
    expect(screen.getByLabelText('תוכן הפנייה')).toHaveAttribute('aria-invalid', 'true')
  })

  it('keeps the patient on the form when the call fails', async () => {
    const user = userEvent.setup()
    createRequest.mockRejectedValue(new ApiError(422, 'validation_error'))
    renderNew()
    await screen.findByLabelText('לאיזה תור הפנייה?')

    await user.type(screen.getByLabelText('תוכן הפנייה'), 'שאלה')
    await user.click(screen.getByRole('button', { name: 'שליחת הפנייה' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('הפרטים שהוזנו אינם תקינים.')
    expect(screen.getByRole('heading', { name: 'פנייה חדשה' })).toBeInTheDocument()
  })
})

describe('NewRequest: which appointment (sub-project 18, design D5)', () => {
  it('asks for the next 90 days of the patient’s own appointments, once', async () => {
    renderNew()
    await screen.findByLabelText('לאיזה תור הפנייה?')
    expect(listMyAppointments).toHaveBeenCalledTimes(1)
    const [from, to] = listMyAppointments.mock.calls[0]
    expect(to.getTime() - from.getTime()).toBe(90 * 24 * 60 * 60 * 1000)
    expect(Math.abs(from.getTime() - Date.now())).toBeLessThan(60_000)
  })

  it('shows the shared loader and holds the send button while the list loads', async () => {
    listMyAppointments.mockReturnValue(new Promise(() => {}))
    renderNew()
    expect(screen.getByText('טוען את התורים שלך')).toHaveAttribute('role', 'status')
    expect(screen.getByRole('button', { name: 'שליחת הפנייה' })).toBeDisabled()
  })

  it('pre-selects the one upcoming appointment, offers the nearest too, and sends its id', async () => {
    const user = userEvent.setup()
    listMyAppointments.mockResolvedValue(list([neuro]))
    createRequest.mockResolvedValue(patientView({ case_id: 'CASE-NEW' }))
    const { container } = renderNew()

    await waitFor(() => expect(picker().value).toBe('APT-8391'))
    expect(optionTexts()).toEqual([expect.stringContaining('ביקור במרפאה נוירולוגית · נוירולוגיה · 3.10.2026 בשעה 10:30'), 'התור הקרוב ביותר'])
    // The patient never sees a code: not the appointment id, the exam code or the source.
    expect(container.textContent).not.toMatch(/APT-|NEURO_|INSTR-/)

    await user.type(screen.getByLabelText('תוכן הפנייה'), 'מה להביא?')
    await user.click(screen.getByRole('button', { name: 'שליחת הפנייה' }))
    expect(createRequest).toHaveBeenCalledWith('מה להביא?', 'APT-8391')
  })

  it('sends no id when the patient picks the nearest appointment', async () => {
    const user = userEvent.setup()
    listMyAppointments.mockResolvedValue(list([neuro]))
    createRequest.mockResolvedValue(patientView({ case_id: 'CASE-NEW' }))
    renderNew()
    await waitFor(() => expect(picker().value).toBe('APT-8391'))

    await user.selectOptions(picker(), 'התור הקרוב ביותר')
    await user.type(screen.getByLabelText('תוכן הפנייה'), 'מה להביא?')
    await user.click(screen.getByRole('button', { name: 'שליחת הפנייה' }))
    expect(createRequest).toHaveBeenCalledWith('מה להביא?', undefined)
  })

  it('offers only the nearest appointment when there is none upcoming', async () => {
    renderNew()
    await screen.findByLabelText('לאיזה תור הפנייה?')
    expect(optionTexts()).toEqual(['התור הקרוב ביותר'])
    expect(screen.getByText('לא נמצאו תורים מתוכננים ב־90 הימים הקרובים.')).toBeInTheDocument()
  })

  it('makes the choice mandatory with more than one upcoming appointment', async () => {
    const user = userEvent.setup()
    const cancelled = appointment({ appointment_id: 'APT-9', status: 'Cancelled' })
    listMyAppointments.mockResolvedValue(list([stress, cancelled, neuro]))
    createRequest.mockResolvedValue(patientView({ case_id: 'CASE-NEW' }))
    renderNew()
    await waitFor(() => expect(optionTexts()).toHaveLength(3))

    // A placeholder, the two Scheduled appointments earliest first, and no "nearest".
    expect(picker().value).toBe('')
    expect(optionTexts()[0]).toBe('יש לבחור תור')
    expect(optionTexts()[1]).toContain('נוירולוגיה')
    expect(optionTexts()[2]).toContain('מבחן מאמץ')
    expect(optionTexts()).not.toContain('התור הקרוב ביותר')

    await user.type(screen.getByLabelText('תוכן הפנייה'), 'מה להביא?')
    await user.click(screen.getByRole('button', { name: 'שליחת הפנייה' }))
    expect(createRequest).not.toHaveBeenCalled()
    expect(screen.getByText('יש לבחור את התור שהפנייה עוסקת בו.')).toBeInTheDocument()
    expect(picker()).toHaveAttribute('aria-invalid', 'true')

    await user.selectOptions(picker(), 'APT-8392')
    await user.click(screen.getByRole('button', { name: 'שליחת הפנייה' }))
    expect(createRequest).toHaveBeenCalledWith('מה להביא?', 'APT-8392')
  })

  it('leaves only the nearest appointment, with a quiet note, when the list fails to load', async () => {
    const user = userEvent.setup()
    listMyAppointments.mockRejectedValue(new ApiError(503, 'appointments_unavailable'))
    createRequest.mockResolvedValue(patientView({ case_id: 'CASE-NEW' }))
    renderNew()

    const note = await screen.findByText(
      'לא הצלחנו לטעון את רשימת התורים, ולכן הפנייה תתייחס לתור הקרוב ביותר.',
    )
    expect(note).toHaveClass('hint')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(optionTexts()).toEqual(['התור הקרוב ביותר'])
    expect(screen.queryByText(/appointments_unavailable/)).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('תוכן הפנייה'), 'מה להביא?')
    await user.click(screen.getByRole('button', { name: 'שליחת הפנייה' }))
    expect(createRequest).toHaveBeenCalledWith('מה להביא?', undefined)
  })

  it('treats appointments_not_enabled like any failed load', async () => {
    listMyAppointments.mockRejectedValue(new ApiError(404, 'appointments_not_enabled'))
    renderNew()
    expect(
      await screen.findByText('לא הצלחנו לטעון את רשימת התורים, ולכן הפנייה תתייחס לתור הקרוב ביותר.'),
    ).toBeInTheDocument()
    expect(optionTexts()).toEqual(['התור הקרוב ביותר'])
  })
})

describe('NewRequest: the pre-send check (design D5)', () => {
  async function chooseNeuroAndWrite(text: string) {
    const user = userEvent.setup()
    listMyAppointments.mockResolvedValue(list([neuro, stress]))
    createRequest.mockResolvedValue(patientView({ case_id: 'CASE-NEW' }))
    renderNew()
    await waitFor(() => expect(optionTexts()).toHaveLength(3))
    await user.selectOptions(picker(), 'APT-8391')
    await user.type(screen.getByLabelText('תוכן הפנייה'), text)
    await user.click(screen.getByRole('button', { name: 'שליחת הפנייה' }))
    return user
  }

  it('offers to switch when the text names another appointment, and sends nothing yet', async () => {
    await chooseNeuroAndWrite('מה ההכנה למבחן מאמץ ב-7/10?')
    expect(createRequest).not.toHaveBeenCalled()
    expect(
      screen.getByText('נראה שכתבת על מבחן מאמץ · קרדיולוגיה · 7.10.2026 בשעה 09:00 - לעבור לתור הזה?'),
    ).toBeInTheDocument()
  })

  it('switches to that appointment and sends', async () => {
    const user = await chooseNeuroAndWrite('מה ההכנה למבחן מאמץ?')
    await user.click(screen.getByRole('button', { name: 'כן, לעבור לתור הזה' }))
    expect(createRequest).toHaveBeenCalledWith('מה ההכנה למבחן מאמץ?', 'APT-8392')
  })

  it('sends as is when the patient keeps the chosen appointment', async () => {
    const user = await chooseNeuroAndWrite('מה ההכנה למבחן מאמץ?')
    await user.click(screen.getByRole('button', { name: 'לא, לשלוח כמו שזה' }))
    expect(createRequest).toHaveBeenCalledWith('מה ההכנה למבחן מאמץ?', 'APT-8391')
  })

  it('drops the offer once the text changes', async () => {
    const user = await chooseNeuroAndWrite('מה ההכנה למבחן מאמץ?')
    expect(screen.getByRole('button', { name: 'כן, לעבור לתור הזה' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('תוכן הפנייה'), ' ')
    expect(screen.queryByRole('button', { name: 'כן, לעבור לתור הזה' })).not.toBeInTheDocument()
  })

  it('sends straight away when the text names no other appointment', async () => {
    await chooseNeuroAndWrite('מה ההכנה לביקור בנוירולוגיה?')
    expect(createRequest).toHaveBeenCalledWith('מה ההכנה לביקור בנוירולוגיה?', 'APT-8391')
  })
})

describe('NewRequest: unmount safety', () => {
  it('ignores a list that answers after the screen is gone', async () => {
    let resolve: (value: AppointmentList) => void = () => {}
    listMyAppointments.mockReturnValue(new Promise((done) => (resolve = done)))
    const { unmount } = renderNew()
    unmount()
    resolve(list([neuro]))
    await Promise.resolve()
    expect(listMyAppointments).toHaveBeenCalledTimes(1)
  })
})
