/**
 * The patient's appointment list (sub-project 16, `docs/api.md` §9, design D10/D11), with each
 * appointment's exam type and preparation instruction since sub-project 18 (design D12).
 * One shared component: the patient's own screen passes `listMyAppointments`, the
 * staff review screen passes `listCaseAppointments` bound to the case id - the panel
 * itself never calls the API client directly, so it never chooses which patient it is.
 */
import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import type { Appointment, AppointmentInstruction, AppointmentList, AppointmentStatus, InstructionText } from '../api/types'
import { documentLabel } from '../pages/patient/helpers'
import { detailOf } from '../pages/staff/labels'
import { Alert } from './Alert'
import { Button } from './Button'
import { Loading } from './Loading'
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
  // Sub-project 18 (`docs/api.md` §9): the instruction-text read.
  instructions_unavailable: 'מערכת הוראות ההכנה אינה זמינה',
  invalid_instruction: 'מזהה הוראות ההכנה אינו תקין',
}

/**
 * The panel's own refusal of an answer for another source or version than it asked for. Not
 * an API code, so it is never printed as one - staff see only its Hebrew line (fix round 1).
 */
const MISMATCH = 'instruction_mismatch'
const MISMATCH_TEXT = 'התקבלה גרסה אחרת של הוראות ההכנה.'

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

/** Reads one approved instruction text: the patient's route or the staff's (sub-project 18). */
export type InstructionLoader = (sourceId: string, version: string) => Promise<InstructionText>

export interface AppointmentsPanelProps {
  audience: 'patient' | 'staff'
  load: (from: Date, to: Date) => Promise<AppointmentList>
  /**
   * Sub-project 18 (design D12): `getPatientInstruction` or `getStaffInstruction`, chosen by
   * the screen like `load`. Without it a row shows the instruction's title and no toggle.
   */
  loadInstruction?: InstructionLoader
}

