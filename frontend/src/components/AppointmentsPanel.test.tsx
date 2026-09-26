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

  it('refuses a range where "to" is exactly one day before "from", without calling load', async () => {
    const load = vi.fn().mockResolvedValue(appointmentList())
    const user = userEvent.setup()
    render(<AppointmentsPanel audience="patient" load={load} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-10-10' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2026-10-09' } })
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

/** A promise plus its own `resolve`/`reject`, for controlling settle order by hand. */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('AppointmentsPanel - fix round 1', () => {
  it('an empty "from" refuses client-side instead of freezing the panel busy', async () => {
    const load = vi.fn().mockResolvedValue(appointmentList())
    const user = userEvent.setup()
    render(<AppointmentsPanel audience="patient" load={load} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))

    expect(await screen.findByText('יש לבחור שני תאריכים תקינים')).toBeInTheDocument()
    expect(load).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'הצגה' })).not.toHaveAttribute('aria-busy')

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-10-01' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2026-10-05' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
  })

  it('an empty "to" refuses client-side instead of freezing the panel busy', async () => {
    const load = vi.fn().mockResolvedValue(appointmentList())
    const user = userEvent.setup()
    render(<AppointmentsPanel audience="patient" load={load} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))

    expect(await screen.findByText('יש לבחור שני תאריכים תקינים')).toBeInTheDocument()
    expect(load).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'הצגה' })).not.toHaveAttribute('aria-busy')

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-10-01' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2026-10-05' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
  })

  it('a year below 1000 refuses client-side instead of freezing the panel busy', async () => {
    const load = vi.fn().mockResolvedValue(appointmentList())
    const user = userEvent.setup()
    render(<AppointmentsPanel audience="patient" load={load} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '0050-01-01' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))

    expect(await screen.findByText('יש לבחור שני תאריכים תקינים')).toBeInTheDocument()
    expect(load).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'הצגה' })).not.toHaveAttribute('aria-busy')

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-10-01' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2026-10-05' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
  })

  it('shows the second answer, not a late first one (stale answers are ignored)', async () => {
    const first = deferred<AppointmentList>()
    const second = deferred<AppointmentList>()
    const load = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const { container } = render(<AppointmentsPanel audience="patient" load={load} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))

    // The mount's load is still pending, so the submit button's own busy-click guard
    // would swallow a second `user.click`. In the real app a second, overlapping load
    // comes from React StrictMode's double-invoked effect (both mount calls run before
    // either resolves) rather than from any click; dispatching the form's submit event
    // directly reproduces that same "two in-flight requests" shape without depending on
    // StrictMode being enabled in this test render.
    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-10-01' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2026-10-05' } })
    fireEvent.submit(container.querySelector('form')!)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))

    const firstAppt = appointment({ appointment_id: 'FIRST', department: 'Cardiology' })
    const secondAppt = appointment({ appointment_id: 'SECOND', department: 'Ophthalmology' })
    second.resolve(appointmentList({ appointments: [secondAppt] }))
    expect(await screen.findByText('עיניים')).toBeInTheDocument()

    first.resolve(appointmentList({ appointments: [firstAppt] }))
    // A real macrotask tick, not just a microtask: React's own state-update flush from a
    // `.then` outside `act` can land on the scheduler's macrotask queue, so a bare
    // `await Promise.resolve()` is not enough to observe whether the late answer
    // (wrongly) overwrote the screen.
    await new Promise((resolve) => setTimeout(resolve, 0))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByText('קרדיולוגיה')).not.toBeInTheDocument()
    expect(screen.getByText('עיניים')).toBeInTheDocument()
  })

  it('retries with the last shown range, not with unsubmitted edits to the inputs', async () => {
    const load = vi
      .fn()
      .mockResolvedValueOnce(appointmentList())
      .mockRejectedValueOnce(new ApiError(503, 'appointments_unavailable'))
      .mockResolvedValueOnce(appointmentList())
    const user = userEvent.setup()
    render(<AppointmentsPanel audience="patient" load={load} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-10-01' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2026-10-05' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('לא הצלחנו לטעון את התורים')).toBeInTheDocument()

    // Edit the inputs again, but do not submit - only the retry button is pressed.
    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-11-01' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2026-11-05' } })
    await user.click(screen.getByRole('button', { name: 'נסה שוב' }))

    await waitFor(() => expect(load).toHaveBeenCalledTimes(3))
    const [retryFrom, retryTo] = load.mock.calls[2] as [Date, Date]
    expect(retryFrom).toEqual(new Date(2026, 9, 1, 0, 0))
    expect(retryTo).toEqual(new Date(2026, 9, 6, 0, 0))
  })

  it('never shows a status or document code to the patient; staff sees both in .mono', async () => {
    const scheduled = appointment({ required_documents: ['CBC'] })
    const cancelled = appointment({
      appointment_id: 'APT-CANCELLED',
      appointment_at: '2026-10-20T08:00:00Z',
      status: 'Cancelled',
      required_documents: [],
    })
    const list = appointmentList({ appointments: [scheduled, cancelled] })

    const patientLoad = vi.fn().mockResolvedValue(list)
    const { unmount } = render(<AppointmentsPanel audience="patient" load={patientLoad} />)
    await screen.findByText('ספירת דם מלאה')
    expect(document.body).not.toHaveTextContent('Scheduled')
    expect(document.body).not.toHaveTextContent('Cancelled')
    expect(document.body).not.toHaveTextContent('CBC')
    unmount()

    const staffLoad = vi.fn().mockResolvedValue(list)
    render(<AppointmentsPanel audience="staff" load={staffLoad} />)
    await screen.findByText('ספירת דם מלאה')
    expect(screen.getByText('Scheduled')).toHaveClass('mono')
    expect(screen.getByText('Cancelled')).toHaveClass('mono')
    expect(screen.getByText('CBC')).toHaveClass('mono')
  })

  it('accepts a 366-day span and refuses a 367-day one, at the boundary', async () => {
    const load = vi.fn().mockResolvedValue(appointmentList())
    const user = userEvent.setup()
    render(<AppointmentsPanel audience="patient" load={load} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-01-01' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2027-01-01' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
    expect(screen.queryByText('אפשר להציג עד שנה אחת')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2027-01-02' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))
    expect(await screen.findByText('אפשר להציג עד שנה אחת')).toBeInTheDocument()
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('accepts a same-day range', async () => {
    const load = vi.fn().mockResolvedValue(appointmentList())
    const user = userEvent.setup()
    render(<AppointmentsPanel audience="patient" load={load} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-10-10' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2026-10-10' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))

    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
    expect(screen.queryByText('תאריך הסיום חייב להיות אחרי תאריך ההתחלה או באותו יום')).not.toBeInTheDocument()
    const [from, to] = load.mock.calls[1] as [Date, Date]
    expect(from).toEqual(new Date(2026, 9, 10, 0, 0))
    expect(to).toEqual(new Date(2026, 9, 11, 0, 0))
  })

  it('sets aria-busy on the submit button while a load is pending', async () => {
    const first = deferred<AppointmentList>()
    const load = vi.fn().mockReturnValue(first.promise)
    render(<AppointmentsPanel audience="patient" load={load} />)

    const button = screen.getByRole('button', { name: 'הצגה' })
    await waitFor(() => expect(button).toHaveAttribute('aria-busy', 'true'))

    first.resolve(appointmentList())
    await waitFor(() => expect(button).not.toHaveAttribute('aria-busy'))
  })

  it('gives a cancelled row the is-cancelled class and keeps the raw timestamp on <time>', async () => {
    const cancelled = appointment({ status: 'Cancelled' })
    const load = vi.fn().mockResolvedValue(appointmentList({ appointments: [cancelled] }))
    const { container } = render(<AppointmentsPanel audience="patient" load={load} />)

    const item = await waitFor(() => {
      const found = container.querySelector('.appointment-item')
      expect(found).not.toBeNull()
      return found as HTMLElement
    })
    expect(item).toHaveClass('is-cancelled')
    const time = item.querySelector('time')
    expect(time).toHaveAttribute('datetime', cancelled.appointment_at)
  })

  it('clears a range error once a later submit is valid', async () => {
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

    fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-10-01' } })
    fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2026-10-05' } })
    await user.click(screen.getByRole('button', { name: 'הצגה' }))

    await waitFor(() =>
      expect(
        screen.queryByText('תאריך הסיום חייב להיות אחרי תאריך ההתחלה או באותו יום'),
      ).not.toBeInTheDocument(),
    )
  })

  it("gives the staff a Hebrew label for network_error, via detailOf's own fallback", async () => {
    const load = vi.fn().mockRejectedValue(new TypeError('offline'))
    render(<AppointmentsPanel audience="staff" load={load} />)

    expect(await screen.findByText('אין חיבור לשרת')).toBeInTheDocument()
    expect(screen.getByText('network_error')).toHaveClass('mono')
  })
})
