import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { Appointment, AppointmentList } from '../api/types'
import { AppointmentsPanel } from './AppointmentsPanel'

function appointmentList(overrides: Partial<AppointmentList> = {}): AppointmentList {
  return {
    from: '2026-09-26T00:00:00Z',
    to: '2026-10-27T00:00:00Z',
    appointments: [],
    truncated: false,
    ...overrides,
  }
}

function appointment(overrides: Partial<Appointment> = {}): Appointment {
  return {
    appointment_id: 'APT-1',
    appointment_at: '2026-10-03T07:30:00Z',
    department: 'Neurology',
    doctor_name: 'ד"ר כהן',
    location: 'בניין ב, קומה 2',
    status: 'Scheduled',
    required_documents: ['CBC'],
    ...overrides,
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 8, 26, 10, 0))
})

afterEach(() => {
  vi.useRealTimers()
})

describe('AppointmentsPanel', () => {
  it('loads the default 30-day range on mount and shows it in the two date inputs', async () => {
    const load = vi.fn().mockResolvedValue(appointmentList())
    render(<AppointmentsPanel audience="patient" load={load} />)

    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))
    const [from, to] = load.mock.calls[0] as [Date, Date]
    expect(from).toEqual(new Date(2026, 8, 26, 0, 0))
    expect(to).toEqual(new Date(2026, 9, 27, 0, 0))

    expect(screen.getByLabelText('מתאריך')).toHaveValue('2026-09-26')
    expect(screen.getByLabelText('עד תאריך')).toHaveValue('2026-10-26')
  })

  it('lists an appointment with its formatted date, department, doctor, location, status and documents', async () => {
    const scheduled = appointment()
    const cancelled = appointment({
      appointment_id: 'APT-2',
      appointment_at: '2026-10-10T09:00:00Z',
      department: 'Cardiology',
      status: 'Cancelled',
      doctor_name: null,
      location: null,
      required_documents: [],
    })
    const unknownDepartment = appointment({
      appointment_id: 'APT-3',
      appointment_at: '2026-10-15T11:00:00Z',
      department: 'Radiology',
      doctor_name: null,
      location: null,
      required_documents: [],
    })
    const load = vi.fn().mockResolvedValue(
      appointmentList({ appointments: [scheduled, cancelled, unknownDepartment] }),
    )
    render(<AppointmentsPanel audience="patient" load={load} />)

    const formatted = new Intl.DateTimeFormat('he-IL', {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(scheduled.appointment_at))

    const scheduledTime = await screen.findByText(formatted)
    const scheduledItem = within(scheduledTime.closest('li')!)
    expect(scheduledItem.getByText('נוירולוגיה')).toBeInTheDocument()
    expect(scheduledItem.getByText('ד"ר כהן')).toBeInTheDocument()
    expect(scheduledItem.getByText('בניין ב, קומה 2')).toBeInTheDocument()
    expect(scheduledItem.getByText('מתוכנן')).toBeInTheDocument()
    expect(scheduledItem.getByText('ספירת דם מלאה')).toBeInTheDocument()

    const cancelledItem = within(screen.getByText('קרדיולוגיה').closest('li')!)
    expect(cancelledItem.getByText('בוטל')).toBeInTheDocument()

    expect(screen.getByText('Radiology')).toBeInTheDocument()
  })

  it('reloads with the range typed in the inputs when הצגה is pressed', async () => {
    const load = vi.fn().mockResolvedValue(appointmentList())
    const user = userEvent.setup()
    render(<AppointmentsPanel audience="patient" load={load} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-10-01' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2026-10-05' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))

    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
    const [from, to] = load.mock.calls[1] as [Date, Date]
    expect(from).toEqual(new Date(2026, 9, 1, 0, 0))
    expect(to).toEqual(new Date(2026, 9, 6, 0, 0))
  })

  it('refuses a reversed range client-side, without calling load', async () => {
    const load = vi.fn().mockResolvedValue(appointmentList())
    const user = userEvent.setup()
    render(<AppointmentsPanel audience="patient" load={load} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-10-10' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2026-10-05' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))

    expect(
      await screen.findByText('תאריך הסיום חייב להיות אחרי תאריך ההתחלה או באותו יום'),
    ).toBeInTheDocument()
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('refuses a span over 366 days, without calling load', async () => {
    const load = vi.fn().mockResolvedValue(appointmentList())
    const user = userEvent.setup()
    render(<AppointmentsPanel audience="patient" load={load} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-01-01' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2027-01-05' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))

    expect(await screen.findByText('אפשר להציג עד שנה אחת')).toBeInTheDocument()
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('shows an empty state when there are no appointments in the range', async () => {
    const load = vi.fn().mockResolvedValue(appointmentList({ appointments: [] }))
    render(<AppointmentsPanel audience="patient" load={load} />)

    expect(await screen.findByText('אין תורים בטווח שנבחר.')).toBeInTheDocument()
  })

  it('shows a truncation notice when the appointment-service capped the answer', async () => {
    const load = vi.fn().mockResolvedValue(appointmentList({ truncated: true, appointments: [appointment()] }))
    render(<AppointmentsPanel audience="patient" load={load} />)

    expect(
      await screen.findByText('מוצגים 100 התורים הראשונים בטווח. אפשר לצמצם את הטווח.'),
    ).toBeInTheDocument()
  })

  it('shows a retry-able error with no raw code for the patient, and retries with the same range', async () => {
    const load = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(503, 'appointments_unavailable'))
      .mockResolvedValueOnce(appointmentList())
    const user = userEvent.setup()
    render(<AppointmentsPanel audience="patient" load={load} />)

    expect(await screen.findByText('לא הצלחנו לטעון את התורים')).toBeInTheDocument()
    expect(screen.queryByText('appointments_unavailable')).not.toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('appointments_unavailable')

    await user.click(screen.getByRole('button', { name: 'נסה שוב' }))
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
    const [firstFrom, firstTo] = load.mock.calls[0] as [Date, Date]
    const [secondFrom, secondTo] = load.mock.calls[1] as [Date, Date]
    expect(secondFrom).toEqual(firstFrom)
    expect(secondTo).toEqual(firstTo)
  })

  it('shows the Hebrew label beside the code for staff', async () => {
    const load = vi.fn().mockRejectedValue(new ApiError(503, 'appointments_unavailable'))
    render(<AppointmentsPanel audience="staff" load={load} />)

    expect(await screen.findByText('מערכת התורים אינה זמינה')).toBeInTheDocument()
    expect(screen.getByText('appointments_unavailable')).toHaveClass('mono')
  })

  it('shows a neutral notice with no retry button when appointments are not enabled', async () => {
    const load = vi.fn().mockRejectedValue(new ApiError(404, 'appointments_not_enabled'))
    render(<AppointmentsPanel audience="patient" load={load} />)

    expect(await screen.findByText('רשימת התורים אינה זמינה כרגע.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'נסה שוב' })).not.toBeInTheDocument()
  })

  it('shows the patient_not_found sentence for the patient audience, with no code', async () => {
    const load = vi.fn().mockRejectedValue(new ApiError(404, 'patient_not_found'))
    render(<AppointmentsPanel audience="patient" load={load} />)

    expect(
      await screen.findByText('לא נמצאו פרטי המטופל במערכת התורים. אפשר לפנות למוקד המטופלים.'),
    ).toBeInTheDocument()
    expect(screen.queryByText('patient_not_found')).not.toBeInTheDocument()
  })
})
