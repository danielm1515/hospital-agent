/**
 * Small helpers shared by the patient screens (design §4).
 *
 * Nothing here decides anything: the status always comes from the API response
 * (`docs/api.md` §4), and these functions only shape it for the screen.
 */
import { useEffect, useRef } from 'react'
import { ApiError } from '../../api/client'
import type { PatientStatus } from '../../api/types'

/** The patient screens refresh every 3 s while a case is still moving (design §4). */
export const POLL_MS = 3000

/** The card text is cut at 120 characters on the list screen. */
export const SUMMARY_LENGTH = 120

/** The two statuses the agent advances on its own; the rest wait for a person. */
export function isMoving(status: PatientStatus): boolean {
  return status === 'received' || status === 'in_progress'
}

export function truncate(text: string, max = SUMMARY_LENGTH): string {
  return text.length <= max ? text : `${text.slice(0, max)}…`
}

const DATE_TIME = new Intl.DateTimeFormat('he-IL', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

/** An ISO timestamp as Hebrew date and time; an unparsable one is shown as it came. */
export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : DATE_TIME.format(date)
}

const DATE = new Intl.DateTimeFormat('he-IL', { day: '2-digit', month: '2-digit', year: 'numeric' })

/** The calendar date alone, for a timeline that prints it only when the day changes. */
export function formatDate(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : DATE.format(date)
}

const CLOCK = new Intl.DateTimeFormat('he-IL', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

/**
 * The time of day, to the second. The agent moves a case in seconds, so a timeline
 * without seconds would stamp every step with the same minute.
 */
export function formatClock(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : CLOCK.format(date)
}

/** The same calendar day, so a timeline can print the date only when it changes. */
export function sameDay(iso: string, other: string): boolean {
  const [a, b] = [new Date(iso), new Date(other)]
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return false
  return a.toDateString() === b.toDateString()
}

const UNITS: Array<{ ms: number; one: string; two: string; many: string }> = [
  { ms: 86_400_000, one: 'יום', two: 'יומיים', many: 'ימים' },
  { ms: 3_600_000, one: 'שעה', two: 'שעתיים', many: 'שעות' },
  { ms: 60_000, one: 'דקה', two: 'שתי דקות', many: 'דקות' },
  { ms: 1000, one: 'שנייה', two: 'שתי שניות', many: 'שניות' },
]

/**
 * How long passed between two steps, in Hebrew ("כעבור 4 שניות"). `null` when the two
 * are in the same second, or either timestamp is unusable - there is nothing to say then.
 */
export function elapsedBetween(from: string, to: string): string | null {
  // Both ends are floored to the second first, so the gap always agrees with the two
  // times printed beside it: 01:58:12.9 to 01:58:47.4 reads as 35 seconds, not 34.
  const second = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) * 1000
  const gap = second(to) - second(from)
  if (!Number.isFinite(gap) || gap < 1000) return null
  const unit = UNITS.find((candidate) => gap >= candidate.ms) ?? UNITS[UNITS.length - 1]
  const count = Math.floor(gap / unit.ms)
  if (count === 1) return `כעבור ${unit.one}`
  if (count === 2) return `כעבור ${unit.two}`
  return `כעבור ${count} ${unit.many}`
}

/**
 * What each status means, in the patient's own words: a title for the timeline step,
 * and one line saying what it means for them. The patient sees only these seven abstract
 * statuses, so nothing here names a State, an event or a reason (§12.3).
 */
const STATUS_TEXT: Record<PatientStatus, { title: string; note: string }> = {
  received: { title: 'הפנייה נקלטה', note: 'הפנייה התקבלה במערכת וממתינה לטיפול.' },
  in_progress: { title: 'הפנייה בטיפול', note: 'בדיקת התור, המסמכים הנדרשים והוראות ההכנה.' },
  needs_document: { title: 'ממתינה למסמך', note: 'כדי להמשיך נדרש מסמך שעדיין לא הועלה.' },
  needs_reply: { title: 'ממתינה לתשובתך', note: 'איש צוות ביקש ממך פרט נוסף או מסמך.' },
  in_review: { title: 'הועברה לצוות', note: 'איש צוות בודק את הפנייה. מידע רפואי אינו נמסר אוטומטית.' },
  completed: { title: 'הפנייה הושלמה', note: 'נשלחה אליכם הודעת סטטוס.' },
  closed: { title: 'הפנייה נסגרה', note: 'הטיפול הסתיים בלי הודעה אוטומטית.' },
}

