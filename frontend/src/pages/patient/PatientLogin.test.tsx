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
  it('reproduces the design brand panel', () => {
    const { container } = renderLogin()
    expect(screen.getByText('PATIENT SERVICES')).toBeInTheDocument()
    expect(container.querySelector('.brand-pane .pattern')).toBeInTheDocument()
    expect(screen.getByText(/בלי להמתין על הקו/)).toBeInTheDocument()
    expect(
      screen.getByText(
        'הסוכן הדיגיטלי אוסף את הפרטים, מאתר את המסמכים בתיק שלכם ומעביר לרופא רק את מה שדורש החלטה רפואית.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('זמין בכל שעה, כל ימות השבוע')).toBeInTheDocument()
    expect(screen.getByText('כל פנייה רפואית עוברת אישור אדם')).toBeInTheDocument()
    expect(screen.getByText('הפרטים נשמרים בתיק הרפואי בלבד')).toBeInTheDocument()
    expect(container.querySelector('.step .steps')).toBeInTheDocument()
  })

  it('fills the id from a demo button, signs in and lands in the patient area', async () => {
    const user = userEvent.setup()
    const { login } = renderLogin()

    await user.click(screen.getByRole('button', { name: 'P-10041' }))
    expect(screen.getByLabelText('מזהה מטופל')).toHaveValue('P-10041')

    await user.type(screen.getByLabelText('סיסמה'), 'demo')
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
