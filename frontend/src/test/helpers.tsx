import { render } from '@testing-library/react'
import type { RenderResult } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import { AuthContext } from '../auth/AuthContext'
import type { AuthStatus, AuthValue } from '../auth/AuthContext'
import type { Me, Role } from '../api/types'

export const PATIENT_USER: Me = { user_id: 'P-10041', role: 'patient', display_name: 'דנה כהן' }
export const STAFF_USER: Me = {
  user_id: 'coordinator_nurse',
  role: 'clinical_staff',
  display_name: 'אחות מתאמת',
}

export function makeUser(role: Role): Me {
  return role === 'patient' ? PATIENT_USER : { ...STAFF_USER, role }
}

export interface AuthOptions {
  user?: Me | null
  status?: AuthStatus
  login?: AuthValue['login']
  logout?: AuthValue['logout']
}

export function authValue(options: AuthOptions = {}): AuthValue {
  const user = options.user ?? null
  return {
    user,
    status: options.status ?? 'ready',
    login: options.login ?? vi.fn(async () => user ?? PATIENT_USER),
    logout: options.logout ?? vi.fn(),
  }
}

export function TestAuthProvider({ value, children }: { value: AuthValue; children: ReactNode }) {
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** Renders `ui` inside a MemoryRouter and a stubbed AuthContext. */
export function renderWithAuth(
  ui: ReactElement,
  options: AuthOptions & { route?: string } = {},
): RenderResult & { auth: AuthValue } {
  const { route = '/', ...authOptions } = options
  const auth = authValue(authOptions)
  const result = render(
    <MemoryRouter initialEntries={[route]}>
      <TestAuthProvider value={auth}>{ui}</TestAuthProvider>
    </MemoryRouter>,
  )
  return { ...result, auth }
}