/** A status this version does not know is still shown, without inventing a meaning for it. */
const UNKNOWN_STATUS = { title: 'עדכון בפנייה', note: 'מצב הפנייה השתנה.' }

export function statusText(status: PatientStatus): { title: string; note: string } {
  return STATUS_TEXT[status] ?? UNKNOWN_STATUS
}

/**
 * The demo's document ids and the sub-project 11 catalog types, for a readable line
 * next to the id itself. An id the demo does not know is shown as the id alone - the
 * UI never invents a name.
 */
const DOCUMENT_LABELS: Record<string, string> = {
  referral: 'הפניה',
  blood_test: 'בדיקת דם',
  imaging: 'הדמיה',
  consent_form: 'טופס הסכמה',
  CBC: 'ספירת דם מלאה',
  COAGULATION_TESTS: 'בדיקות קרישה',
  ECG: 'תרשים פעילות חשמלית של הלב',
  URINALYSIS: 'בדיקת שתן',
  PREOP_SUMMARY: 'סיכום טרום ניתוח',
}

export function documentLabel(documentId: string): string | null {
  return DOCUMENT_LABELS[documentId] ?? null
}

/** The client-side refusal of a non-PDF file, and of a PDF over the 10 MB limit. */
export const NOT_PDF_MESSAGE = 'יש לבחור קובץ PDF.'
export const FILE_TOO_LARGE_MESSAGE = 'הקובץ גדול מדי. אפשר להעלות קובץ עד 10MB.'

/** `POST .../documents/file` accepts a PDF of at most 10 MB (`docs/api.md` §4). */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024

/**
 * Hebrew for the sub-project 13 upload errors and the sub-project 15 reply errors
 * (`docs/api.md` §4, §8), keyed by `detail`. Checked before the generic status-based
 * fallbacks below, since a `404` here (`file_upload_not_enabled`) is not the same as
 * the generic "case not found" `404`. A code not listed here still falls back to the
 * generic sentence below, never to the raw code (§12.3).
 */
const ERROR_DETAILS: Record<string, string> = {
  not_waiting_for_document: 'הפנייה כבר אינה ממתינה למסמך. רעננו את המסך ונסו שוב.',
  too_large: FILE_TOO_LARGE_MESSAGE,
  document_service_unavailable: 'שירות המסמכים אינו זמין כרגע. נסו שוב מאוחר יותר או פנו למוקד המטופלים.',
  file_upload_not_enabled: 'העלאת קובץ אינה זמינה כרגע. נסו שוב מאוחר יותר או פנו למוקד המטופלים.',
  // Sub-project 15 (`docs/api.md` §8): the patient's reply to a staff request.
  not_waiting_for_reply: 'הפנייה כבר אינה ממתינה לתשובה. רעננו את המסך כדי לראות את מצב הפנייה.',
  reply_kind_mismatch: 'לא ניתן להשיב בדרך זו לבקשה שנשלחה. רעננו את המסך ונסו שוב.',
  reply_not_accepted: 'לא הצלחנו לקלוט את התשובה. נסו שוב בעוד רגע או פנו למוקד המטופלים.',
  reply_too_long: 'התשובה ארוכה מדי. יש לקצר אותה ל־2000 תווים לכל היותר.',
}

/**
 * A failed call, as one Hebrew sentence. The patient never sees an internal
 * code, an escalation kind or a policy reason (§12.3, `docs/api.md` §4).
 */
export function errorMessage(caught: unknown): string {
  if (!(caught instanceof ApiError)) return 'אירעה תקלה. נסו שוב בעוד רגע.'
  if (caught.status === 0) return 'אין חיבור לשרת. בדקו את החיבור ונסו שוב.'
  const specific = ERROR_DETAILS[caught.detail]
  if (specific) return specific
  if (caught.status === 404) return 'הפנייה לא נמצאה.'
  if (caught.status === 422 || caught.detail === 'validation_error') return 'הפרטים שהוזנו אינם תקינים.'
  return 'אירעה תקלה. נסו שוב בעוד רגע.'
}

/** Runs `tick` every `POLL_MS` while `active`, always with the latest closure. */
export function usePolling(active: boolean, tick: () => void): void {
  const latest = useRef(tick)
  useEffect(() => {
    latest.current = tick
  })
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => latest.current(), POLL_MS)
    return () => clearInterval(timer)
  }, [active])
}
