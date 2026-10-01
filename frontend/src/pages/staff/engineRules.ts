/**
 * What each reasoning engine checked on one audit row, derived only from the row and from the
 * engines' own source files - the Audit keeps each policy row's folded result, not every rule's
 * outcome. Each constant here mirrors a backend file, and `engineRules.test.ts` fails if it
 * drifts: `policy/rules.pl` (Prolog), `policy/flows.dl` and `execution/gateway.py` (Datalog),
 * `policy/readiness.py` (Z3).
 */

export type RuleStatus = 'pass' | 'fail' | 'skip' | 'na' | 'info'

export interface RuleItem {
  code: string
  label: string
  status: RuleStatus
  note?: string
}

export interface RuleList {
  engine: 'OPA' | 'Prolog' | 'Datalog' | 'Z3'
  summary: string
  failed: boolean
  items: RuleItem[]
}

type Row = { event: string; action: string | null; policy_result: string | null; policy_reasons: string[]; guards?: Record<string, boolean> }

// ---- Prolog (policy/rules.pl) -------------------------------------------------------------

/**
 * The checks `allowed/3` makes for the agent's automatic action, in the order `explain/4`
 * tries them - it stops at the first that fails (`!`), so every check before the reported
 * one held and every check after it was never reached. `case_identity_verified` has no
 * `explain` clause of its own: for an automatic action it is the one left for
 * `unspecified_block`.
 */
export const PROLOG_CHECKS: ReadonlyArray<{ code: string; label: string; only?: string }> = [
  { code: 'actor_role_mismatch', label: 'לסוכן יש התפקיד שהפעולה דורשת (automation)' },
  { code: 'medical_action_requires_approval', label: 'אין תוכן רפואי בלי אישור אנושי', only: 'SendStatusUpdate' },
  { code: 'action_not_current_step', label: 'הפעולה היא הצעד הנוכחי בתוכנית' },
  { code: 'message_not_evaluated', label: 'ההודעה למטופל עברה הערכה', only: 'SendStatusUpdate' },
  { code: 'patient_context_missing', label: 'פרטי המטופל, הפנייה והביצוע קיימים' },
  { code: 'unspecified_block', label: 'זהות המטופל אומתה (case_identity_verified)' },
]

