/**
 * Hebrew labels and formatting shared by the staff screens.
 *
 * The staff screens show exactly what the API returned (§12.3): a label is only
 * ever shown *next to* the code it translates, never instead of it, and an
 * unknown code falls back to itself.
 */
import type { Decision, EscalationKind, RequiredField } from '../../api/types'

export const ESCALATION_LABELS: Record<EscalationKind, string> = {
  PatientVerificationFailed: 'זיהוי המטופל נכשל',
  MedicalQuestion: 'שאלה רפואית',
  SafetyEscalation: 'הסלמת בטיחות',
  ClassificationFailed: 'הסיווג נכשל',
  TemporalViolation: 'הפרת כלל זמן',
  PlanningFailed: 'התכנון נכשל',
  PolicyDenied: 'המדיניות דחתה',
  PolicyReview: 'בדיקת מדיניות',
  RetryExhausted: 'הניסיונות מוצו',
  NonIdempotentFailure: 'כשל בפעולה שאינה אידמפוטנטית',
  ExecutionUnknown: 'תוצאת ביצוע לא ידועה',
  Z3Counterexample: 'דוגמה נגדית מ-Z3',
  PatientSlaExpired: 'חלון הזמן למטופל פג',
  DeliveryStepMissing: 'שלב המסירה חסר',
}

/** The Hebrew label of an escalation kind, or the raw code when it is unknown. */
export function escalationLabel(kind: string | null | undefined): string {
  if (!kind) return '—'
  return ESCALATION_LABELS[kind as EscalationKind] ?? kind
}

export const DECISION_LABELS: Record<Decision, string> = {
  approve: 'אישור והמשך',
  resolve: 'סגירת הפנייה',
  reject: 'דחייה',
}

export const REQUIRED_FIELD_LABELS: Record<RequiredField, string> = {
  verified_identity_ref: 'אסמכתת זיהוי',
  patient_deadline: 'מועד יעד חדש למטופל',
}

export const REQUIRED_FIELD_HINTS: Record<RequiredField, string> = {
  verified_identity_ref: 'כיצד בוצע הזיהוי, למשל ID-DESK-17.',
  patient_deadline: 'המועד החדש נשלח עם אזור הזמן של הדפדפן.',
}

/** Data Log kinds (`docs/api.md` §5), in the order the review screen shows them. */
export const DATA_KIND_ORDER = [
  'request_text',
  'uploaded_document',
  'instructions',
  'outgoing_message',
] as const

export const DATA_KIND_LABELS: Record<string, string> = {
  request_text: 'הפנייה',
  uploaded_document: 'מסמכים שהועלו',
  instructions: 'הוראות שנטענו',
  outgoing_message: 'הודעה יוצאת',
}

export function dataKindLabel(kind: string): string {
  return DATA_KIND_LABELS[kind] ?? kind
}

/** Groups Data Log entries by kind, known kinds first, in API order inside a group. */
export function groupByKind<T extends { kind: string }>(entries: T[]): Array<[string, T[]]> {
  const groups = new Map<string, T[]>()
  for (const kind of DATA_KIND_ORDER) {
    const matching = entries.filter((entry) => entry.kind === kind)
    if (matching.length > 0) groups.set(kind, matching)
  }
  for (const entry of entries) {
    if (groups.has(entry.kind)) continue
    if ((DATA_KIND_ORDER as readonly string[]).includes(entry.kind)) continue
    groups.set(entry.kind, entries.filter((other) => other.kind === entry.kind))
  }
  return [...groups.entries()]
}

const DATE_TIME = new Intl.DateTimeFormat('he-IL', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

/** An API timestamp in the browser's own time zone; an unparsable value is shown as is. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : DATE_TIME.format(date)
}

const pad = (value: number) => String(value).padStart(2, '0')

/**
 * `<input type="datetime-local">` gives a local wall-clock string with no zone;
 * `patient_deadline` must carry one (`docs/api.md` §5), so the browser's current
 * offset is appended. `null` when the value is empty or not a real datetime.
 */
export function toIsoWithOffset(localValue: string): string | null {
  if (!localValue.trim()) return null
  const date = new Date(localValue)
  if (Number.isNaN(date.getTime())) return null
  const offsetMinutes = -date.getTimezoneOffset()
  const sign = offsetMinutes < 0 ? '-' : '+'
  const absolute = Math.abs(offsetMinutes)
  const stamp =
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  return `${stamp}${sign}${pad(Math.floor(absolute / 60))}:${pad(absolute % 60)}`
}

/** The message shown for a failed call: the API's own `detail` code, or a network note. */
export function detailOf(error: unknown): string {
  if (error && typeof error === 'object' && 'detail' in error) {
    const detail = (error as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
  }
  return 'network_error'
}
