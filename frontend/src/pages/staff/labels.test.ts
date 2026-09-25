import { describe, expect, it } from 'vitest'
import {
  DATA_KIND_LABELS,
  REQUEST_ERROR_LABELS,
  RETURNED_BY_LABELS,
  STATE_LABELS,
  dataKindLabel,
  requestErrorLabel,
  returnedByLabel,
} from './labels'

describe('sub-project 15 labels', () => {
  it('labels the new State beside the code', () => {
    expect(STATE_LABELS.AwaitingPatientReply).toBe('ממתינה לתשובת המטופל')
  })

  it('labels the new Data Log kinds', () => {
    expect(DATA_KIND_LABELS.staff_message).toBe('הודעת צוות למטופל')
    expect(DATA_KIND_LABELS.patient_reply).toBe('תשובת המטופל')
    expect(dataKindLabel('staff_message')).toBe('הודעת צוות למטופל')
  })

  it('labels returned_by, and falls back to the code or a dash', () => {
    expect(RETURNED_BY_LABELS.patient_reply).toBe('התקבלה תשובת מטופל')
    expect(RETURNED_BY_LABELS.reply_timeout).toBe('לא נענתה בזמן')
    expect(returnedByLabel('patient_reply')).toBe('התקבלה תשובת מטופל')
    expect(returnedByLabel('reply_timeout')).toBe('לא נענתה בזמן')
    expect(returnedByLabel('something_new')).toBe('something_new')
    expect(returnedByLabel(null)).toBe('—')
    expect(returnedByLabel(undefined)).toBe('—')
  })

  it('labels the request/decision-message error codes from api.md §5/§8, and falls back to the code', () => {
    for (const code of [
      'invalid_request',
      'invalid_template',
      'unexpected_param',
      'invalid_param',
      'message_required',
      'document_service_not_configured',
      'invalid_deadline',
      'appointment_passed',
      'message_not_allowed',
      'human_engaged',
      'clinical_staff_only',
      'context_changed',
      'not_in_review',
      'reason_required',
    ]) {
      expect(REQUEST_ERROR_LABELS[code]).toBeTypeOf('string')
      expect(requestErrorLabel(code)).toBe(REQUEST_ERROR_LABELS[code])
    }
    expect(requestErrorLabel('some_future_code')).toBe('some_future_code')
  })
})
