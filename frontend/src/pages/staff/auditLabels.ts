/**
 * Hebrew labels for the audit timeline: the phase each event belongs to, what each event,
 * record type, guard, action and reason code means. Same rule as `labels.ts`: a label is
 * shown beside its code, never instead of it, and a code this file does not know falls
 * back to itself.
 */
import { safetyLabel } from './labels'

export type Phase = 'intake' | 'classification' | 'planning' | 'execution' | 'readiness' | 'human' | 'closure' | 'other'

export const PHASE_LABELS: Record<Phase, string> = {
  intake: 'קליטה וזיהוי',
  classification: 'סיווג',
  planning: 'תכנון ומדיניות',
  execution: 'ביצוע',
  readiness: 'מוכנות ומסמכים',
  human: 'בקרה אנושית',
  closure: 'סגירה',
  other: 'אחר',
}

const EVENT_PHASES: Record<string, Phase> = {
  REQUEST_SUBMITTED: 'intake',
  REQUEST_VALIDATED: 'intake',
  PATIENT_VERIFICATION_FAILED: 'intake',
  INTENT_CLASSIFIED: 'classification',
  MEDICAL_QUESTION_DETECTED: 'classification',
  PLAN_CREATED: 'planning',
  ACTION_PROPOSED: 'planning',
  POLICY_ALLOWED: 'planning',
  POLICY_DENIED: 'planning',
  POLICY_HUMAN_REVIEW_REQUIRED: 'planning',
  DELIVERY_PLANNED: 'planning',
  TOOL_EXECUTION_STARTED: 'execution',
  AUDIT_RECORDED: 'execution',
  DATA_RETRIEVED: 'execution',
  TOOL_TRANSIENT_FAILURE: 'execution',
  RETRY_EXHAUSTED: 'execution',
  STEP_ADVANCED: 'execution',
  MISSING_INFORMATION_DETECTED: 'readiness',
  READINESS_PASSED: 'readiness',
  DOCUMENT_UPLOADED: 'readiness',
  TIMEOUT_EXPIRED: 'readiness',
  HUMAN_REVIEW_REQUIRED: 'human',
  HUMAN_APPROVED: 'human',
  HUMAN_REJECTED: 'human',
  HUMAN_RESOLVED_CASE: 'human',
  PATIENT_REPLY_REQUESTED: 'human',
  PATIENT_REPLY_SUBMITTED: 'human',
  CASE_RESOLVED: 'closure',
}

export function phaseOf(event: string): Phase {
  return EVENT_PHASES[event] ?? 'other'
}

export const EVENT_LABELS: Record<string, string> = {
  REQUEST_SUBMITTED: 'הפנייה נשלחה',
  REQUEST_VALIDATED: 'הפנייה אומתה',
  PATIENT_VERIFICATION_FAILED: 'זיהוי המטופל נכשל',
  INTENT_CLASSIFIED: 'כוונת הפנייה סווגה',
  MEDICAL_QUESTION_DETECTED: 'זוהתה שאלה רפואית',
  PLAN_CREATED: 'נבנתה תוכנית פעולה',
  ACTION_PROPOSED: 'הוצע הצעד הבא',
  POLICY_ALLOWED: 'המדיניות אישרה את הצעד',
  POLICY_DENIED: 'המדיניות דחתה את הצעד',
  POLICY_HUMAN_REVIEW_REQUIRED: 'המדיניות דרשה בקרה אנושית',
  DELIVERY_PLANNED: 'תוכננה מסירה למטופל',
  TOOL_EXECUTION_STARTED: 'התחיל ביצוע',
  AUDIT_RECORDED: 'הביצוע נרשם ביומן',
  DATA_RETRIEVED: 'הנתונים התקבלו',
  TOOL_TRANSIENT_FAILURE: 'כשל זמני במערכת החיצונית',
  RETRY_EXHAUSTED: 'הניסיונות מוצו',
  STEP_ADVANCED: 'מעבר לצעד הבא בתוכנית',
  MISSING_INFORMATION_DETECTED: 'חסר מידע או מסמך',
  READINESS_PASSED: 'בדיקת המוכנות עברה',
  DOCUMENT_UPLOADED: 'הועלה מסמך תקין',
  TIMEOUT_EXPIRED: 'פג הזמן להמתנה',
  HUMAN_REVIEW_REQUIRED: 'הועברה להכרעת צוות',
  HUMAN_APPROVED: 'הצוות אישר המשך',
  HUMAN_REJECTED: 'הצוות דחה',
  HUMAN_RESOLVED_CASE: 'הצוות סגר את הפנייה',
  PATIENT_REPLY_REQUESTED: 'נשלחה בקשה למטופל',
  PATIENT_REPLY_SUBMITTED: 'המטופל השיב',
  CASE_RESOLVED: 'הפנייה הושלמה',
}