/** `prolog:reason(<code>, <action>)` -> `<code>`; `null` for anything else. */
function prologReason(reasons: string[]): string | null {
  for (const reason of reasons) {
    const match = /^prolog:reason\(([a-z_]+),/.exec(reason)
    if (match) return match[1]
  }
  return null
}

export function prologChecksOf(row: Row): RuleList | null {
  if (!row.policy_result || !row.action) return null
  const raw = row.policy_reasons.filter((reason) => reason.startsWith('prolog:'))
  const fired = prologReason(raw)
  // An engine failure or an unsupported action: Prolog never reached its rules.
  if (raw.length > 0 && fired === null) return null
  const firedAt = fired === null ? -1 : PROLOG_CHECKS.findIndex((check) => check.code === fired)
  const items = PROLOG_CHECKS.map((check, index): RuleItem => {
    if (check.only && check.only !== row.action) return { code: check.code, label: check.label, status: 'na' }
    if (firedAt === -1 || index < firedAt) return { code: check.code, label: check.label, status: 'pass' }
    if (index === firedAt) return { code: check.code, label: check.label, status: 'fail' }
    return { code: check.code, label: check.label, status: 'skip' }
  })
  const relevant = items.filter((item) => item.status !== 'na')
  const passed = relevant.filter((item) => item.status === 'pass').length
  return {
    engine: 'Prolog',
    summary: `Prolog: ${passed}/${relevant.length} בדיקות עברו`,
    failed: firedAt !== -1,
    items,
  }
}

// ---- Datalog (policy/flows.dl, execution/gateway.py) --------------------------------------

/** `sensitive/1` of flows.dl: data that must not leave unless minimized for the target. */
export const DATALOG_SENSITIVE: readonly string[] = [
  'patient_text',
  'patient_id',
  'appointment_id',
  'document_id',
  'medical_history',
]

/** `minimized/2` of flows.dl: which sensitive field may reach which external system. */
export const DATALOG_MINIMIZED: Record<string, readonly string[]> = {
  appointment_system: ['patient_id', 'appointment_id'],
  document_system: ['patient_id', 'document_id'],
  patient_channel: ['patient_id'],
}

/** `ACTION_TARGETS` of execution/gateway.py: each action's target and the fields it sends. */
export const ACTION_TARGETS: Record<string, { target: string; fields: readonly string[] }> = {
  CheckAppointment: { target: 'appointment_system', fields: ['patient_id', 'appointment_id'] },
  CheckDocuments: { target: 'document_system', fields: ['patient_id'] },
  LoadInstructions: { target: 'instruction_system', fields: [] },
  SendStatusUpdate: { target: 'patient_channel', fields: ['patient_id'] },
}

const FIELD_LABELS: Record<string, string> = {
  patient_text: 'טקסט הפנייה',
  patient_id: 'מזהה מטופל',
  appointment_id: 'מזהה תור',
  document_id: 'מזהה מסמך',
  medical_history: 'היסטוריה רפואית',
}

/**
 * Datalog runs at build time: flows.dl's `minimized/2` is exported to OPA's data, and OPA's
 * `field_not_minimized` enforces it on every decision. So the verdict is that OPA rule's; the
 * fields are what the action declares it sends (the Audit does not record the fields sent).
 */
export function datalogOf(row: Row): RuleList | null {
  if (!row.policy_result || !row.action) return null
  const declared = ACTION_TARGETS[row.action]
  if (!declared) return null
  if (row.policy_reasons.includes('policy_engine_unavailable')) return null
  const allowed = DATALOG_MINIMIZED[declared.target] ?? []
  const violated = row.policy_result === 'Deny' && row.policy_reasons.includes('field_not_minimized')
  const items = DATALOG_SENSITIVE.map((field): RuleItem => {
    const label = FIELD_LABELS[field] ?? field
    const sent = declared.fields.includes(field)
    if (allowed.includes(field)) {
      return {
        code: field,
        label,
        status: sent && violated ? 'fail' : sent ? 'pass' : 'info',
        note: sent ? 'נשלח · מותר' : 'מותר · לא נשלח',
      }
    }
    return { code: field, label, status: sent ? 'fail' : 'info', note: 'חסום למערכת הזו' }
  })
  return {
    engine: 'Datalog',
    summary: `Datalog: ${declared.target} · ${violated ? 'מזעור נכשל' : 'המזעור נשמר'}`,
    failed: violated,
    items,
  }
}

// ---- Z3 (policy/readiness.py, spec §9.1) --------------------------------------------------

/**
 * The §9.1 model: Z3 searches for a legal SLA scenario in which an uploaded document would not
 * be verified and reviewed before the appointment. UNSAT - no such scenario - makes asking the
 * patient safe. The hours left until the appointment are not kept in the audit row.
 */
export const Z3_MODEL: ReadonlyArray<{ code: string; label: string }> = [
  { code: 'review_h >= 0, review_h <= 4', label: 'בדיקת צוות: 0–4 שעות' },
  { code: 'upload_h >= 0, upload_h <= 24, verify_h >= 1, verify_h <= 2', label: 'מסמך מסוג 1: העלאה עד 24 ש׳, אימות 1–2 ש׳' },
  { code: 'upload_h >= 0, upload_h <= 8, verify_h >= 4, verify_h <= 8', label: 'מסמך מסוג 2: העלאה עד 8 ש׳, אימות 4–8 ש׳' },
  { code: 'upload_h + verify_h + review_h > hours_until', label: 'השאלה: יש תרחיש שחורג מהזמן שנשאר עד התור?' },
]

export function z3ModelOf(row: Row): RuleList | null {
  const unsat = row.event === 'MISSING_INFORMATION_DETECTED' && row.guards?.AskPatientSafe === true
  const verdict = row.policy_reasons.find((reason) => reason.startsWith('z3:'))
  if (!unsat && !verdict) return null
  const result = unsat ? 'unsat' : verdict!.slice('z3:'.length)
  const failed = result !== 'unsat'
  return {
    engine: 'Z3',
    summary: `Z3: ${Z3_MODEL.length - 1} אילוצים · תוצאה ${result.toUpperCase()}`,
    failed,
    items: Z3_MODEL.map((constraint, index) => ({
      code: constraint.code,
      label: constraint.label,
      status: index < Z3_MODEL.length - 1 ? 'info' : failed ? 'fail' : 'pass',
      note: index < Z3_MODEL.length - 1 ? undefined : failed ? 'נמצא תרחיש (דוגמה נגדית)' : 'אין תרחיש כזה (UNSAT)',
    })),
  }
}
