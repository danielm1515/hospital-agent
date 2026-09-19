import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import { NewRequest } from './NewRequest'
import { patientView } from './fixtures'
import { authValue, PATIENT_USER, TestAuthProvider } from '../../test/helpers'

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>()
  return { ...actual, createRequest: vi.fn() }
})

const createRequest = vi.mocked(api.createRequest)

function renderNew() {
  return render(
    <MemoryRouter initialEntries={['/patient/new']}>
      <TestAuthProvider value={authValue({ user: PATIENT_USER })}>
        <Routes>
          <Route path="/patient" element={<h1>הפניות שלי</h1>} />
          <Route path="/patient/new" element={<NewRequest />} />
          <Route path="/patient/requests/:caseId" element={<h1>פרטי פנייה</h1>} />
        </Routes>
      </TestAuthProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  createRequest.mockReset()
})

describe('NewRequest', () => {
  it('posts the trimmed text and goes to the new case', async () => {
    const user = userEvent.setup()
    createRequest.mockResolvedValue(patientView({ case_id: 'CASE-NEW' }))
    renderNew()

    await user.type(screen.getByLabelText('תוכן הפנייה'), '  מתי התור שלי?  ')
    await user.click(screen.getByRole('button', { name: 'שליחת הפנייה' }))

    expect(createRequest).toHaveBeenCalledWith('מתי התור שלי?')
    expect(await screen.findByRole('heading', { name: 'פרטי פנייה' })).toBeInTheDocument()
  })

  it('counts the characters, up to 2000', async () => {
    const user = userEvent.setup()
    renderNew()
    const field = screen.getByLabelText('תוכן הפנייה')
    expect(field).toHaveAttribute('maxlength', '2000')
    await user.type(field, 'שלום')
    expect(screen.getByText('4/2000')).toBeInTheDocument()
  })

  it('refuses an empty request without calling the API', async () => {
    const user = userEvent.setup()
    renderNew()
    await user.type(screen.getByLabelText('תוכן הפנייה'), '   ')
    await user.click(screen.getByRole('button', { name: 'שליחת הפנייה' }))

    expect(createRequest).not.toHaveBeenCalled()
    expect(screen.getByText('יש לכתוב את תוכן הפנייה.')).toBeInTheDocument()
    expect(screen.getByLabelText('תוכן הפנייה')).toHaveAttribute('aria-invalid', 'true')
  })

  it('keeps the patient on the form when the call fails', async () => {
    const user = userEvent.setup()
    createRequest.mockRejectedValue(new ApiError(422, 'validation_error'))
    renderNew()

    await user.type(screen.getByLabelText('תוכן הפנייה'), 'שאלה')
    await user.click(screen.getByRole('button', { name: 'שליחת הפנייה' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('הפרטים שהוזנו אינם תקינים.')
    expect(screen.getByRole('heading', { name: 'פנייה חדשה' })).toBeInTheDocument()
  })
})