export function eventLabel(event: string): string {
  return EVENT_LABELS[event] ?? event
}

export const RECORD_TYPE_LABELS: Record<string, string> = {
  Transition: 'מעבר',
  Blocked: 'חסימה',
  ExecutionStarted: 'ביצוע',
  ExecutionSucceeded: 'ביצוע הצליח',
  ExecutionFailed: 'ביצוע נכשל',
  ExecutionUnknown: 'תוצאה לא ידועה',
}

export function recordTypeLabel(recordType: string): string {
  return RECORD_TYPE_LABELS[recordType] ?? recordType
}

/**
 * Guard results that are policy evidence rather than a gate: they record a fact about the
 * step (is the content medical, is there a valid approval) and are shown as a yes/no fact,
 * not as a gate that passed.
 */
export const EVIDENCE_KEYS: readonly string[] = ['medical_content_flag', 'ContentApprovalValid']

export function isEvidence(key: string): boolean {
  return EVIDENCE_KEYS.includes(key)
}

export const GUARD_LABELS: Record<string, string> = {
  PatientIdentified: 'המטופל מזוהה',
  RequestValid: 'הפנייה תקינה',
  PlanComplete: 'התוכנית שלמה',
  InPlan: 'הצעד חלק מהתוכנית',
  IdentityVerified: 'הזהות אומתה',
  PatientContextPresent: 'פרטי המטופל והפנייה קיימים',
  AttemptsAvailable: 'נותרו ניסיונות',
  IsIdempotent: 'הפעולה בטוחה לחזרה',
  ReadinessComplete: 'כל מסמכי החובה קיימים',
  ReadinessInProgress: 'בדיקת המוכנות בתהליך',
  DeliveryConfirmed: 'המסירה אושרה',
  PlanIntact: 'התוכנית לא שונתה',
  CanAdvance: 'אפשר להתקדם',
  RetrievalStepsRemain: 'נותרו צעדי אחזור',
  PreReadinessPhaseComplete: 'שלב האחזור הושלם',
  DeliveryStepPending: 'צעד המסירה ממתין',
  DocumentValid: 'המסמך תקין',
  WorkflowDecisionValid: 'החלטת הצוות תקינה',
  ExecutorReverified: 'אומת מחדש לפני הביצוע',
  PatientSlaExpired: 'חלון הזמן למטופל פג',
  SystemEscalationRequired: 'נדרשת הסלמה',
  AskPatientSafe: 'בטוח לפנות למטופל',
  retrieval_action: 'פעולת אחזור',
  delivery_action: 'פעולת מסירה',
  closure_reason: 'סיבת סגירה תקינה',
  valid_classification: 'הסיווג תקין',
  valid_tool_result: 'תוצאת הכלי תקינה',
  deadline_registered: 'נרשם מועד יעד',
  reply_request_valid: 'בקשת התשובה תקינה',
  patient_reply_valid: 'תשובת המטופל תקינה',
  ContentApprovalValid: 'אישור תוכן תקף',
  medical_content_flag: 'תוכן רפואי',
}

/** A guard's Hebrew label; `!X` (the guard X must not hold) reads "לא: <label of X>". */
export function guardLabel(key: string): string {
  if (key.startsWith('!')) {
    const inner = key.slice(1)
    return `לא: ${GUARD_LABELS[inner] ?? inner}`
  }
  return GUARD_LABELS[key] ?? key
}

