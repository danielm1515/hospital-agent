import { describe, expect, it } from 'vitest'
import { ApiError } from '../../api/client'
import { documentLabel, errorMessage, formatDateTime, isMoving, truncate } from './helpers'

describe('patient helpers', () => {
  it('polls only while the agent is still advancing the case', () => {
    expect(isMoving('received')).toBe(true)
    expect(isMoving('in_progress')).toBe(true)
    for (const status of ['needs_document', 'in_review', 'completed', 'closed'] as const) {
      expect(isMoving(status)).toBe(false)
    }
  })

  it('cuts the card text at 120 characters', () => {
    expect(truncate('קצר')).toBe('קצר')
    expect(truncate('א'.repeat(120))).toBe('א'.repeat(120))
    expect(truncate('א'.repeat(121))).toBe(`${'א'.repeat(120)}…`)
  })

  it('formats a timestamp and leaves an unparsable one alone', () => {
    expect(formatDateTime('2026-09-19T22:12:47.693947Z')).toMatch(/2026/)
    expect(formatDateTime('not-a-date')).toBe('not-a-date')
  })

  it('names the documents it knows and nothing else', () => {
    expect(documentLabel('blood_test')).toBe('בדיקת דם')
    expect(documentLabel('mystery_doc')).toBeNull()
  })

  it('turns an error into one Hebrew sentence, never a code', () => {
    expect(errorMessage(new ApiError(404, 'case_not_found'))).toBe('הפנייה לא נמצאה.')
    expect(errorMessage(new ApiError(0, 'network_error'))).toMatch(/אין חיבור לשרת/)
    expect(errorMessage(new ApiError(422, 'validation_error'))).toBe('הפרטים שהוזנו אינם תקינים.')
    expect(errorMessage(new ApiError(409, 'medical_answer_attempt'))).not.toMatch(/medical/)
    expect(errorMessage(new Error('boom'))).not.toMatch(/boom/)
  })
})
