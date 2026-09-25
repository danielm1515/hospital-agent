import { describe, expect, it } from 'vitest'
import { ApiError } from '../../api/client'
import {
  describeDocumentType,
  documentLabel,
  elapsedBetween,
  errorMessage,
  formatClock,
  formatDate,
  formatDateTime,
  isMoving,
  sameDay,
  truncate,
  uploadResultMessage,
} from './helpers'

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

  it('names the sub-project 11 catalog types (design §2)', () => {
    expect(documentLabel('CBC')).toBe('ספירת דם מלאה')
    expect(documentLabel('COAGULATION_TESTS')).toBe('בדיקות קרישה')
    expect(documentLabel('ECG')).toBe('תרשים פעילות חשמלית של הלב')
    expect(documentLabel('URINALYSIS')).toBe('בדיקת שתן')
    expect(documentLabel('PREOP_SUMMARY')).toBe('סיכום טרום ניתוח')
  })

  it('isolates an unrecognised catalog code (fail closed, §14) instead of leaving it to pick its own direction', () => {
    // A code outside the closed `DocumentType` union can still arrive over the wire; it is
    // wrapped in FSI/PDI (U+2068/U+2069) so it reads left-to-right inside the Hebrew
    // sentence it lands in, instead of jumping to the wrong edge.
    expect(describeDocumentType('MYSTERY_TYPE' as never)).toBe('⁨MYSTERY_TYPE⁩')
  })

  it('the expired outcome has no double space when document_type is null (the common case)', () => {
    const { text } = uploadResultMessage({ code: 'expired', document_type: null })
    expect(text).toBe('המסמך ישן מדי לפי כללי התוקף. יש להעלות מסמך עדכני.')
    expect(text).not.toMatch(/ {2}/)
  })

  it('turns an error into one Hebrew sentence, never a code', () => {
    expect(errorMessage(new ApiError(404, 'case_not_found'))).toBe('הפנייה לא נמצאה.')
    expect(errorMessage(new ApiError(0, 'network_error'))).toMatch(/אין חיבור לשרת/)
    expect(errorMessage(new ApiError(422, 'validation_error'))).toBe('הפרטים שהוזנו אינם תקינים.')
    expect(errorMessage(new ApiError(409, 'medical_answer_attempt'))).not.toMatch(/medical/)
    expect(errorMessage(new Error('boom'))).not.toMatch(/boom/)
  })

  it('gives Hebrew for the sub-project 13 upload errors, ahead of the generic status fallback', () => {
    // A 404 here is not the generic "case not found" - the specific detail wins.
    expect(errorMessage(new ApiError(404, 'file_upload_not_enabled'))).toMatch(/העלאת קובץ אינה זמינה/)
    expect(errorMessage(new ApiError(409, 'not_waiting_for_document'))).toMatch(/אינה ממתינה למסמך/)
    expect(errorMessage(new ApiError(413, 'too_large'))).toMatch(/גדול מדי/)
    expect(errorMessage(new ApiError(503, 'document_service_unavailable'))).toMatch(/שירות המסמכים אינו זמין/)
  })

  it('gives Hebrew for the sub-project 15 reply errors, never the raw code', () => {
    expect(errorMessage(new ApiError(409, 'not_waiting_for_reply'))).toMatch(/אינה ממתינה לתשובה/)
    expect(errorMessage(new ApiError(409, 'reply_kind_mismatch'))).toMatch(/לא ניתן להשיב בדרך זו/)
    expect(errorMessage(new ApiError(409, 'reply_not_accepted'))).toMatch(/לא הצלחנו לקלוט את התשובה/)
    expect(errorMessage(new ApiError(422, 'reply_too_long'))).toMatch(/ארוכה מדי/)
    // An unknown code still falls back to the generic sentence, never the code itself.
    expect(errorMessage(new ApiError(409, 'some_future_code'))).not.toMatch(/some_future_code/)
  })

  it('prints the time to the second, so two steps in the same minute differ', () => {
    expect(formatClock('2026-09-19T22:12:47Z')).toMatch(/\d{2}:\d{2}:\d{2}/)
    expect(formatClock('2026-09-19T22:12:47Z')).not.toBe(formatClock('2026-09-19T22:12:49Z'))
    expect(formatClock('not-a-date')).toBe('not-a-date')
    expect(formatDate('2026-09-19T22:12:47Z')).toMatch(/2026/)
    expect(formatDate('not-a-date')).toBe('not-a-date')
  })

  it('knows when two steps fall on the same day, in the reader’s own time zone', () => {
    // Local-time literals (no `Z`): the patient reads the timeline in local time, so
    // "the same day" must be the same local day whatever the machine's time zone is.
    expect(sameDay('2026-09-19T01:00:00', '2026-09-19T23:00:00')).toBe(true)
    expect(sameDay('2026-09-19T23:00:00', '2026-09-21T01:00:00')).toBe(false)
    expect(sameDay('not-a-date', '2026-09-19T23:00:00')).toBe(false)
  })

  it('says in Hebrew how long a step took, and says nothing about the same second', () => {
    const gap = (from: string, to: string) => elapsedBetween(from, to)
    expect(gap('2026-09-19T22:12:47Z', '2026-09-19T22:12:47.400Z')).toBeNull()
    expect(gap('2026-09-19T22:12:47Z', '2026-09-19T22:12:48Z')).toBe('כעבור שנייה')
    expect(gap('2026-09-19T22:12:47Z', '2026-09-19T22:12:49Z')).toBe('כעבור שתי שניות')
    expect(gap('2026-09-19T22:12:47Z', '2026-09-19T22:12:51Z')).toBe('כעבור 4 שניות')
    expect(gap('2026-09-19T22:12:47Z', '2026-09-19T22:20:47Z')).toBe('כעבור 8 דקות')
    expect(gap('2026-09-19T22:12:47Z', '2026-09-20T00:12:47Z')).toBe('כעבור שעתיים')
    expect(gap('2026-09-19T22:12:47Z', '2026-09-22T22:12:47Z')).toBe('כעבור 3 ימים')
    expect(gap('not-a-date', '2026-09-19T22:12:47Z')).toBeNull()
  })
})