export function AppointmentsPanel({ audience, load, loadInstruction }: AppointmentsPanelProps) {
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
      // A request superseded - or a panel unmounted - before its microtask ran never calls load.
      .then(() => (id === requestId.current ? load(instants.from, instants.to) : null))
      .then((answer) => {
        if (id !== requestId.current || answer === null) return
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
    runLoad(range.from, range.to)
    return () => {
      // Unmounted: drop any answer still in flight and skip a load not yet started.
      requestId.current += 1
    }
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
                <AppointmentRow
                  key={appointment.appointment_id}
                  appointment={appointment}
                  audience={audience}
                  loadInstruction={loadInstruction}
                />
              ))}
            </ul>
          )}
        </>
      ) : busy ? (
        // Fix round 1 (I2): the panel's first load has no previous result or error to show,
        // so without this the whole panel rendered nothing at all while it was in flight.
        <Loading size="inline" label="טוען תורים" />
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

function AppointmentRow({
  appointment,
  audience,
  loadInstruction,
}: {
  appointment: Appointment
  audience: 'patient' | 'staff'
  loadInstruction?: InstructionLoader
}) {
  const departmentLabel = DEPARTMENT_LABELS[appointment.department] ?? appointment.department
  const statusLabel = STATUS_LABELS[appointment.status]
  const cancelled = appointment.status === 'Cancelled'
  const examType = appointment.exam_type ?? null

  return (
    <li className={cancelled ? 'appointment-item is-cancelled' : 'appointment-item'}>
      <time dateTime={appointment.appointment_at}>{DATE_TIME.format(new Date(appointment.appointment_at))}</time>
      <p className="appointment-meta">
        <span>{departmentLabel}</span>
        {examType && <span>{examType.label}</span>}
        {examType && audience === 'staff' && <span className="mono">{examType.code}</span>}
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
      {appointment.instruction && (
        <AppointmentInstructions
          // A new source or version is a new component: no cached text, and no answer still
          // in flight for the old one, can ever show under it (fix round 1, item 2).
          key={`${appointment.instruction.source_id}@${appointment.instruction.version}`}
          instruction={appointment.instruction}
          audience={audience}
          loadInstruction={loadInstruction}
          textId={`instr-${appointment.appointment_id}`}
        />
      )}
    </li>
  )
}

/**
 * Sub-project 18 (design D12): the instruction's title, and "הצגת הוראות ההכנה" loading its
 * text on demand through the screen's own route - which answers only a source the registry
 * approves now, so the panel can never show unapproved text. The patient sees no code; staff
 * see the source id and version beside the title. A failed read is a quiet line, never an
 * error box: `instruction_not_approved` says so, `instructions_not_enabled` removes the
 * toggle, anything else offers a retry.
 */
function AppointmentInstructions({
  instruction,
  audience,
  loadInstruction,
  textId,
}: {
  instruction: AppointmentInstruction
  audience: 'patient' | 'staff'
  loadInstruction?: InstructionLoader
  textId: string
}) {
  const [text, setText] = useState<InstructionText | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const requestId = useRef(0)

  useEffect(
    () => () => {
      // Unmounted (or StrictMode's throw-away mount): drop any answer still in flight.
      requestId.current += 1
    },
    [],
  )

  function fetchText(read: InstructionLoader) {
    const id = ++requestId.current
    setBusy(true)
    setProblem(null)
    Promise.resolve()
      // A read superseded - or a row unmounted - before its microtask ran never starts.
      .then(() => (id === requestId.current ? read(instruction.source_id, instruction.version) : null))
      .then((answer) => {
        if (id !== requestId.current || answer === null) return
        // Only the exact source and version that was asked for is ever shown.
        if (answer.source_id !== instruction.source_id || answer.version !== instruction.version) {
          setProblem(MISMATCH)
        } else {
          setText(answer)
          setOpen(true)
        }
        setBusy(false)
      })
      .catch((caught: unknown) => {
        if (id !== requestId.current) return
        setProblem(detailOf(caught))
        setBusy(false)
      })
  }

  function toggle() {
    if (open) setOpen(false)
    else if (text) setOpen(true)
    else if (loadInstruction) fetchText(loadInstruction)
  }

  let control: ReactNode = null
  if (problem === 'instruction_not_approved') {
    control = <p className="hint">הוראות ההכנה טרם אושרו.</p>
  } else if (problem === 'instructions_not_enabled') {
    control = <p className="hint">הוראות ההכנה אינן זמינות כרגע.</p>
  } else if (problem && loadInstruction) {
    control = (
      <div className="appointment-instructions-row">
        <p className="hint">
          לא הצלחנו לטעון את הוראות ההכנה כרגע.
          {audience === 'staff' &&
            (problem === MISMATCH ? (
              <> {MISMATCH_TEXT}</>
            ) : (
              <>
                {' '}
                {ERROR_LABELS[problem] ?? 'שגיאה לא צפויה'} <span className="mono">{problem}</span>
              </>
            ))}
        </p>
        <Button variant="quiet" busy={busy} onClick={() => fetchText(loadInstruction)}>
          נסו שוב
        </Button>
      </div>
    )
  } else if (loadInstruction) {
    control = (
      <Button
        variant="quiet"
        busy={busy}
        aria-expanded={open}
        // Only while the text is actually there to point at (fix round 1, item 7).
        aria-controls={open && text ? textId : undefined}
        onClick={toggle}
      >
        {open ? 'הסתרת הוראות ההכנה' : 'הצגת הוראות ההכנה'}
      </Button>
    )
  }

  return (
    <div className="appointment-instructions">
      <p className="appointment-meta">
        <span>{`הוראות הכנה: ${instruction.title}`}</span>
        {audience === 'staff' && (
          <>
            <span className="mono">{instruction.source_id}</span>
            <span>{`גרסה ${instruction.version}`}</span>
          </>
        )}
      </p>
      {control}
      {busy && <Loading size="inline" label="טוען את הוראות ההכנה" />}
      {open && text && (
        <div className="appointment-instructions-text" id={textId}>
          <p className="req-instructions-title">{text.title}</p>
          <p className="message-text">{text.text}</p>
        </div>
      )}
    </div>
  )
}
