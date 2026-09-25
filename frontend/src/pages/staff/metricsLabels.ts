/**
 * Labels, number formatting and the date range of the admin metrics screen (sub-project 14,
 * design docs/superpowers/specs/2026-09-24-admin-metrics-design.md §8).
 *
 * The staff screens' rule (./labels): a label is shown next to the code it translates,
 * never instead of it, and an unknown code falls back to itself.
 */
import type { Durations } from '../../api/types'

export const OUTCOME_LABELS: Record<string, string> = {
  MedicalQuestion: 'שאלה רפואית',
  EscalatedAtClassification: 'הוסלמה בשלב הסיווג',
  AppointmentPreparation: 'הכנה לתור',
  Unsupported: 'לא נתמכת',
  NotClassified: 'טרם סווגה',
}

export const EVENT_LABELS: Record<string, string> = {
  CASE_RESOLVED: 'הושלמה אוטומטית',
  HUMAN_RESOLVED_CASE: 'נסגרה ע״י צוות',
  HUMAN_APPROVED: 'אושרה והמשיכה',
  HUMAN_REJECTED: 'נדחתה ע״י צוות',
  TOOL_TRANSIENT_FAILURE: 'כשל זמני',
  RETRY_EXHAUSTED: 'הניסיונות מוצו',
  POLICY_ALLOWED: 'המדיניות אישרה',
  POLICY_DENIED: 'המדיניות דחתה',
  POLICY_HUMAN_REVIEW_REQUIRED: 'המדיניות העבירה לאדם',
  PATIENT_REPLY_REQUESTED: 'נשלחה בקשה למטופל',
}

export const REASON_LABELS: Record<string, string> = {
  'tool:transient_failure:timeout': 'פסק זמן',
  'tool:transient_failure:unavailable': 'השירות לא זמין',
  guard_failed: 'אין מעבר חוקי',
  invalid_escalation_reason: 'סיבת הסלמה לא תקינה',
  system_owned_event: 'אירוע מערכת ממקור חיצוני',
  restart: 'הפעלה מחדש באמצע קריאה',
}

export const SOURCE_LABELS: Record<string, string> = {
  'appointment-service': 'שירות התורים',
  'document-service': 'שירות המסמכים',
}

/** The escalations raised by the formal layers (design §4.5 E3). */
export const FORMAL_KINDS = ['TemporalViolation', 'Z3Counterexample', 'PlanningFailed'] as const

/** What the screen says for the API's own error codes; any other code is shown as is. */
export const ERROR_TEXT: Record<string, string> = {
  admin_only: 'אין הרשאה לצפות במדדים.',
  invalid_range: 'הטווח לא תקין: תחילתו חייבת להיות לפני סופו.',
  range_too_large: 'הטווח ארוך מ־90 יום.',
  metrics_unavailable: 'חישוב המדדים ארך יותר מדי. נסו טווח קצר יותר.',
  network_error: 'אין חיבור לשרת.',
}

export function labelOf(labels: Record<string, string>, code: string | null | undefined): string {
  if (!code) return '—'
  return labels[code] ?? code
}

const COUNT = new Intl.NumberFormat('he-IL')

export function formatCount(value: number): string {
  return COUNT.format(value)
}

export function formatPercent(ratio: number | null): string {
  return ratio === null ? '—' : `${Math.round(ratio * 100)}%`
}

/** Seconds, in the largest unit that keeps the number readable. */
export function formatSeconds(seconds: number | null): string {
  if (seconds === null) return '—'
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}

export function durationsText(durations: Durations): string {
  if (durations.count === 0) return 'אין מדידות'
  return [
    `p50 ${formatSeconds(durations.p50)}`,
    `p95 ${formatSeconds(durations.p95)}`,
    `max ${formatSeconds(durations.max)}`,
  ].join(' · ')
}

export interface BarRow {
  code: string
  label: string
  count: number
  /** React key, when two rows share a code. */
  key?: string
}

/** Counts as bar rows: the largest first, ties by code. */
export function toRows(counts: Record<string, number>, label: (code: string) => string): BarRow[] {
  return Object.entries(counts)
    .map(([code, count]) => ({ code, label: label(code), count }))
    .sort((a, b) => b.count - a.count || a.code.localeCompare(b.code))
}

/**
 * A tool-failure row (one of `tools.failure_reasons`) as a pure-Hebrew label beside its code -
 * never the code itself outside `.mono` (CLAUDE.md: a Hebrew label beside the code, never
 * instead of it; an unknown code falls back to itself).
 *
 * `ExecutionFailed`'s code is known or unknown on its own. `ExecutionUnknown`'s reason (e.g.
 * `exception:ValueError`, `restart`) is almost never in `REASON_LABELS`, so it falls back to the
 * generic Hebrew "תוצאה לא ידועה" instead of repeating the unknown code as if it were a label.
 */
export function failureRow(failure: { outcome: string; reason: string | null; count: number }): BarRow {
  const code = failure.reason ?? failure.outcome
  const known = failure.reason ? REASON_LABELS[failure.reason] : undefined
  const label =
    failure.outcome === 'ExecutionUnknown'
      ? known
        ? `${known} (תוצאה לא ידועה)`
        : 'תוצאה לא ידועה'
      : (known ?? code)
  return { key: `${failure.outcome}|${failure.reason ?? ''}`, code, label, count: failure.count }
}

/** Two `<input type="datetime-local">` values, in the browser's own time zone. */
export interface LocalRange {
  from: string
  to: string
}

const pad = (value: number) => String(value).padStart(2, '0')

export function toLocalInput(date: Date): string {
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

export function fromLocalInput(value: string): Date | null {
  if (!value.trim()) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** Presets first, then the custom range (dataviz: date range first, presets before custom). */
export const PRESETS = [
  { key: '24h', label: '24 שעות', hours: 24 },
  { key: '7d', label: '7 ימים', hours: 24 * 7 },
  { key: '30d', label: '30 יום', hours: 24 * 30 },
  { key: '90d', label: '90 יום', hours: 24 * 90 },
] as const

/** The preset's range, ending at the minute after `now` so that `now` itself is inside. */
export function presetRange(hours: number, now: Date): LocalRange {
  const end = new Date(now.getTime())
  end.setSeconds(0, 0)
  end.setMinutes(end.getMinutes() + 1)
  const start = new Date(end.getTime() - hours * 3600_000)
  return { from: toLocalInput(start), to: toLocalInput(end) }
}
