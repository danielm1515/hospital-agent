import { describe, expect, it } from 'vitest'
import type { Appointment } from '../../api/types'
import {
  appointmentOptionLabel,
  datesInText,
  israelDayMonth,
  suggestOtherAppointment,
  upcomingScheduled,
} from './appointmentChoice'

function appointment(overrides: Partial<Appointment> = {}): Appointment {
  return {
    appointment_id: 'APT-1',
    appointment_at: '2026-10-03T07:30:00Z',
    department: 'Neurology',
    doctor_name: null,
    location: null,
    status: 'Scheduled',
    required_documents: [],
    exam_type: { code: 'NEURO_EEG', label: 'EEG' },
    instruction: { source_id: 'INSTR-NEURO-EEG', version: '1', title: 'לפני EEG' },
    ...overrides,
  }
}

const neuro = appointment()
const stress = appointment({
  appointment_id: 'APT-2',
  appointment_at: '2026-10-07T06:00:00Z',
  department: 'Cardiology',
  exam_type: { code: 'CARD_STRESS', label: 'מבחן מאמץ' },
})
const echo = appointment({
  appointment_id: 'APT-3',
  appointment_at: '2026-10-12T21:30:00Z', // 13/10 in Israel, 12/10 in UTC
  department: 'Cardiology',
  exam_type: { code: 'CARD_ECHO', label: 'אקו לב' },
})

describe('israelDayMonth', () => {
  it('reads the calendar day in Israel time, not UTC or the browser zone', () => {
    expect(israelDayMonth('2026-10-12T21:30:00Z')).toEqual({ day: 13, month: 10 })
    expect(israelDayMonth('not a date')).toBeNull()
  })
})

describe('datesInText', () => {
  it('finds d/m and d.m dates, with or without a year', () => {
    expect(datesInText('התור ב-7/10 או ב 13.10.2026, לא 99/99')).toEqual([
      { day: 7, month: 10 },
      { day: 13, month: 10 },
    ])
  })

  it('ignores digits that are part of a longer number', () => {
    expect(datesInText('טלפון 0521/234')).toEqual([])
  })
})

describe('upcomingScheduled', () => {
  it('keeps only Scheduled appointments, earliest first', () => {
    const cancelled = appointment({ appointment_id: 'APT-9', status: 'Cancelled' })
    expect(upcomingScheduled([echo, cancelled, neuro]).map((a) => a.appointment_id)).toEqual(['APT-1', 'APT-3'])
  })
})

describe('appointmentOptionLabel', () => {
  it('names the exam, the department in Hebrew and the Israel date - never a code', () => {
    const label = appointmentOptionLabel(stress)
    expect(label).toContain('מבחן מאמץ')
    expect(label).toContain('קרדיולוגיה')
    expect(label).toContain('7.10.2026')
    expect(label).toContain('09:00')
    expect(label).not.toMatch(/APT-|CARD_|INSTR|Cardiology/)
  })

  it('falls back to the department alone without an exam type', () => {
    expect(appointmentOptionLabel(appointment({ exam_type: undefined }))).toMatch(/^נוירולוגיה · /)
  })
})

describe('suggestOtherAppointment', () => {
  it('suggests the other appointment whose date the text names', () => {
    expect(suggestOtherAppointment('מה ההכנה לתור ב-7/10?', neuro, [stress, echo])).toBe(stress)
    expect(suggestOtherAppointment('מה ההכנה לתור ב-13.10?', neuro, [stress, echo])).toBe(echo)
  })

  it('matches a department or an exam label, with a Hebrew prefix letter', () => {
    expect(suggestOtherAppointment('מה צריך לפני המבחן מאמץ', neuro, [stress])).toBe(stress)
    expect(suggestOtherAppointment('שאלה על התור בקרדיולוגיה', neuro, [stress])).toBe(stress)
    expect(suggestOtherAppointment('my cardiology visit', neuro, [stress])).toBe(stress)
  })

  it('does not match a label inside a longer word', () => {
    const skin = appointment({ appointment_id: 'APT-4', department: 'Dermatology', exam_type: null })
    expect(suggestOtherAppointment('שאלה לעורך הדין', neuro, [skin])).toBeNull()
    expect(suggestOtherAppointment('שאלה על בדיקת העור', neuro, [skin])).toBe(skin)
  })

  it('says nothing when the text names the chosen appointment too', () => {
    expect(suggestOtherAppointment('EEG ב-3/10 ומבחן מאמץ ב-7/10', neuro, [stress])).toBeNull()
  })

  it('ignores what the chosen and the other appointment share', () => {
    // Both are Cardiology: the department alone names neither of them.
    expect(suggestOtherAppointment('שאלה על קרדיולוגיה', stress, [echo])).toBeNull()
    expect(suggestOtherAppointment('שאלה על אקו לב', stress, [echo])).toBe(echo)
  })

  it('says nothing for a text that names no appointment', () => {
    expect(suggestOtherAppointment('מתי התור שלי?', neuro, [stress, echo])).toBeNull()
  })

  it('prefers the other appointment the text names most', () => {
    expect(suggestOtherAppointment('אקו לב בקרדיולוגיה ב-13/10', neuro, [stress, echo])).toBe(echo)
  })
})
