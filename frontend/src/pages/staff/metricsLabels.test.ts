import { describe, expect, it } from 'vitest'
import {
  OUTCOME_LABELS,
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
