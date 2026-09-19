import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { App } from './App'
import { authValue, PATIENT_USER, STAFF_USER, TestAuthProvider } from './test/helpers'
import type { AuthOptions } from './test/helpers'

function renderApp(route: string, options: AuthOptions = {}) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <TestAuthProvider value={authValue(options)}>
        <App />
      </TestAuthProvider>
    </MemoryRouter>,
  )
}

describe('routing', () => {
  it('sends a signed-out visitor from / to the patient login', () => {
    renderApp('/')
    expect(screen.getByRole('heading', { name: 'כניסה לפניות מטופלים' })).toBeInTheDocument()
  })

  it('guards the patient area: no token means the patient login', () => {
    renderApp('/patient')
    expect(screen.getByRole('heading', { name: 'כניסה לפניות מטופלים' })).toBeInTheDocument()
  })

  it('guards the staff area: no token means the staff login', () => {
    renderApp('/staff')
    expect(screen.getByRole('heading', { name: 'כניסת צוות רפואי' })).toBeInTheDocument()
  })

  it('waits instead of redirecting while the session is being restored', () => {
    renderApp('/patient', { status: 'loading' })
    expect(screen.getByRole('status')).toHaveTextContent('טוען…')
    expect(screen.queryByRole('heading', { name: 'כניסה לפניות מטופלים' })).not.toBeInTheDocument()
  })

  it('sends a signed-in patient from / to the patient area', () => {
    renderApp('/', { user: PATIENT_USER })
    expect(screen.getByRole('heading', { name: 'הפניות שלי' })).toBeInTheDocument()
    expect(screen.getByText(PATIENT_USER.display_name)).toBeInTheDocument()
  })

  it('sends a signed-in staff user from / to the staff area', () => {
    renderApp('/', { user: STAFF_USER })
    expect(screen.getByRole('heading', { name: 'תור הסלמות' })).toBeInTheDocument()
  })

  it('keeps a patient out of the staff area', () => {
    renderApp('/staff/monitor', { user: PATIENT_USER })
    expect(screen.getByRole('heading', { name: 'הפניות שלי' })).toBeInTheDocument()
  })

  it('keeps a staff user out of the patient area', () => {
    renderApp('/patient/new', { user: STAFF_USER })
    expect(screen.getByRole('heading', { name: 'תור הסלמות' })).toBeInTheDocument()
  })

  it('routes inside the areas', () => {
    renderApp('/patient/new', { user: PATIENT_USER })
    expect(screen.getByRole('heading', { name: 'פנייה חדשה' })).toBeInTheDocument()
  })

  it('sends an unknown path home', () => {
    renderApp('/nowhere', { user: PATIENT_USER })
    expect(screen.getByRole('heading', { name: 'הפניות שלי' })).toBeInTheDocument()
  })
})
