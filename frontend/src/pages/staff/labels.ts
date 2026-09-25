/**
 * Hebrew labels and formatting shared by the staff screens.
 *
 * The staff screens show exactly what the API returned (§12.3): a label is only
 * ever shown *next to* the code it translates, never instead of it, and an
 * unknown code falls back to itself.
 */
import type { Decision, EscalationKind, RequiredField, SafetyLevel, State } from '../../api/types'

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

/**
 * The 13 States (§2.1, plus sub-project 15's `AwaitingPatientReply`) in Hebrew. Shown
 * next to the State itself, never instead of it - the code is what the spec, the Audit
 * and the guards all use.
 */
export const STATE_LABELS: Record<State, string> = {
  Received: 'התקבלה',
  Classifying: 'בסיווג',
  Classified: 'סווגה',
  Planning: 'בתכנון',
  RetrievingData: 'באחזור נתונים',
  Delivering: 'במסירה',
  AssessingReadiness: 'בבדיקת מוכנות',
  AwaitingPatientInput: 'ממתינה למטופל',
  AwaitingHumanReview: 'ממתינה להכרעת צוות',
  Ready: 'מוכנה למסירה',
  Completed: 'הושלמה',
  Failed: 'נכשלה',
  AwaitingPatientReply: 'ממתינה לתשובת המטופל',
}

export function stateLabel(state: string | null | undefined): string {
  if (!state) return '—'
  return STATE_LABELS[state as State] ?? state
}

/** The three intents the Classifier may return (`docs/api.md` §5). */
export const INTENT_LABELS: Record<string, string> = {
  AppointmentPreparation: 'הכנה לתור',
  MedicalQuestion: 'שאלה רפואית',
  Unsupported: 'לא נתמכת',
}

export function intentLabel(intent: string | null | undefined): string {
  if (!intent) return '—'
  return INTENT_LABELS[intent] ?? intent
}

export const SAFETY_LABELS: Record<SafetyLevel, string> = {
  LowRisk: 'סיכון נמוך',
  MediumRisk: 'סיכון בינוני',
  HighRisk: 'סיכון גבוה',
  CriticalRisk: 'סיכון קריטי',
}

export function safetyLabel(level: string | null | undefined): string {
  if (!level) return '—'
  return SAFETY_LABELS[level as SafetyLevel] ?? level
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

/**
 * Document ids/types the demo and the sub-project 11 catalog use, for the Case
 * Monitor's required/held/missing facts (§5.3). A code shows beside its own label,
 * never instead of it, and an id this version does not know falls back to itself.
 */
export const DOCUMENT_LABELS: Record<string, string> = {
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

/** A document code with its Hebrew label beside it; an unknown code is shown alone. */
export function documentLabel(documentId: string): string {
  const label = DOCUMENT_LABELS[documentId]
  return label ? `${documentId} (${label})` : documentId
}

export const DATA_KIND_LABELS: Record<string, string> = {
  request_text: 'הפנייה',
  uploaded_document: 'מסמכים שהועלו',
  instructions: 'הוראות שנטענו',
  outgoing_message: 'הודעה יוצאת',
  staff_message: 'הודעת צוות למטופל',
  patient_reply: 'תשובת המטופל',
}

export function dataKindLabel(kind: string): string {
  return DATA_KIND_LABELS[kind] ?? kind
}

/** Sub-project 15: how a case last came back to the review queue. */
export const RETURNED_BY_LABELS: Record<'patient_reply' | 'reply_timeout', string> = {
  patient_reply: 'התקבלה תשובת מטופל',
  reply_timeout: 'לא נענתה בזמן',
}

/** The Hebrew label for `returned_by`, or the raw code when it is unknown. */
export function returnedByLabel(code: string | null | undefined): string {
  if (!code) return '—'
  return RETURNED_BY_LABELS[code as 'patient_reply' | 'reply_timeout'] ?? code
}

/**
 * Sub-project 15: the error codes from the staff request/decision-message routes
 * (`docs/api.md` §5, §8) - a Hebrew sentence beside the code, never instead of it.
 */
export const REQUEST_ERROR_LABELS: Record<string, string> = {
  invalid_request: 'בקשה לא תקינה',
  invalid_template: 'התבנית אינה ידועה או אינה מתאימה לסוג הבקשה',
  unexpected_param: 'הבקשה כוללת פרמטר שהתבנית או סוג הבקשה אינם מקבלים',
  invalid_param: 'ערך הפרמטר אינו ברשימה הסגורה של התבנית',
  message_required: 'יש להזין טקסט להודעה',
  document_service_not_configured: 'שירות המסמכים אינו מוגדר במערכת',
  invalid_deadline: 'מועד היעד אינו תקין',
  message_not_allowed: 'לא ניתן לצרף הודעה לאישור המשך',
  human_engaged: 'לא ניתן לאשר המשך: כבר נשלחה בקשה למטופל בפנייה זו',
  clinical_staff_only: 'הפעולה מותרת לאיש צוות קליני בלבד',
  context_changed: 'המידע המוצג השתנה. יש לרענן ולנסות שוב',
  not_in_review: 'הפנייה אינה ממתינה להכרעת צוות',
  reason_required: 'יש לציין סיבה',
}

/** The Hebrew label for a request/decision-message error code, or the code when it is unknown. */
export function requestErrorLabel(code: string): string {
  return REQUEST_ERROR_LABELS[code] ?? code
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

const AUDIT_CLOCK = new Intl.DateTimeFormat('he-IL', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  fractionalSecondDigits: 3,
})

/**
 * An audit row's time, to the millisecond. A whole case's rows are written within a
 * second or two, so minutes alone would stamp the entire trace with one time.
 */
export function formatAuditTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : AUDIT_CLOCK.format(date)
}

const DATE_ONLY = new Intl.DateTimeFormat('he-IL', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : DATE_ONLY.format(date)
}

/**
 * How long after the previous row this one was written ("+0.412 שנ׳"), so the trace
 * reads as a sequence and not as a list of near-identical timestamps. `null` when
 * there is no previous row or either time is unusable.
 */
export function gapAfter(previous: string | null | undefined, value: string): string | null {
  if (!previous) return null
  const gap = new Date(value).getTime() - new Date(previous).getTime()
  if (!Number.isFinite(gap) || gap < 0) return null
  return `+${(gap / 1000).toFixed(3)} שנ׳`
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
