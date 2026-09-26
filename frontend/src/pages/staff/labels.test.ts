import { describe, expect, it } from 'vitest'
import { STATES } from '../../api/types'
import {
  DATA_KIND_LABELS,
  REQUEST_ERROR_LABELS,
  RETURNED_BY_LABELS,
  STATE_GROUPS,
  STATE_GROUP_LABELS,
  STATE_GROUP_ORDER,
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

  it('partitions every State into exactly one Case Monitor group (staff-fixes design Task 4)', () => {
    expect(STATE_GROUP_ORDER).toHaveLength(5)
    expect(new Set(STATE_GROUP_ORDER)).toEqual(new Set(Object.keys(STATE_GROUPS)))

    const covering = new Map<string, string>()
    for (const group of STATE_GROUP_ORDER) {
      for (const state of STATE_GROUPS[group]) {
        expect(covering.has(state)).toBe(false) // a state belongs to only one group
        covering.set(state, group)
      }
    }
    for (const state of STATES) {
      expect(covering.has(state)).toBe(true) // every State (including the extension) is covered
    }
    expect(covering.size).toBe(STATES.length)
  })

  it('gives every group a Hebrew label', () => {
    for (const group of STATE_GROUP_ORDER) {
      expect(STATE_GROUP_LABELS[group]).toBeTypeOf('string')
      expect(STATE_GROUP_LABELS[group].length).toBeGreaterThan(0)
    }
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
