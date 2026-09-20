import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import { ClinicalAnswer } from './ClinicalAnswer'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  answer: vi.fn(),
}))

const onAnswered = vi.fn()

function renderAnswer(role: 'clinical_staff' | 'admin_staff' = 'clinical_staff') {
  return render(
    <ClinicalAnswer caseId="CASE-1" shownContextRef="ctx-1" role={role} onAnswered={onAnswered} />,
  )
}

beforeEach(() => {
  vi.mocked(api.answer).mockReset()
  vi.mocked(api.answer).mockResolvedValue({ case_id: 'CASE-1', state: 'Completed' })
  onAnswered.mockReset()
})

describe('ClinicalAnswer', () => {
  it('sends the exact text with the shown context ref', async () => {
    renderAnswer()

    await userEvent.type(screen.getByLabelText(/התשובה למטופל/), 'אין להפסיק את הטיפול.')
    await userEvent.type(screen.getByLabelText(/סיבה/), 'נענתה טלפונית')
    await userEvent.click(screen.getByRole('button', { name: 'אישור ושליחת התשובה' }))

    expect(api.answer).toHaveBeenCalledWith('CASE-1', {
      answer: 'אין להפסיק את הטיפול.',
      reason: 'נענתה טלפונית',
      shown_context_ref: 'ctx-1',
    })
    expect(onAnswered).toHaveBeenCalled()
  })

  it('says the text itself is what gets approved', () => {
    renderAnswer()
    expect(screen.getByText(/הטקסט הזה בדיוק הוא מה שיאושר/)).toBeInTheDocument()
  })

  it('locks the block for a role that may not approve content, and explains why', () => {
    renderAnswer('admin_staff')
    expect(screen.getByText(/צוות קליני בלבד/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'אישור ושליחת התשובה' })).not.toBeInTheDocument()
  })

  it('sends nothing when the answer or the reason is empty', async () => {
    renderAnswer()
    await userEvent.click(screen.getByRole('button', { name: 'אישור ושליחת התשובה' }))
    expect(api.answer).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('יש לכתוב תשובה וסיבה.')
  })

  it('shows a Hebrew message for a context that changed, and keeps the text', async () => {
    vi.mocked(api.answer).mockRejectedValue(new ApiError(409, 'context_changed'))
    renderAnswer()

    await userEvent.type(screen.getByLabelText(/התשובה למטופל/), 'תשובה')
    await userEvent.type(screen.getByLabelText(/סיבה/), 'סיבה')
    await userEvent.click(screen.getByRole('button', { name: 'אישור ושליחת התשובה' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/ההקשר השתנה/)
    expect(screen.getByLabelText(/התשובה למטופל/)).toHaveValue('תשובה')
    expect(onAnswered).not.toHaveBeenCalled()
  })
})
