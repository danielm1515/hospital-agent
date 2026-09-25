import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import type { MessageTemplate } from '../../api/types'
import { PatientRequest } from './PatientRequest'
import { toIsoWithOffset } from './labels'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  requestFromPatient: vi.fn(),
}))

const TEMPLATES: MessageTemplate[] = [
  { template_id: 'clarify_general', purpose: 'question', text: 'לא הצלחנו להבין', param: null, options: {} },
  {
    template_id: 'clarify_did_you_mean',
    purpose: 'question',
    text: 'האם התכוונת ל{topic}?',
    param: 'topic',
    options: { preparation: 'הוראות ההכנה לתור' },
  },
  {
    template_id: 'document_request',
    purpose: 'document',
    text: 'נא להעלות את המסמך: {document}.',
    param: 'document',
    options: { URINALYSIS: 'בדיקת שתן' },
  },
  { template_id: 'close_handled', purpose: 'closing', text: 'טופלה', param: null, options: {} },
]

const DEADLINE_LABEL = 'עד מתי (ברירת מחדל: 24 שעות)'

function renderPanel(role: 'clinical_staff' | 'admin_staff' = 'admin_staff') {
  const onSent = vi.fn()
  render(
    <PatientRequest
      caseId="C-1"
      shownContextRef="ref-1"
      role={role}
      templates={TEMPLATES}
      onSent={onSent}
      onContextChanged={vi.fn()}
    />,
  )
  return { onSent }
}

describe('PatientRequest', () => {
  it('sends a question template with its parameter', async () => {
    vi.mocked(api.requestFromPatient).mockResolvedValue({ case_id: 'C-1', state: 'AwaitingPatientReply' })
    const { onSent } = renderPanel()
    await userEvent.selectOptions(screen.getByLabelText('הודעה'), 'clarify_did_you_mean')
    await userEvent.selectOptions(screen.getByLabelText('נושא'), 'preparation')
    await userEvent.type(screen.getByLabelText('סיבה (פנימית)'), 'unclear')
    await userEvent.click(screen.getByRole('button', { name: 'שליחה למטופל' }))
    expect(api.requestFromPatient).toHaveBeenCalledWith(
      'C-1',
      expect.objectContaining({
        kind: 'question',
        template_id: 'clarify_did_you_mean',
        param: 'preparation',
        reason: 'unclear',
        shown_context_ref: 'ref-1',
      }),
    )
    expect(onSent).toHaveBeenCalled()
  })

  it('offers free text to clinical staff only', () => {
    renderPanel('admin_staff')
    expect(screen.queryByRole('option', { name: 'טקסט חופשי (צוות קליני)' })).not.toBeInTheDocument()
  })

  it('lets clinical staff write free text', async () => {
    vi.mocked(api.requestFromPatient).mockResolvedValue({ case_id: 'C-1', state: 'AwaitingPatientReply' })
    renderPanel('clinical_staff')
    await userEvent.selectOptions(screen.getByLabelText('הודעה'), 'text')
    await userEvent.type(screen.getByLabelText('הטקסט למטופל'), 'נא לפרט')
    await userEvent.type(screen.getByLabelText('סיבה (פנימית)'), 'r')
    await userEvent.click(screen.getByRole('button', { name: 'שליחה למטופל' }))
    expect(api.requestFromPatient).toHaveBeenCalledWith('C-1', expect.objectContaining({ text: 'נא לפרט' }))
  })

  it('asks for a catalog document, with no template_id and no param', async () => {
    vi.mocked(api.requestFromPatient).mockResolvedValue({ case_id: 'C-1', state: 'AwaitingPatientReply' })
    renderPanel()
    await userEvent.click(screen.getByRole('radio', { name: 'בקשת מסמך' }))
    await userEvent.selectOptions(screen.getByLabelText('סוג המסמך'), 'URINALYSIS')
    await userEvent.type(screen.getByLabelText('סיבה (פנימית)'), 'r')
    await userEvent.click(screen.getByRole('button', { name: 'שליחה למטופל' }))
    expect(api.requestFromPatient).toHaveBeenCalledWith(
      'C-1',
      expect.objectContaining({ kind: 'document', document_type: 'URINALYSIS' }),
    )
    const [, body] = vi.mocked(api.requestFromPatient).mock.calls[0]
    expect(body).not.toHaveProperty('template_id')
    expect(body).not.toHaveProperty('param')
  })

  it('shows the refusal code with its Hebrew label', async () => {
    vi.mocked(api.requestFromPatient).mockRejectedValue(new api.ApiError(409, 'invalid_deadline'))
    renderPanel()
    await userEvent.selectOptions(screen.getByLabelText('הודעה'), 'clarify_general')
    await userEvent.type(screen.getByLabelText('סיבה (פנימית)'), 'r')
    await userEvent.click(screen.getByRole('button', { name: 'שליחה למטופל' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('invalid_deadline')
    expect(alert).toHaveTextContent('מועד היעד אינו תקין')
  })

  it('sends the deadline with a timezone offset when one is set', async () => {
    vi.mocked(api.requestFromPatient).mockResolvedValue({ case_id: 'C-1', state: 'AwaitingPatientReply' })
    renderPanel()
    fireEvent.change(screen.getByLabelText(DEADLINE_LABEL), { target: { value: '2026-10-01T10:00' } })
    await userEvent.selectOptions(screen.getByLabelText('הודעה'), 'clarify_general')
    await userEvent.type(screen.getByLabelText('סיבה (פנימית)'), 'r')
    await userEvent.click(screen.getByRole('button', { name: 'שליחה למטופל' }))
    const [, body] = vi.mocked(api.requestFromPatient).mock.calls[0]
    expect(body.deadline).toBe(toIsoWithOffset('2026-10-01T10:00'))
  })

  it('omits the deadline when the field is emptied, so the server default applies', async () => {
    vi.mocked(api.requestFromPatient).mockResolvedValue({ case_id: 'C-1', state: 'AwaitingPatientReply' })
    renderPanel()
    fireEvent.change(screen.getByLabelText(DEADLINE_LABEL), { target: { value: '' } })
    await userEvent.selectOptions(screen.getByLabelText('הודעה'), 'clarify_general')
    await userEvent.type(screen.getByLabelText('סיבה (פנימית)'), 'r')
    await userEvent.click(screen.getByRole('button', { name: 'שליחה למטופל' }))
    const [, body] = vi.mocked(api.requestFromPatient).mock.calls[0]
    expect(body.deadline).toBeUndefined()
  })
})
