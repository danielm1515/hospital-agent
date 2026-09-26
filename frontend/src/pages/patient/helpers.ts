/**
 * Small helpers shared by the patient screens (design §4).
 *
 * Nothing here decides anything: the status always comes from the API response
 * (`docs/api.md` §4), and these functions only shape it for the screen.
 */
import { useEffect, useRef } from 'react'
import { ApiError } from '../../api/client'
import type { DocumentType, PatientStatus, UploadResult } from '../../api/types'
import type { AlertVariant } from '../../components/Alert'

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

// Unicode "First Strong Isolate" / "Pop Directional Isolate": wraps a Latin run inside
// Hebrew text so it keeps its own direction without disturbing the RTL sentence around it
// (the `.mono` convention elsewhere in this app does the same with CSS `unicode-bidi:
// isolate` for a run that lives in its own element; a raw code interpolated into a plain
// string has no element to isolate, so the string itself carries the isolation).
const FSI = '\u2068'
const PDI = '\u2069'

/**
 * The type's Hebrew label, or - fail closed (§14) - the raw catalog code, isolated so it
 * reads left-to-right instead of jumping to the wrong edge of the Hebrew sentence it lands
 * in (a code this version does not know should still never happen in practice, since
 * `DocumentType` is a closed union, but the value arrives over the wire, not from the type
 * checker).
 */
export function describeDocumentType(type: DocumentType | null): string {
  if (!type) return ''
  const label = documentLabel(type)
  return label ?? `${FSI}${type}${PDI}`
}

/**
 * The client-side refusal of a file that is not PDF, JPEG or PNG, and of one over the 10 MB
 * limit (sub-project 17 task 2: the picker now also takes a photo of a document, not only a
 * PDF).
 */
export const UNSUPPORTED_FILE_MESSAGE = 'אפשר להעלות רק PDF או תמונה (JPG/PNG).'
export const FILE_TOO_LARGE_MESSAGE = 'הקובץ גדול מ־10MB. העלו קובץ קטן יותר.'

/** `POST .../documents/file` accepts a document of at most 10 MB (`docs/api.md` §4). */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024

const EXTENSION_KIND: Record<string, 'pdf' | 'jpeg' | 'png'> = {
  pdf: 'pdf',
  jpg: 'jpeg',
  jpeg: 'jpeg',
  png: 'png',
}

const MIME_KIND: Record<string, 'pdf' | 'jpeg' | 'png'> = {
  'application/pdf': 'pdf',
  'image/jpeg': 'jpeg',
  'image/png': 'png',
}

/**
 * A file whose extension says PDF, JPEG or PNG, and whose type (when the browser reports one)
 * agrees with that same kind - a photo taken on a phone often has no reported type at all, so
 * an empty one is accepted by extension alone, exactly like the previous PDF-only check did.
 */
export function isAcceptedDocumentFile(file: File): boolean {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? ''
  const kind = EXTENSION_KIND[extension]
  if (!kind) return false
  if (file.type === '') return true
  return MIME_KIND[file.type] === kind
}

/** The outcome of a PDF upload, shown next to the request regardless of its status. */
export interface UploadNotice {
  variant: AlertVariant
  text: string
}

/**
 * One Hebrew sentence per `upload.code` (`docs/api.md` §4 and §8, design §5.3). Shared by
 * the `needs_document` file picker and the sub-project 15 reply file picker - the reply
 * route only ever returns a subset of these codes plus `wrong_document_type`, but the
 * mapping is the same one either way, never duplicated. A code this version does not know
 * is shown as the neutral, fail-closed sentence (§14), never the raw code (§12.3).
 */
export function uploadResultMessage(upload: UploadResult): UploadNotice {
  const label = describeDocumentType(upload.document_type)
  switch (upload.code) {
    case 'accepted':
      return { variant: 'ok', text: `המסמך ${label} התקבל. הפנייה ממשיכה בטיפול.` }
    case 'not_required':
      return { variant: 'info', text: `המסמך ${label} תקין, אבל אינו נדרש לתור הזה.` }
    case 'already_received':
      return { variant: 'info', text: `המסמך ${label} כבר התקבל קודם.` }
    case 'wrong_document_type':
      return label
        ? {
            variant: 'error',
            text: `המסמך שהועלה אינו המסמך שהתבקש. זיהינו אותו כ${label}. נא להעלות את המסמך הנכון.`,
          }
        : { variant: 'error', text: 'המסמך שהועלה אינו המסמך שהתבקש. נא להעלות את המסמך הנכון.' }
    case 'not_medical':
      return { variant: 'error', text: 'הקובץ אינו מסמך רפואי, ולכן לא נקלט.' }
    case 'unreadable':
      // Softened (sub-project 17 task 2): the previous text ("ודאו שזה קובץ PDF ברור") claimed
      // the file was unclear even when the real cause was unknown (e.g. a temporary provider
      // failure that was, until this fix, mis-reported as the file's fault).
      return { variant: 'error', text: 'לא הצלחנו לקרוא את המסמך. העלו קובץ PDF או תמונה ברורה של המסמך.' }
    case 'unrecognised_type':
      return { variant: 'error', text: 'לא זיהינו את סוג המסמך. ודאו שהעליתם את המסמך שהתבקש.' }
    case 'unreadable_scan':
      return {
        variant: 'error',
        text: 'לא הצלחנו לקרוא את הסריקה. צלמו את המסמך באור טוב ובחדות, או העלו את קובץ ה־PDF המקורי.',
      }
    case 'bad_date':
      return { variant: 'error', text: 'תאריך המסמך עתידי. ודאו שהעליתם את המסמך הנכון.' }
    case 'no_date':
      return { variant: 'error', text: 'לא מצאנו תאריך על המסמך. העלו מסמך שמופיע עליו תאריך הבדיקה.' }
    case 'unsupported_format':
      return { variant: 'error', text: 'סוג הקובץ אינו נתמך. העלו PDF או תמונה (JPG/PNG).' }
    case 'too_large':
      return { variant: 'error', text: FILE_TOO_LARGE_MESSAGE }
    case 'expired':
      // `document_type` is `null` for `expired` in the common case (`docs/api.md` §4) - an
      // empty `label` must not leave a double space where it would have gone.
      return label
        ? { variant: 'error', text: `המסמך ${label} ישן מדי לפי כללי התוקף. יש להעלות מסמך עדכני.` }
        : { variant: 'error', text: 'המסמך ישן מדי לפי כללי התוקף. יש להעלות מסמך עדכני.' }
    case 'not_yours':
      return { variant: 'error', text: 'המסמך אינו שייך לך, ולכן לא נקלט.' }
    default:
      return { variant: 'error', text: 'המסמך לא נקלט. נסו שוב או פנו למוקד.' }
  }
}

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
  not_waiting_for_reply: 'הפנייה כבר אינה ממתינה לתשובה, ולכן התשובה לא נשלחה. המסך עודכן למצב הנוכחי.',
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
