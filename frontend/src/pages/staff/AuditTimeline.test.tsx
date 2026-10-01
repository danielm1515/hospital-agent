import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { TraceRow } from '../../api/types'
import { AuditTimeline, formatDuration } from './AuditTimeline'
import { OPA_DENY_RULES, enginesOf, guardLabel, opaRulesOf, reasonLabel } from './auditLabels'

let nextId = 1
function row(overrides: Partial<TraceRow>): TraceRow {
  const id = nextId++
  return {
    audit_id: id,
    record_type: 'Transition',
    event: 'REQUEST_SUBMITTED',
    state_before: null,
    state_after: 'Received',
    action: null,
    policy_result: null,
    policy_reasons: [],
    recorded_at: `2026-09-19T22:12:39.${String(100 + id).padStart(3, '0')}Z`,
    guards: {},
    outcome: null,
    attempt_number: null,
    retry_cycle: null,
    execution_id: null,
    approval_id: null,
    ...overrides,
  }
}

const TRACE: TraceRow[] = [
  row({ event: 'REQUEST_SUBMITTED', guards: { PatientIdentified: true } }),
  row({
    event: 'REQUEST_VALIDATED',
    state_before: 'Received',
    state_after: 'Classifying',
    guards: { RequestValid: true, IdentityVerified: true },
  }),
  row({ event: 'INTENT_CLASSIFIED', state_before: 'Classifying', state_after: 'Classified' }),
  row({
    event: 'POLICY_ALLOWED',
    state_before: 'Planning',
    state_after: 'RetrievingData',
    action: 'CheckAppointment',
    policy_result: 'Allow',
    guards: { InPlan: true, medical_content_flag: false },
  }),
  row({
    record_type: 'ExecutionStarted',
    event: 'TOOL_EXECUTION_STARTED',
    state_before: null,
    state_after: null,
    action: 'CheckAppointment',
    attempt_number: 1,
    execution_id: 'EXEC-1',
    guards: { IdentityVerified: true, AttemptsAvailable: true },
  }),
  row({
    record_type: 'ExecutionFailed',
    event: 'TOOL_TRANSIENT_FAILURE',
    state_before: null,
    state_after: null,
    action: 'CheckAppointment',
    attempt_number: 1,
    outcome: 'failed',
    policy_reasons: ['tool:transient_failure:unavailable'],
  }),
  row({
    record_type: 'Blocked',
    event: 'HUMAN_REVIEW_REQUIRED',
    state_before: 'RetrievingData',
    state_after: 'RetrievingData',
    policy_reasons: ['temporal_violation:T3'],
  }),
]