export const ACTION_LABELS: Record<string, string> = {
  CheckAppointment: 'בדיקת התור',
  CheckDocuments: 'בדיקת המסמכים',
  LoadInstructions: 'טעינת הוראות ההכנה',
  SendStatusUpdate: 'שליחת עדכון למטופל',
  CloseMedicalCase: 'סגירת פנייה רפואית',
  AnswerClinicalQuestion: 'מענה קליני',
}

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action
}

export const POLICY_RESULT_LABELS: Record<string, string> = {
  Allow: 'אושר',
  Deny: 'נדחה',
  RequireHumanReview: 'נדרשת בקרה אנושית',
}

export function policyResultLabel(result: string): string {
  return POLICY_RESULT_LABELS[result] ?? result
}

export const OUTCOME_LABELS: Record<string, string> = {
  success: 'הצליח',
  failed: 'נכשל',
  unknown: 'לא ידוע',
}

export function outcomeLabel(outcome: string): string {
  return OUTCOME_LABELS[outcome] ?? outcome
}

/**
 * Every `deny` rule of `policy/policy.rego`, in file order, with what it guarantees. OPA
 * evaluates all of them on every policy decision (`data.hospital_agent.policy.decision`);
 * a Deny's reasons are exactly the rules that fired, so every other one passed.
 * `AuditTimeline.test.tsx` fails if this list drifts from the Rego file.
 */
export const OPA_DENY_RULES: Record<string, string> = {
  invalid_safety_level: 'רמת הבטיחות תקינה',
  identity_not_verified: 'זהות המטופל אומתה',
  patient_verification_failed: 'אימות המטופל לא נכשל',
  missing_patient_context: 'פרטי המטופל והפנייה קיימים',
  action_not_in_plan: 'הפעולה היא הצעד הנוכחי בתוכנית',
  action_not_supported: 'הפעולה מוכרת',
  plan_modified: 'התוכנית לא שונתה',
  invalid_attempt_budget: 'תקציב הניסיונות תקין',
  attempts_exhausted: 'הניסיונות לא מוצו',
  message_not_evaluated: 'הודעה למטופל עברה הערכה',
  medical_answer_attempt: 'אין תשובה רפואית בלי אישור תוכן',
  approval_is_workflow_only: 'אישור זרימה אינו משמש כאישור תוכן',
  invalid_patient_fields: 'שדות המטופל תקינים',
  field_not_minimized: 'נשלחים רק שדות מותרים (מזעור, Datalog)',
  unknown_target_system: 'מערכת היעד מוכרת',
  unapproved_instruction_source: 'מקור ההוראות מאושר',
  instruction_source_expired: 'מקור ההוראות לא פג תוקף',
  instruction_source_not_yet_valid: 'מקור ההוראות כבר בתוקף',
}

/**
 * The OPA rules on a policy row, each passed or fired - or `null` when OPA did not rule
 * (not a policy row, or OPA failed closed with `policy_engine_unavailable`).
 */
export function opaRulesOf(row: { policy_result: string | null; policy_reasons: string[] }):
  | Array<{ code: string; label: string; passed: boolean }>
  | null {
  if (!row.policy_result) return null
  if (row.policy_result === 'Deny' && row.policy_reasons.includes('policy_engine_unavailable')) return null
  const fired = new Set(row.policy_result === 'Deny' ? row.policy_reasons : [])
  return Object.entries(OPA_DENY_RULES).map(([code, label]) => ({ code, label, passed: !fired.has(code) }))
}

export interface EngineVerdict {
  engine: 'OPA' | 'Prolog' | 'Datalog' | 'Z3'
  verdict: string
  tone: 'good' | 'bad' | 'warn' | 'neutral'
}

/**
 * Which reasoning engine decided a row, derived only from what the row itself holds - the
 * Audit keeps the folded policy result, not each engine's own verdict:
 *
 * - A policy row: `PolicyService.decide` (`policy/service.py`) folds OPA and Prolog - Allow
 *   only when OPA allows and Prolog allows; Deny when either blocks, with OPA's reasons and
 *   Prolog's as `prolog:<explanation>`; RequireHumanReview is OPA's, once Prolog allowed.
 *   `policy_engine_unavailable` alone is OPA failing closed.
 * - Z3 runs only in the Readiness Check when a document is missing: `AskPatientSafe` holds
 *   only on UNSAT (`guards.ask_patient_safe`); a counterexample escalates with `z3:<result>`.
 *   `READINESS_PASSED` never calls Z3 - every required document is already held.
 */
