import { describe, expect, it } from 'vitest'
import {
  LLM_CALL_LABELS,
  OUTCOME_LABELS,
  costText,
  entryCostText,
  formatUsd,
  durationsText,
  failureRow,
  formatPercent,
  formatSeconds,
  fromLocalInput,
  labelOf,
  presetRange,
  toLocalInput,
  toRows,
} from './metricsLabels'

describe('labelOf', () => {
  it('translates a known code and falls back to the code itself', () => {
    expect(labelOf(OUTCOME_LABELS, 'MedicalQuestion')).toBe('שאלה רפואית')
    expect(labelOf(OUTCOME_LABELS, 'SomethingNew')).toBe('SomethingNew')
    expect(labelOf(OUTCOME_LABELS, null)).toBe('—')
  })
})

describe('formatting', () => {
  it('writes seconds in the largest readable unit', () => {
    expect(formatSeconds(null)).toBe('—')
    expect(formatSeconds(0.0123)).toBe('12 ms')
    expect(formatSeconds(2.64)).toBe('2.6 s')
    expect(formatSeconds(90)).toBe('1.5 min')
    expect(formatSeconds(7200)).toBe('2.0 h')
  })

  it('writes a ratio as a whole percent', () => {
    expect(formatPercent(null)).toBe('—')
    expect(formatPercent(2 / 3)).toBe('67%')
  })

  it('summarises durations, or says there were none', () => {
    expect(durationsText({ count: 0, p50: null, p95: null, max: null })).toBe('אין מדידות')
    expect(durationsText({ count: 3, p50: 4, p95: 5.8, max: 6 })).toBe('p50 4.0 s · p95 5.8 s · max 6.0 s')
  })
})

describe('toRows', () => {
  it('orders the largest first, ties by code, with each row labelled', () => {
    const rows = toRows({ Unsupported: 2, MedicalQuestion: 5, AppointmentPreparation: 2 }, (code) =>
      labelOf(OUTCOME_LABELS, code),
    )
    expect(rows).toEqual([
      { code: 'MedicalQuestion', label: 'שאלה רפואית', count: 5 },
      { code: 'AppointmentPreparation', label: 'הכנה לתור', count: 2 },
      { code: 'Unsupported', label: 'לא נתמכת', count: 2 },
    ])
  })
})

describe('failureRow', () => {
  it('labels a known ExecutionFailed reason in Hebrew, the code once in .mono', () => {
    expect(failureRow({ outcome: 'ExecutionFailed', reason: 'tool:transient_failure:timeout', count: 3 })).toEqual({
      key: 'ExecutionFailed|tool:transient_failure:timeout',
      code: 'tool:transient_failure:timeout',
      label: 'פסק זמן',
      count: 3,
    })
  })

  it('falls an unknown ExecutionFailed reason back to the code as its own label', () => {
    expect(failureRow({ outcome: 'ExecutionFailed', reason: 'something_new', count: 1 })).toEqual({
      key: 'ExecutionFailed|something_new',
      code: 'something_new',
      label: 'something_new',
      count: 1,
    })
  })

  it('marks an ExecutionUnknown reason as undetermined in Hebrew, never repeating the code as a label', () => {
    expect(failureRow({ outcome: 'ExecutionUnknown', reason: 'restart', count: 1 })).toEqual({
      key: 'ExecutionUnknown|restart',
      code: 'restart',
      label: 'הפעלה מחדש באמצע קריאה (תוצאה לא ידועה)',
      count: 1,
    })
    expect(failureRow({ outcome: 'ExecutionUnknown', reason: 'exception:ValueError', count: 1 })).toEqual({
      key: 'ExecutionUnknown|exception:ValueError',
      code: 'exception:ValueError',
      label: 'תוצאה לא ידועה',
      count: 1,
    })
  })

  it('falls a reasonless ExecutionUnknown back to the outcome as its code', () => {
    expect(failureRow({ outcome: 'ExecutionUnknown', reason: null, count: 2 })).toEqual({
      key: 'ExecutionUnknown|',
      code: 'ExecutionUnknown',
      label: 'תוצאה לא ידועה',
      count: 2,
    })
  })
})