describe('AuditTimeline', () => {
  it('sums up the gates passed, policy decisions, blocks and execution attempts', () => {
    render(<AuditTimeline rows={TRACE} />)
    const summary = screen.getByLabelText('סיכום יומן המעקב')
    const stat = (label: string) => within(summary).getByText(label).closest('.audit-stat') as HTMLElement
    // PatientIdentified + RequestValid + IdentityVerified + InPlan + IdentityVerified + AttemptsAvailable;
    // medical_content_flag is evidence, not a gate.
    expect(stat('שערים שעברו')).toHaveTextContent('6')
    expect(stat('החלטות מדיניות (OPA + Prolog)')).toHaveTextContent('1 אושרו · 0 לא אושרו')
    expect(stat('חסימות')).toHaveTextContent('1')
    expect(stat('ניסיונות ביצוע')).toHaveTextContent('0 הצליחו · 1 נכשלו')
  })

  it('groups consecutive rows into the phase of the flow, keeping one running index', () => {
    const { container } = render(<AuditTimeline rows={TRACE} />)
    const phases = [...container.querySelectorAll('.audit-phase-title')].map((title) => title.textContent)
    expect(phases).toEqual(['קליטה וזיהוי', 'סיווג', 'תכנון ומדיניות', 'ביצוע', 'בקרה אנושית'])
    const indexes = [...container.querySelectorAll('.audit-timeline-index')].map((index) => index.textContent)
    expect(indexes).toEqual(['1.', '2.', '3.', '4.', '5.', '6.', '7.'])
    expect(screen.getByText(/7 רשומות/)).toBeInTheDocument()
  })

  it('labels each event in Hebrew beside its code', () => {
    render(<AuditTimeline rows={TRACE} />)
    expect(screen.getByText('הפנייה אומתה')).toBeInTheDocument()
    expect(screen.getByText('REQUEST_VALIDATED')).toBeInTheDocument()
  })

  it('shows the gates a row passed, and the policy evidence as a yes/no check', () => {
    const { container } = render(<AuditTimeline rows={TRACE} />)
    const validated = container.querySelectorAll('.audit-timeline-item')[1] as HTMLElement
    const gates = [...validated.querySelectorAll('.audit-gate.pass')].map((gate) => gate.textContent)
    expect(gates).toEqual(['✓ הפנייה תקינה RequestValid', '✓ הזהות אומתה IdentityVerified'])

    const policy = container.querySelectorAll('.audit-timeline-item')[3] as HTMLElement
    expect(policy.querySelector('.audit-gate.check')).toHaveTextContent('תוכן רפואי: לא medical_content_flag')
    expect(within(policy).getByText('אושר')).toHaveClass('audit-badge', 'policy-Allow')
  })

  it('shows an execution attempt with its number and outcome, and colours the failure', () => {
    const { container } = render(<AuditTimeline rows={TRACE} />)
    const failed = container.querySelectorAll('.audit-timeline-item')[5] as HTMLElement
    expect(failed).toHaveClass('tone-bad')
    expect(failed).toHaveTextContent('פעולה: בדיקת התור CheckAppointment · ניסיון 1')
    expect(within(failed).getByText('נכשל')).toHaveClass('outcome-failed')
    expect(within(failed).getByText('tool:transient_failure:unavailable')).toBeInTheDocument()
    expect(
      within(failed).getByText('כשל זמני במערכת החיצונית', { selector: '.audit-reason-label' }),
    ).toBeInTheDocument()
  })

  it('names a block and explains its reason, keeping the code', () => {
    const { container } = render(<AuditTimeline rows={TRACE} />)
    const blocked = container.querySelectorAll('.audit-timeline-item')[6] as HTMLElement
    expect(within(blocked).getByText('סיבת החסימה')).toBeInTheDocument()
    expect(within(blocked).getByText('temporal_violation:T3')).toBeInTheDocument()
    expect(within(blocked).getByText('הפרת כלל זמן T3: אין ביצוע על מטופל לא מאומת')).toBeInTheDocument()
  })

  it('shows a reason or a guard it does not know as the code alone', () => {
    expect(reasonLabel('something_new')).toBeNull()
    expect(guardLabel('BrandNewGuard')).toBe('BrandNewGuard')
    expect(guardLabel('!ReadinessComplete')).toBe('לא: כל מסמכי החובה קיימים')
  })

  it('formats the total span from milliseconds up to days', () => {
    expect(formatDuration(1842)).toBe('1.842 שנ׳')
    expect(formatDuration(192_000)).toBe('3 דק׳ 12 שנ׳')
    expect(formatDuration(26 * 3600_000)).toBe('1 ימים 2 שע׳')
  })

  it('names OPA and Prolog on a policy row, and explains the engines in a legend', () => {
    const { container } = render(<AuditTimeline rows={TRACE} />)
    const policy = container.querySelectorAll('.audit-timeline-item')[3] as HTMLElement
    const engines = [...policy.querySelectorAll('.audit-engine')].map((engine) => engine.textContent)
    expect(engines).toEqual(['OPA אישר', 'Prolog אישר', 'Datalog המזעור נשמר'])
    const legend = screen.getByLabelText('מקרא מנועי ההחלטה')
    expect(legend).toHaveTextContent('Datalog')
    expect(legend).toHaveTextContent('Temporal Monitor')
  })

  it('tells which engine blocked a denied step from its reasons', () => {
    const verdicts = (reasons: string[]) =>
      enginesOf({ event: 'POLICY_DENIED', policy_result: 'Deny', policy_reasons: reasons }).map(
        (e) => `${e.engine}:${e.verdict}`,
      )
    expect(verdicts(['prolog:not_in_plan'])).toEqual(['OPA:לא חסם', 'Prolog:חסם'])
    expect(verdicts(['minimization_violation'])).toEqual(['OPA:חסם', 'Prolog:אישר'])
    expect(verdicts(['policy_engine_unavailable'])).toEqual(['OPA:לא זמין (נכשל סגור)', 'Prolog:אישר'])
    expect(
      enginesOf({ event: 'POLICY_HUMAN_REVIEW_REQUIRED', policy_result: 'RequireHumanReview', policy_reasons: [] }).map(
        (e) => `${e.engine}:${e.verdict}`,
      ),
    ).toEqual(['OPA:דרש בקרה אנושית', 'Prolog:אישר'])
  })

  it('shows Z3 only where it ran: UNSAT on a missing document, SAT on a counterexample, not on readiness passed', () => {
    const z3 = (event: string, reasons: string[], guards: Record<string, boolean> = {}) =>
      enginesOf({ event, policy_result: null, policy_reasons: reasons, guards }).map((e) => e.verdict)
    expect(z3('MISSING_INFORMATION_DETECTED', [], { AskPatientSafe: true })).toEqual(['UNSAT · בטוח לבקש מהמטופל'])
    expect(z3('HUMAN_REVIEW_REQUIRED', ['z3:sat', 'z3_detail:[h = 2]'])).toEqual(['SAT · נמצאה דוגמה נגדית'])
    expect(z3('READINESS_PASSED', [])).toEqual(['לא נדרש · כל המסמכים קיימים'])
    expect(z3('REQUEST_VALIDATED', [])).toEqual([])

    render(
      <AuditTimeline
        rows={[
          row({ event: 'MISSING_INFORMATION_DETECTED', guards: { AskPatientSafe: true } }),
          row({ event: 'READINESS_PASSED' }),
        ]}
      />,
    )
    const summary = screen.getByLabelText('סיכום יומן המעקב')
    expect(within(summary).getByText('בדיקות Z3').closest('.audit-stat')).toHaveTextContent('1')
  })

  it('lists every deny rule of policy.rego, in file order', () => {
    // Paths are relative to the Vitest root, i.e. `frontend/`.
    const rego = readFileSync(resolve(process.cwd(), '../backend/hospital_agent/policy/policy.rego'), 'utf-8')
    const rules = [...rego.matchAll(/deny contains "([a-z_]+)"/g)].map((match) => match[1])
    expect(Object.keys(OPA_DENY_RULES)).toEqual(rules)
  })

  it('shows all OPA rules as passed on an allowed step, and marks the ones a denial fired', () => {
    const { container } = render(<AuditTimeline rows={TRACE} />)
    const policy = container.querySelectorAll('.audit-timeline-item')[3] as HTMLElement
    const total = Object.keys(OPA_DENY_RULES).length
    expect(within(policy).getByText(`OPA: ${total}/${total} כללי deny עברו`)).toBeInTheDocument()
    expect(policy.querySelectorAll('.audit-opa-rules .audit-gate.pass')).toHaveLength(total)

    const denied = opaRulesOf({ policy_result: 'Deny', policy_reasons: ['attempts_exhausted', 'prolog:x'] })
    expect(denied?.filter((rule) => !rule.passed).map((rule) => rule.code)).toEqual(['attempts_exhausted'])
    expect(opaRulesOf({ policy_result: 'Deny', policy_reasons: ['policy_engine_unavailable'] })).toBeNull()
    expect(opaRulesOf({ policy_result: null, policy_reasons: [] })).toBeNull()
    expect(reasonLabel('field_not_minimized')).toBe('כלל OPA נכשל: נשלחים רק שדות מותרים (מזעור, Datalog)')
  })

  it('opens a check list per engine on a policy row: OPA, Prolog and Datalog', () => {
    const { container } = render(<AuditTimeline rows={TRACE} />)
    const policy = container.querySelectorAll('.audit-timeline-item')[3] as HTMLElement
    const summaries = [...policy.querySelectorAll('.audit-rule-list summary')].map((summary) => summary.textContent)
    expect(summaries).toEqual([
      `OPA: ${Object.keys(OPA_DENY_RULES).length}/${Object.keys(OPA_DENY_RULES).length} כללי deny עברו`,
      'Prolog: 4/4 בדיקות עברו',
      'Datalog: appointment_system · המזעור נשמר',
    ])
    expect(within(policy).getByText('לסוכן יש התפקיד שהפעולה דורשת (automation)')).toBeInTheDocument()
  })

  it('opens the Z3 model on the row where the readiness check asked it', () => {
    const { container } = render(
      <AuditTimeline rows={[row({ event: 'MISSING_INFORMATION_DETECTED', guards: { AskPatientSafe: true } })]} />,
    )
    expect(container.querySelector('.audit-rule-list summary')).toHaveTextContent('Z3: 3 אילוצים · תוצאה UNSAT')
    expect(screen.getByText('upload_h + verify_h + review_h > hours_until')).toBeInTheDocument()
  })

  it("shows the case's classification on the row that classified it", () => {
    const { container } = render(
      <AuditTimeline rows={TRACE} context={{ intent: 'Unsupported', safety_level: 'LowRisk', llm_calls: [] }} />,
    )
    const classified = container.querySelectorAll('.audit-timeline-item')[2] as HTMLElement
    expect(classified.querySelector('.audit-classification')).toHaveTextContent(
      'סיווג: כוונה לא נתמכת Unsupported · רמת בטיחות סיכון נמוך LowRisk',
    )
    expect(container.querySelectorAll('.audit-classification')).toHaveLength(1)
  })

  it('says an earlier classification of a re-classified case was replaced, never showing a value it lacks', () => {
    const rows = [
      row({ event: 'INTENT_CLASSIFIED' }),
      row({ event: 'DOCUMENT_UPLOADED' }),
      row({ event: 'INTENT_CLASSIFIED' }),
    ]
    const { container } = render(
      <AuditTimeline rows={rows} context={{ intent: 'AppointmentPreparation', safety_level: 'LowRisk' }} />,
    )
    const lines = [...container.querySelectorAll('.audit-classification')]
    expect(lines[0]).toHaveClass('replaced')
    expect(lines[0]).not.toHaveTextContent('AppointmentPreparation')
    expect(lines[1]).toHaveTextContent('הכנה לתור')
  })

  it('puts each LLM call on the first row written after it, and counts them in the summary', () => {
    const rows = [
      row({ event: 'REQUEST_VALIDATED', recorded_at: '2026-09-30T21:01:10.000Z' }),
      row({ event: 'INTENT_CLASSIFIED', recorded_at: '2026-09-30T21:01:15.000Z' }),
      row({ event: 'HUMAN_REVIEW_REQUIRED', recorded_at: '2026-09-30T21:01:19.000Z' }),
    ]
    const llm_calls = [
      { call: 'Intent', source: 'agent', outcome: 'ok', created_at: '2026-09-30T21:01:14.000Z' },
      { call: 'Safety', source: 'agent', outcome: 'ok', created_at: '2026-09-30T21:01:14.500Z' },
      { call: 'Planner', source: 'agent', outcome: 'schema_violation', created_at: '2026-09-30T21:01:18.000Z' },
    ]
    const { container } = render(<AuditTimeline rows={rows} context={{ llm_calls }} />)
    const items = container.querySelectorAll('.audit-timeline-item')
    const callsOf = (item: Element) => [...item.querySelectorAll('.audit-gate')].map((gate) => gate.textContent)
    expect(callsOf(items[0])).toEqual([])
    expect(callsOf(items[1])).toEqual(['✓ סיווג כוונה Intent', '✓ סיווג סיכון Safety'])
    expect(callsOf(items[2])).toEqual(['✗ תכנון Planner · schema_violation'])
    const summary = screen.getByLabelText('סיכום יומן המעקב')
    expect(within(summary).getByText('קריאות LLM').closest('.audit-stat')).toHaveTextContent('31 נכשלו')
  })

  it('explains an unsupported intent', () => {
    expect(reasonLabel('intent_unsupported')).toBe('הכוונה אינה נתמכת בטיפול אוטומטי (רק הכנה לתור)')
  })

  it('says so when there are no rows', () => {
    render(<AuditTimeline rows={[]} />)
    expect(screen.getByText('אין רשומות ביומן הביקורת לפנייה הזו.')).toBeInTheDocument()
  })
})