export function enginesOf(row: { event: string; policy_result: string | null; policy_reasons: string[]; guards?: Record<string, boolean> }): EngineVerdict[] {
  const engines: EngineVerdict[] = []
  const reasons = row.policy_reasons
  if (row.policy_result === 'Allow') {
    engines.push({ engine: 'OPA', verdict: 'אישר', tone: 'good' }, { engine: 'Prolog', verdict: 'אישר', tone: 'good' })
  } else if (row.policy_result === 'RequireHumanReview') {
    engines.push(
      { engine: 'OPA', verdict: 'דרש בקרה אנושית', tone: 'warn' },
      { engine: 'Prolog', verdict: 'אישר', tone: 'good' },
    )
  } else if (row.policy_result === 'Deny') {
    const prolog = reasons.filter((reason) => reason.startsWith('prolog:'))
    const opa = reasons.filter((reason) => !reason.startsWith('prolog:'))
    const opaDown = opa.length > 0 && opa.every((reason) => reason === 'policy_engine_unavailable')
    engines.push(
      opaDown
        ? { engine: 'OPA', verdict: 'לא זמין (נכשל סגור)', tone: 'bad' }
        : opa.length > 0
          ? { engine: 'OPA', verdict: 'חסם', tone: 'bad' }
          : { engine: 'OPA', verdict: 'לא חסם', tone: 'neutral' },
      prolog.includes('prolog:policy_engine_unavailable')
        ? { engine: 'Prolog', verdict: 'לא זמין (נכשל סגור)', tone: 'bad' }
        : prolog.length > 0
          ? { engine: 'Prolog', verdict: 'חסם', tone: 'bad' }
          : { engine: 'Prolog', verdict: 'אישר', tone: 'good' },
    )
  }
  if (row.event === 'MISSING_INFORMATION_DETECTED' && row.guards?.AskPatientSafe === true) {
    engines.push({ engine: 'Z3', verdict: 'UNSAT · בטוח לבקש מהמטופל', tone: 'good' })
  }
  const z3 = reasons.find((reason) => reason.startsWith('z3:'))
  if (z3) {
    const result = z3.slice('z3:'.length)
    engines.push({
      engine: 'Z3',
      verdict: result === 'sat' ? 'SAT · נמצאה דוגמה נגדית' : `${result} · לא הוכרע`,
      tone: 'bad',
    })
  }
  if (row.event === 'READINESS_PASSED') {
    engines.push({ engine: 'Z3', verdict: 'לא נדרש · כל המסמכים קיימים', tone: 'neutral' })
  }
  return engines
}

/** Row 98: what a patient upload attempt ended as (the code the patient was shown). */
export const UPLOAD_OUTCOME_LABELS: Record<string, string> = {
  accepted: 'המסמך התקבל',
  not_required: 'המסמך אינו נדרש בפנייה',
  already_received: 'המסמך כבר התקבל',
  wrong_document_type: 'סוג מסמך שונה מהמבוקש',
  not_medical: 'המסמך אינו רפואי',
  not_yours: 'המסמך שייך למטופל אחר',
  unreadable: 'המסמך אינו קריא',
  unrecognised_type: 'סוג המסמך לא זוהה',
  unreadable_scan: 'סריקה שאינה קריאה',
  bad_date: 'תאריך המסמך אינו תקין',
  no_date: 'למסמך אין תאריך',
  expired: 'המסמך ישן מדי',
  unsupported_format: 'פורמט קובץ שאינו נתמך',
  too_large: 'הקובץ גדול מדי',
  document_service_unavailable: 'שירות המסמכים לא היה זמין',
  not_waiting_for_document: 'הפנייה כבר לא המתינה למסמך',
}

/** Row 98: why the document-service could not be used (`IntakeUnavailable`'s code). */
export const UPLOAD_REASON_LABELS: Record<string, string> = {
  no_answer: 'לא התקבלה תשובה',
  invalid_response: 'תשובה שאינה לפי החוזה',
  classifier_unavailable: 'ספק ה-LLM של הסיווג אינו זמין',
}

