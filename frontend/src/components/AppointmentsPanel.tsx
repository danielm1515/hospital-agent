/**
 * The patient's appointment list (sub-project 16, `docs/api.md` §9, design D10/D11).
 * One shared component: the patient's own screen passes `listMyAppointments`, the
 * staff review screen passes `listCaseAppointments` bound to the case id - the panel
 * itself never calls the API client directly, so it never chooses which patient it is.
 */
import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import type { Appointment, AppointmentList, AppointmentStatus } from '../api/types'
import { documentLabel } from '../pages/patient/helpers'
import { detailOf } from '../pages/staff/labels'
import { Alert } from './Alert'
import { Button } from './Button'
import { TextField } from './TextField'

/** The appointment-service catalog (`app/catalog.py`), presentation only (design D11). */
export const DEPARTMENT_LABELS: Record<string, string> = {
  Cardiology: 'קרדיולוגיה',
  Dermatology: 'עור',
  Neurology: 'נוירולוגיה',
  Ophthalmology: 'עיניים',
  Orthopedics: 'אורתופדיה',
}

const STATUS_LABELS: Record<AppointmentStatus, string> = {
  Scheduled: 'מתוכנן',
  Cancelled: 'בוטל',
}

/** Staff-only: a Hebrew label beside the code, never instead of it (CLAUDE.md). */
const ERROR_LABELS: Record<string, string> = {
  appointments_unavailable: 'מערכת התורים אינה זמינה',
  patient_not_found: 'המטופל אינו מוכר במערכת התורים',
  case_not_found: 'הפנייה לא נמצאה',
  invalid_range: 'טווח תאריכים לא תקין',
  network_error: 'אין חיבור לשרת',
}

const INVALID_DATES_ERROR = 'יש לבחור שני תאריכים תקינים'
const RANGE_ORDER_ERROR = 'תאריך הסיום חייב להיות אחרי תאריך ההתחלה או באותו יום'
const RANGE_SPAN_ERROR = 'אפשר להציג עד שנה אחת'
const MAX_SPAN_DAYS = 366
const ONE_DAY_MS = 24 * 60 * 60 * 1000
/** `<input type="date">` value shape, a four-digit year required (never a two-digit
 * year: the multi-arg `Date` constructor maps 0-99 to 1900-1999, silently). */
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

