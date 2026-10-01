import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  ACTION_TARGETS,
  DATALOG_MINIMIZED,
  DATALOG_SENSITIVE,
  PROLOG_CHECKS,
  Z3_MODEL,
  datalogOf,
  prologChecksOf,
  z3ModelOf,
} from './engineRules'

// Paths are relative to the Vitest root, i.e. `frontend/`.
const backend = (path: string) => readFileSync(resolve(process.cwd(), '../backend/hospital_agent', path), 'utf-8')

const policyRow = (action: string, result: string, reasons: string[] = []) => ({
  event: 'POLICY_ALLOWED',
  action,
  policy_result: result,
  policy_reasons: reasons,
})

describe('engine rule lists mirror the backend sources', () => {
  it("Prolog: explain/4's reasons for an automatic action, in rules.pl order", () => {
    const explain = [...backend('policy/rules.pl').matchAll(/^explain\(.*reason\(([a-z_]+),/gm)].map((m) => m[1])
    // workflow_decision_missing is the human-workflow path; the agent's actions never reach it.
    const automatic = explain.filter((code) => code !== 'workflow_decision_missing')
    expect(PROLOG_CHECKS.map((check) => check.code)).toEqual(automatic)
  })

  it('Datalog: sensitive/1 and minimized/2 of flows.dl', () => {
    const flows = backend('policy/flows.dl')
    expect([...flows.matchAll(/^sensitive\(([a-z_]+)\)\./gm)].map((m) => m[1])).toEqual(DATALOG_SENSITIVE)
    const minimized: Record<string, string[]> = {}
    for (const [, field, target] of flows.matchAll(/^minimized\(([a-z_]+),\s*([a-z_]+)\)\./gm)) {
      ;(minimized[target] ??= []).push(field)
    }
    expect(minimized).toEqual(DATALOG_MINIMIZED)
  })

  it("Datalog: each action's target and fields, as execution/gateway.py's ACTION_TARGETS", () => {
    const gateway = backend('execution/gateway.py')
    const found: Record<string, { target: string; fields: string[] }> = {}
    for (const [, action, target, fields] of gateway.matchAll(
      /Action\.([A-Z_]+)\.value: \("([a-z_]+)", \(([^)]*)\)\)/g,
    )) {
      const name = action.toLowerCase().replace(/(^|_)([a-z])/g, (_, __, c: string) => c.toUpperCase())
      found[name] = { target, fields: [...fields.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]) }
    }
    expect(found).toEqual(ACTION_TARGETS)
  })

  it("Z3: every constraint is in readiness.py's §9.1 model", () => {
    const readiness = backend('policy/readiness.py')
    for (const constraint of Z3_MODEL) expect(readiness).toContain(constraint.code)
  })
})

describe('prologChecksOf', () => {
  it('passes every relevant check on an allowed step, and marks the ones that do not apply', () => {
    const checks = prologChecksOf(policyRow('CheckAppointment', 'Allow'))!
    expect(checks.summary).toBe('Prolog: 4/4 בדיקות עברו')
    expect(checks.items.map((item) => item.status)).toEqual(['pass', 'na', 'pass', 'na', 'pass', 'pass'])
  })

  it('on a block: the checks before the reported one held, the ones after were never reached', () => {
    const checks = prologChecksOf(
      policyRow('SendStatusUpdate', 'Deny', ['prolog:reason(action_not_current_step, send_status_update)']),
    )!
    expect(checks.failed).toBe(true)
    expect(checks.items.map((item) => item.status)).toEqual(['pass', 'pass', 'fail', 'skip', 'skip', 'skip'])
  })

  it('counts a Deny that only OPA gave as Prolog passing, and shows nothing when Prolog never ran', () => {
    expect(prologChecksOf(policyRow('CheckDocuments', 'Deny', ['attempts_exhausted']))!.failed).toBe(false)
    expect(prologChecksOf(policyRow('CheckDocuments', 'Deny', ['prolog:policy_engine_unavailable']))).toBeNull()
    expect(prologChecksOf({ ...policyRow('CheckDocuments', 'Allow'), policy_result: null })).toBeNull()
  })
})

describe('datalogOf', () => {
  it("shows the action's target, what it sends and what is blocked there", () => {
    const flow = datalogOf(policyRow('CheckAppointment', 'Allow'))!
    expect(flow.summary).toBe('Datalog: appointment_system · המזעור נשמר')
    const status = Object.fromEntries(flow.items.map((item) => [item.code, `${item.status}:${item.note}`]))
    expect(status.patient_id).toBe('pass:נשלח · מותר')
    expect(status.patient_text).toBe('info:חסום למערכת הזו')
    expect(status.document_id).toBe('info:חסום למערכת הזו')
  })

  it("fails the flow when OPA's field_not_minimized fired", () => {
    const flow = datalogOf(policyRow('CheckDocuments', 'Deny', ['field_not_minimized']))!
    expect(flow.failed).toBe(true)
    expect(flow.summary).toContain('מזעור נכשל')
  })
})

describe('z3ModelOf', () => {
  it('shows the model and UNSAT where the readiness check asked Z3', () => {
    const model = z3ModelOf({
      event: 'MISSING_INFORMATION_DETECTED',
      action: null,
      policy_result: null,
      policy_reasons: [],
      guards: { AskPatientSafe: true },
    })!
    expect(model.summary).toBe('Z3: 3 אילוצים · תוצאה UNSAT')
    expect(model.items.at(-1)?.status).toBe('pass')
  })

  it('fails on a counterexample, and is absent where Z3 did not run', () => {
    const base = { action: null, policy_result: null, guards: {} }
    expect(z3ModelOf({ ...base, event: 'HUMAN_REVIEW_REQUIRED', policy_reasons: ['z3:sat'] })!.failed).toBe(true)
    expect(z3ModelOf({ ...base, event: 'READINESS_PASSED', policy_reasons: [] })).toBeNull()
  })
})