export function uploadOutcomeLabel(code: string): string {
  return UPLOAD_OUTCOME_LABELS[code] ?? code
}

export function uploadReasonLabel(code: string | null): string | null {
  if (!code) return null
  if (UPLOAD_REASON_LABELS[code]) return UPLOAD_REASON_LABELS[code]
  return /^status_\d+$/.test(code) ? `השירות החזיר ${code.slice('status_'.length)}` : null
}

/** The temporal rules (spec §6.2, plus sub-project 15's T13), in one line each. */
export const TEMPORAL_RULE_LABELS: Record<string, string> = {
  T1: 'כל ביצוע מיד אחרי אישור מדיניות',
  T2: 'אין ביצוע מחוץ לתוכנית',
  T3: 'אין ביצוע על מטופל לא מאומת',
  T4: 'אין ביצוע בלי מזהי מטופל, פנייה וביצוע',
  T5: 'שאלה רפואית עוברת מיד לאדם',
  T6: 'תוכן רפואי יוצא רק אחרי אישור אנושי תקף',
  T7: 'מיצוי ניסיונות מסלים לצוות',
  T8: 'מוכנות מחייבת את כל מסמכי החובה',
  T9: 'כל ביצוע נרשם ביומן',
  T10: 'מסמך תקין שהועלה חוזר לסיווג',
  T11: 'ממצב סיום עוברים רק למצב סיום',
  T12: 'אין ביצוע כשהניסיונות נגמרו',
  T13: 'כלל ההרחבה של תשובת המטופל',
}

const REASON_LABELS: Record<string, string> = {
  guard_failed: 'שער לא התקיים',
  identity_not_established: 'זהות המטופל לא אומתה',
  system_owned_event: 'האירוע מותר רק לרכיב המערכת שאחראי עליו',
  content_approval_invalid: 'אישור התוכן אינו תקף',
  invalid_escalation_reason: 'סיבת ההסלמה אינה תקינה',
  executor_reverification_failed: 'האימות מחדש לפני הביצוע נכשל',
  attempts_exhausted: 'הניסיונות מוצו',
  plan_incomplete: 'ה-Planner לא החזיר תוכנית שלמה',
  intent_unsupported: 'הכוונה אינה נתמכת בטיפול אוטומטי (רק הכנה לתור)',
}

/**
 * What a policy/escalation/blocking reason code means, or `null` when this version does not
 * know it (the code alone is then shown). Codes with a detail after `:` are matched on
 * their prefix; the detail stays in the code shown beside the label.
 */
export function reasonLabel(reason: string): string | null {
  if (REASON_LABELS[reason]) return REASON_LABELS[reason]
  if (OPA_DENY_RULES[reason]) return `כלל OPA נכשל: ${OPA_DENY_RULES[reason]}`
  const [prefix, ...rest] = reason.split(':')
  const detail = rest.join(':')
  switch (prefix) {
    case 'temporal_violation': {
      const rule = TEMPORAL_RULE_LABELS[detail]
      return rule ? `הפרת כלל זמן ${detail}: ${rule}` : 'הפרת כלל זמן'
    }
    case 'safety_level':
      return `רמת סיכון: ${safetyLabel(detail)}`
    case 'llm_failed':
      return 'המודל לא החזיר תשובה שמישה שלוש פעמים ברצף'
    case 'tool':
      return rest[0] === 'transient_failure' ? 'כשל זמני במערכת החיצונית' : 'שגיאה במערכת החיצונית'
    case 'content_check_failed':
      return 'בדיקת הבטיחות של התוכן שהתקבל נכשלה'
    case 'execution_error':
      return 'שגיאה בביצוע'
    case 'execution_unknown':
      return 'תוצאת הביצוע אינה ידועה'
    case 'result_event_blocked':
      return 'אירוע התוצאה נחסם'
    case 'z3':
      return 'תוצאת בדיקת המוכנות (Z3)'
    case 'z3_detail':
      return 'פירוט בדיקת המוכנות (Z3)'
    case 'prolog':
      return 'הסבר מנוע הכללים (Prolog)'
    default:
      return null
  }
}