const DATE_TIME = new Intl.DateTimeFormat('he-IL', {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** A local `YYYY-MM-DD`, never `toISOString()`, which would shift the day. */
function toDateString(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** Today and today+30 days, as local `YYYY-MM-DD` day strings. */
export function defaultRange(today: Date): { from: string; to: string } {
  const from = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  const to = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 30)
  return { from: toDateString(from), to: toDateString(to) }
}

function dateParts(value: string): [number, number, number] {
  const [y, m, d] = value.split('-').map(Number)
  return [y, m, d]
}

/**
 * `value` is a well-formed `YYYY-MM-DD` day with a four-digit year (>= 1000). An empty,
 * partial or two/three-digit-year string (from a cleared or half-typed date input) fails
 * this, so it is refused before it ever reaches `new Date(...)` or `toISOString()`.
 */
function isValidDay(value: string): boolean {
  if (!DATE_RE.test(value)) return false
  const [y] = dateParts(value)
  return y >= 1000
}

/** Local midnight of `from`, local midnight of the day *after* `to` - `to` is included. */
export function rangeToInstants(from: string, to: string): { from: Date; to: Date } {
  const [fy, fm, fd] = dateParts(from)
  const [ty, tm, td] = dateParts(to)
  return { from: new Date(fy, fm - 1, fd), to: new Date(ty, tm - 1, td + 1) }
}

export interface AppointmentsPanelProps {
  audience: 'patient' | 'staff'
  load: (from: Date, to: Date) => Promise<AppointmentList>
}

export function AppointmentsPanel({ audience, load }: AppointmentsPanelProps) {
  const [range, setRange] = useState(() => defaultRange(new Date()))
  const [shown, setShown] = useState(range)
  const [result, setResult] = useState<AppointmentList | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [rangeError, setRangeError] = useState<string | null>(null)
  const requestId = useRef(0)

  function runLoad(from: string, to: string) {
    // Validated first, and entirely before any state that starts a request (`shown`,
    // `busy`, `error`) - a cleared or half-typed date input must never leave the panel
    // permanently busy (it would, if `toISOString()` on an Invalid Date threw before
    // `busy` was ever set back to `false`).
    if (!isValidDay(from) || !isValidDay(to)) {
      setRangeError(INVALID_DATES_ERROR)
      return
    }
    const instants = rangeToInstants(from, to)
    if (Number.isNaN(instants.from.getTime()) || Number.isNaN(instants.to.getTime())) {
      setRangeError(INVALID_DATES_ERROR)
      return
    }
    if (instants.to.getTime() <= instants.from.getTime()) {
      setRangeError(RANGE_ORDER_ERROR)
      return
    }
    const spanDays = (instants.to.getTime() - instants.from.getTime()) / ONE_DAY_MS
    if (spanDays > MAX_SPAN_DAYS) {
      setRangeError(RANGE_SPAN_ERROR)
      return
    }
    setRangeError(null)
    setShown({ from, to })
    setBusy(true)
    setError(null)
    const id = ++requestId.current
    // `Promise.resolve().then(...)` so a `load` that throws synchronously (rather than
    // returning a rejected promise) still lands in `.catch` instead of escaping `runLoad`
    // with `busy` stuck at `true`.
    Promise.resolve()
      .then(() => load(instants.from, instants.to))
      .then((answer) => {
        if (id !== requestId.current) return
        setResult(answer)
        setError(null)
        setBusy(false)
      })
      .catch((caught: unknown) => {
        if (id !== requestId.current) return
        setError(caught)
        setResult(null)
        setBusy(false)
      })
  }

  useEffect(() => {
    // Loads the default range once on mount; every later load goes through `runLoad`
    // from the form submit or the retry button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    runLoad(range.from, range.to)
  }, [])

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    runLoad(range.from, range.to)
  }

  function retry() {
    runLoad(shown.from, shown.to)
  }

  const heading = audience === 'patient' ? 'התורים שלי' : 'התורים של המטופל'
  const detail = error ? detailOf(error) : null

  return (
    <section className="appointments card">
      <h2 className="section-h">{heading}</h2>

      <form className="appointments-filter" onSubmit={handleSubmit}>
        <TextField
          label="מתאריך"
          type="date"
          dir="ltr"
          value={range.from}
          onChange={(event) => setRange((prev) => ({ ...prev, from: event.target.value }))}
        />
        <TextField
          label="עד תאריך"
          type="date"
          dir="ltr"
          value={range.to}
          onChange={(event) => setRange((prev) => ({ ...prev, to: event.target.value }))}
        />
        <Button type="submit" busy={busy}>
          הצגה
        </Button>
      </form>

      {rangeError && <Alert variant="error">{rangeError}</Alert>}

      {detail === 'appointments_not_enabled' ? (
        <Alert variant="info">רשימת התורים אינה זמינה כרגע.</Alert>
      ) : error ? (
        <Alert variant="error" title="לא הצלחנו לטעון את התורים">
          <p>{errorBody(audience, detail ?? 'network_error')}</p>
          <Button variant="secondary" busy={busy} onClick={retry}>
            נסה שוב
          </Button>
        </Alert>
      ) : result ? (
        <>
          {result.truncated && (
            <Alert variant="info">מוצגים 100 התורים הראשונים בטווח. אפשר לצמצם את הטווח.</Alert>
          )}
          {result.appointments.length === 0 ? (
            <p className="empty">אין תורים בטווח שנבחר.</p>
          ) : (
            <ul className="appointment-list">
              {result.appointments.map((appointment) => (
                <AppointmentRow key={appointment.appointment_id} appointment={appointment} audience={audience} />
              ))}
            </ul>
          )}
        </>
      ) : null}
    </section>
  )
}

function errorBody(audience: 'patient' | 'staff', detail: string): ReactNode {
  if (audience === 'patient') {
    if (detail === 'patient_not_found') {
      return 'לא נמצאו פרטי המטופל במערכת התורים. אפשר לפנות למוקד המטופלים.'
    }
    return 'מערכת התורים אינה זמינה כרגע. נסו שוב בעוד רגע.'
  }
  const label = ERROR_LABELS[detail] ?? 'שגיאה לא צפויה'
  return (
    <>
      {label} <span className="mono">{detail}</span>
    </>
  )
}

function AppointmentRow({ appointment, audience }: { appointment: Appointment; audience: 'patient' | 'staff' }) {
  const departmentLabel = DEPARTMENT_LABELS[appointment.department] ?? appointment.department
  const statusLabel = STATUS_LABELS[appointment.status]
  const cancelled = appointment.status === 'Cancelled'

  return (
    <li className={cancelled ? 'appointment-item is-cancelled' : 'appointment-item'}>
      <time dateTime={appointment.appointment_at}>{DATE_TIME.format(new Date(appointment.appointment_at))}</time>
      <p className="appointment-meta">
        <span>{departmentLabel}</span>
        {appointment.doctor_name && <span>{appointment.doctor_name}</span>}
        {appointment.location && <span>{appointment.location}</span>}
      </p>
      <p className="appointment-meta">
        <span>{statusLabel}</span>
        {audience === 'staff' && <span className="mono">{appointment.status}</span>}
      </p>
      {appointment.required_documents.length > 0 && (
        <ul className="appointment-meta">
          {appointment.required_documents.map((documentId) => (
            <li key={documentId}>
              <span>{documentLabel(documentId) ?? documentId}</span>
              {audience === 'staff' && <span className="mono">{documentId}</span>}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}
