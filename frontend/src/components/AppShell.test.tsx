import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'
import { renderWithAuth, STAFF_USER } from '../test/helpers'

describe('AppShell', () => {
  it('shows the compact lockup, the display name and the theme toggle', () => {
    const { container } = renderWithAuth(<AppShell>תוכן</AppShell>, { user: STAFF_USER })
    expect(container.querySelector('.lock.compact')).toBeInTheDocument()
    expect(screen.getByText(STAFF_USER.display_name)).toHaveClass('topbar-user')
    expect(screen.getByRole('button', { name: 'מצב כהה' })).toBeInTheDocument()
    expect(screen.getByRole('main')).toHaveTextContent('תוכן')
  })

  it('logs out through the quiet button', async () => {
    const logout = vi.fn()
    renderWithAuth(<AppShell>תוכן</AppShell>, { user: STAFF_USER, logout })
    const button = screen.getByRole('button', { name: 'יציאה' })
    expect(button).toHaveClass('btn', 'quiet')
    await userEvent.click(button)
    expect(logout).toHaveBeenCalledTimes(1)
  })

  it('renders the area navigation and marks the current page', () => {
    renderWithAuth(
      <AppShell
        nav={[
          { to: '/staff', label: 'תור הסלמות', end: true },
          { to: '/staff/monitor', label: 'כל הפניות' },
        ]}
      >
        תוכן
      </AppShell>,
      { user: STAFF_USER, route: '/staff/monitor' },
    )
    expect(screen.getByRole('navigation', { name: 'ניווט ראשי' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'כל הפניות' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'תור הסלמות' })).not.toHaveAttribute('aria-current')
  })

  it('renders no navigation when there are no items', () => {
    renderWithAuth(<AppShell>תוכן</AppShell>, { user: STAFF_USER })
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
  })
})
