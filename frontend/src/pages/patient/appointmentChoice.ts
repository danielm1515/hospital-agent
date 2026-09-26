/**
 * "לאיזה תור הפנייה?" (sub-project 18, design D5): the patient's upcoming appointments as
 * choices, and the pre-send check that the text does not name another appointment than the
 * one chosen.
 *
 * The check is deterministic string matching only - no LLM, no server block. It looks for
 * three signals: a date written as d/m or d.m, a department (the Hebrew label or the English
 * catalog code) and an exam type's Hebrew label. It never decides anything: it only offers
 * the patient a switch, and the patient may send as is.
 */
import type { Appointment } from '../../api/types'
import { DEPARTMENT_LABELS } from '../../components/AppointmentsPanel'

/** Appointments are compared and shown in Israel time, like the agent's own message. */
const ISRAEL = 'Asia/Jerusalem'

const DAY_MONTH = new Intl.DateTimeFormat('en-GB', { timeZone: ISRAEL, day: 'numeric', month: 'numeric' })
const OPTION_DATE = new Intl.DateTimeFormat('he-IL', {
  timeZone: ISRAEL,
  day: 'numeric',
  month: 'numeric',
  year: 'numeric',
})
const OPTION_TIME = new Intl.DateTimeFormat('he-IL', {
  timeZone: ISRAEL,
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

export interface DayMonth {
  day: number
  month: number
}

/** The calendar day and month of an instant in Israel; `null` for an unparsable one. */
export function israelDayMonth(iso: string): DayMonth | null {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  const parts = DAY_MONTH.formatToParts(date)
  const day = Number(parts.find((part) => part.type === 'day')?.value)
  const month = Number(parts.find((part) => part.type === 'month')?.value)
  return { day, month }
}

/** A d/m or d.m date, optionally followed by a year; never digits inside a longer number. */
const DATE_RE = /(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](?:\d{4}|\d{2}))?(?!\d)/g

/** Every plausible d/m (or d.m) date in `text`, in order; an impossible day or month is dropped. */
export function datesInText(text: string): DayMonth[] {
  const found: DayMonth[] = []
  for (const match of text.matchAll(DATE_RE)) {
    const day = Number(match[1])
    const month = Number(match[2])
    if (day >= 1 && day <= 31 && month >= 1 && month <= 12) found.push({ day, month })
  }
  return found
}

/** Scheduled appointments only (a cancelled one is not something to ask about), earliest first. */
export function upcomingScheduled(appointments: Appointment[]): Appointment[] {
  return appointments
    .filter((appointment) => appointment.status === 'Scheduled')
    .sort((a, b) => new Date(a.appointment_at).getTime() - new Date(b.appointment_at).getTime())
}

function departmentLabel(appointment: Appointment): string {
  return DEPARTMENT_LABELS[appointment.department] ?? appointment.department
}

/**
 * One appointment as the patient reads it: exam, department and the Israel date and time.
 * No code - not the appointment id, the exam code or the instruction source.
 */
export function appointmentOptionLabel(appointment: Appointment): string {
  const date = new Date(appointment.appointment_at)
  const when = Number.isNaN(date.getTime())
    ? appointment.appointment_at
    : `${OPTION_DATE.format(date)} בשעה ${OPTION_TIME.format(date)}`
  const parts = [appointment.exam_type?.label, departmentLabel(appointment), when].filter(Boolean)
  return parts.join(' · ')
}

// ---- The pre-send check ------------------------------------------------------

/** Letters and digits only, lower-cased, single-spaced and padded, so a phrase test is a word test. */
function normalise(text: string): string {
  return ` ${text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()} `
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * `phrase` as whole words in the normalised text, allowing up to two Hebrew prefix letters
 * (ו, ה, ב, ל, מ, ש, כ) glued to its start - "בקרדיולוגיה", "והמבחן מאמץ" - but never a
 * phrase inside a longer word ("עורך" is not "עור").
 */
function containsPhrase(normalisedText: string, phrase: string): boolean {
  const target = normalise(phrase).trim()
  if (!target) return false
  return new RegExp(` [והבלמשכ]{0,2}${escapeRegExp(target)} `, 'u').test(normalisedText)
}

/** Every signal that names `appointment`, as comparable keys. */
function signalsOf(appointment: Appointment): string[] {
  const keys: string[] = []
  const dayMonth = israelDayMonth(appointment.appointment_at)
  if (dayMonth) keys.push(`date:${dayMonth.day}/${dayMonth.month}`)
  keys.push(`phrase:${normalise(departmentLabel(appointment)).trim()}`)
  keys.push(`phrase:${normalise(appointment.department).trim()}`)
  if (appointment.exam_type?.label) keys.push(`phrase:${normalise(appointment.exam_type.label).trim()}`)
  return [...new Set(keys)]
}

function textHas(key: string, normalisedText: string, dates: Set<string>): boolean {
  if (key.startsWith('date:')) return dates.has(key)
  return containsPhrase(normalisedText, key.slice('phrase:'.length))
}

/**
 * The other appointment the text seems to be about, or `null`.
 *
 * Only a signal that tells two appointments apart counts: a department both share, or a
 * date both fall on, names neither. An other appointment is offered when the text carries at
 * least one of its own distinguishing signals and none of the chosen appointment's - a text
 * that names both is left alone. With several candidates, the one the text names most wins;
 * a tie goes to the earlier in `others`.
 */
export function suggestOtherAppointment(
  text: string,
  chosen: Appointment,
  others: Appointment[],
): Appointment | null {
  const normalisedText = normalise(text)
  const dates = new Set(datesInText(text).map(({ day, month }) => `date:${day}/${month}`))
  const chosenKeys = signalsOf(chosen)

  let best: Appointment | null = null
  let bestScore = 0
  for (const other of others) {
    if (other.appointment_id === chosen.appointment_id) continue
    const otherKeys = signalsOf(other)
    const namesChosen = chosenKeys.some(
      (key) => !otherKeys.includes(key) && textHas(key, normalisedText, dates),
    )
    if (namesChosen) continue
    const score = otherKeys.filter(
      (key) => !chosenKeys.includes(key) && textHas(key, normalisedText, dates),
    ).length
    if (score > bestScore) {
      best = other
      bestScore = score
    }
  }
  return best
}