describe('ranges', () => {
  it('round-trips a Date through a datetime-local value, to the minute', () => {
    const date = new Date(2026, 8, 24, 13, 5, 42)
    expect(toLocalInput(date)).toBe('2026-09-24T13:05')
    expect(fromLocalInput('2026-09-24T13:05')?.getTime()).toBe(new Date(2026, 8, 24, 13, 5).getTime())
    expect(fromLocalInput('')).toBeNull()
    expect(fromLocalInput('not a date')).toBeNull()
  })

  it('ends a preset at the next minute, so now is inside it', () => {
    const range = presetRange(24 * 7, new Date(2026, 8, 24, 13, 5, 42))
    expect(range).toEqual({ from: '2026-09-17T13:06', to: '2026-09-24T13:06' })
    const start = fromLocalInput(range.from)!
    const end = fromLocalInput(range.to)!
    expect(end.getTime() - start.getTime()).toBe(7 * 24 * 3600_000)
  })
})

describe('formatUsd (sub-project 19, docs/api.md §10)', () => {
  it('writes an 8-place money string as dollars with 4 decimals', () => {
    expect(formatUsd('0.00213400')).toBe('$0.0021')
    expect(formatUsd('0.00130360')).toBe('$0.0013')
    expect(formatUsd('12.34560000')).toBe('$12.3456')
    expect(formatUsd('1234.50000000')).toBe('$1,234.5000')
  })

  it('rounds half-up on the digits themselves, never through a float', () => {
    expect(formatUsd('0.00014999')).toBe('$0.0001')
    expect(formatUsd('0.99995000')).toBe('$1.0000')
    expect(formatUsd('9.99999999')).toBe('$10.0000')
    // As doubles these are 0.000149999… and 2.000249999…, so Number(value).toFixed(4) would
    // round them down to 0.0001 and 2.0002; the digits say half-up.
    expect(formatUsd('0.00015000')).toBe('$0.0002')
    expect(formatUsd('2.00025000')).toBe('$2.0003')
  })

  it('never shows a tiny non-zero cost as free, and shows a real zero as one', () => {
    expect(formatUsd('0.00000001')).toBe('<$0.0001')
    expect(formatUsd('0.00009999')).toBe('<$0.0001')
    expect(formatUsd('0.00010000')).toBe('$0.0001')
    expect(formatUsd('0.00000000')).toBe('$0.0000')
  })

  it('tells "nothing yet" ("—") apart from "unknown price" by what the caller knows', () => {
    expect(formatUsd(null)).toBe('—')
    expect(formatUsd(null, { unpriced: true })).toBe('מחיר לא ידוע')
    // The flag only explains a null; a known cost is shown as it is.
    expect(formatUsd('0.00100000', { unpriced: true })).toBe('$0.0010')
    expect(costText(null, 0)).toBe('—')
    expect(costText(null, 2)).toBe('מחיר לא ידוע')
    expect(costText('0.00000000', 1)).toBe('$0.0000')
    expect(entryCostText({ calls: 1, cost_usd: null })).toBe('מחיר לא ידוע')
    expect(entryCostText({ calls: 1, cost_usd: '0.00040800' })).toBe('$0.0004')
  })

  it('shows a string that is not a plain decimal as it is, rather than guessing', () => {
    expect(formatUsd('abc')).toBe('abc')
    expect(formatUsd('-0.00100000')).toBe('-0.00100000')
  })
})

describe('LLM_CALL_LABELS', () => {
  it('labels the six call codes and leaves an unknown one to itself', () => {
    expect(labelOf(LLM_CALL_LABELS, 'Intent')).toBe('סיווג כוונה')
    expect(labelOf(LLM_CALL_LABELS, 'Safety')).toBe('סיווג סיכון')
    expect(labelOf(LLM_CALL_LABELS, 'Planner')).toBe('תכנון')
    expect(labelOf(LLM_CALL_LABELS, 'Evaluator')).toBe('בודק תגובה')
    expect(labelOf(LLM_CALL_LABELS, 'DocumentClassify')).toBe('סיווג מסמך')
    expect(labelOf(LLM_CALL_LABELS, 'DocumentVision')).toBe('קריאת מסמך סרוק')
    expect(labelOf(LLM_CALL_LABELS, 'SomethingNew')).toBe('SomethingNew')
  })
})
