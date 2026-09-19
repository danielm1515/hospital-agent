import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import { renderWithAuth, STAFF_USER } from '../../test/helpers'
import { StaffLogin } from './StaffLogin'

describe('StaffLogin', () => {
  it('reproduces the design: badge, heading, scope list and audit line', () => {
    renderWithAuth(<StaffLogin />)

    expect(screen.getByText('STAFF ACCESS')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'כניסת צוות רפואי' })).toBeInTheDocument()
    expect(screen.getByText('מה מותר מהמסך הזה')).toBeInTheDocument()
    expect(screen.getByText(/אישור, דחייה וסגירה של פניות שהוסלמו/)).toBeInTheDocument()
    expect(screen.getByText(/עריכת תיק רפואי או מסמכים/)).toBeInTheDocument()
    expect(
      screen.getByText('כל כניסה, אישור ודחייה נרשמים ביומן הביקורת עם מזהה המשתמש וחותמת זמן.'),
    ).toBeInTheDocument()
  })

  it('fills the id and the demo password from a demo-user button', async () => {
    renderWithAuth(<StaffLogin />)

    await userEvent.click(screen.getByRole('button', { name: /coordinator_nurse/ }))

    expect(screen.getByLabelText('מזהה משתמש')).toHaveValue('coordinator_nurse')
    expect(screen.getByLabelText('סיסמה')).toHaveValue('demo')
  })

  it('offers both staff demo users', () => {
    renderWithAuth(<StaffLogin />)

    expect(screen.getByRole('button', { name: /coordinator_nurse/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /admin_coordinator/ })).toBeInTheDocument()
  })

  it('signs in with the id and password from the form', async () => {
    const login = vi.fn(async () => STAFF_USER)
    renderWithAuth(<StaffLogin />, { login })

    await userEvent.type(screen.getByLabelText('מזהה משתמש'), ' admin_coordinator ')
    await userEvent.type(screen.getByLabelText('סיסמה'), 'demo')
    await userEvent.click(screen.getByRole('button', { name: 'כניסה למערכת' }))

    expect(login).toHaveBeenCalledWith('admin_coordinator', 'demo')
  })

  it('does not call the API when a field is empty', async () => {
    const login = vi.fn(async () => STAFF_USER)
    renderWithAuth(<StaffLogin />, { login })

    await userEvent.click(screen.getByRole('button', { name: 'כניסה למערכת' }))

    expect(login).not.toHaveBeenCalled()
    expect(await screen.findByRole('alert')).toHaveTextContent('יש להזין מזהה משתמש וסיסמה.')
  })

  it('reports wrong credentials', async () => {
    const login = vi.fn(async () => {
      throw new ApiError(401, 'invalid_credentials')
    })
    renderWithAuth(<StaffLogin />, { login })

    await userEvent.type(screen.getByLabelText('מזהה משתמש'), 'coordinator_nurse')
    await userEvent.type(screen.getByLabelText('סיסמה'), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: 'כניסה למערכת' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('מזהה או סיסמה שגויים.')
  })
})
