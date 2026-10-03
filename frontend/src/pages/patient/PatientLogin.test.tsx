import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import { PatientLogin } from './PatientLogin'
import { authValue, PATIENT_USER, TestAuthProvider } from '../../test/helpers'
import type { AuthValue } from '../../auth/AuthContext'

function renderLogin(login: AuthValue['login'] = vi.fn(async () => PATIENT_USER)) {
  const result = render(
    <MemoryRouter initialEntries={['/login']}>
      <TestAuthProvider value={authValue({ login })}>
        <Routes>
          <Route path="/login" element={<PatientLogin />} />
          <Route path="/patient" element={<h1>הפניות שלי</h1>} />
        </Routes>
      </TestAuthProvider>
    </MemoryRouter>,
  )
  return { ...result, login }
}

describe('PatientLogin', () => {
  it('keeps the brand panel as decoration only, with no marketing copy', () => {
    const { container } = renderLogin()
    const pane = container.querySelector('.brand-pane')
    expect(pane?.querySelector('.pattern')).toBeInTheDocument()
    expect(pane?.textContent?.trim()).toBe('')
    expect(screen.queryByText('PATIENT SERVICES')).not.toBeInTheDocument()
    expect(container.querySelector('.step .steps')).toBeInTheDocument()
  })

  it('a demo button fills both fields, so one click and submit signs in', async () => {
    const user = userEvent.setup()
    const { login } = renderLogin()

    await user.click(screen.getByRole('button', { name: 'P-10041' }))
    expect(screen.getByLabelText('מזהה מטופל')).toHaveValue('P-10041')
    expect(screen.getByLabelText('סיסמה')).toHaveValue('demo')

    await user.click(screen.getByRole('button', { name: 'כניסה למערכת' }))

    expect(login).toHaveBeenCalledWith('P-10041', 'demo')
    expect(await screen.findByRole('heading', { name: 'הפניות שלי' })).toBeInTheDocument()
  })

  it('shows an error alert for invalid_credentials and stays on the page', async () => {
    const user = userEvent.setup()
    const login = vi.fn(async () => {
      throw new ApiError(401, 'invalid_credentials')
    }) as unknown as AuthValue['login']
    renderLogin(login)

    await user.type(screen.getByLabelText('מזהה מטופל'), 'P-10041')
    await user.type(screen.getByLabelText('סיסמה'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'כניסה למערכת' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('מזהה מטופל או סיסמה שגויים')
    expect(screen.getByRole('heading', { name: 'כניסה לפניות מטופלים' })).toBeInTheDocument()
  })

  it('does not call the API when a field is empty', async () => {
    const user = userEvent.setup()
    const { login } = renderLogin()
    await user.click(screen.getByRole('button', { name: 'כניסה למערכת' }))
    expect(login).not.toHaveBeenCalled()
    expect(await screen.findByRole('alert')).toHaveTextContent('יש להזין מזהה מטופל וסיסמה.')
  })
})
