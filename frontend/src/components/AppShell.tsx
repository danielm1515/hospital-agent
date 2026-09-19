import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { Button } from './Button'
import { Logo } from './Logo'
import { ThemeToggle } from './ThemeToggle'

/**
 * The signed-in frame (not in the design set; design decision 6): a top bar with
 * the compact lockup, optional area navigation, the user's display name, a quiet
 * logout button and the theme toggle, then the page body.
 */
export interface NavItem {
  to: string
  label: string
  /** Match the path exactly (for an area's index route). */
  end?: boolean
}

interface AppShellProps {
  children: ReactNode
  nav?: NavItem[]
}

export function AppShell({ children, nav = [] }: AppShellProps) {
  const { user, logout } = useAuth()
  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-start">
          <Logo variant="compact" />
          {nav.length > 0 && (
            <nav className="topnav" aria-label="ניווט ראשי">
              {nav.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.end} className="topnav-link">
                  {item.label}
                </NavLink>
              ))}
            </nav>
          )}
        </div>
        <div className="topbar-end">
          {user && <span className="topbar-user">{user.display_name}</span>}
          <Button variant="quiet" onClick={logout}>
            יציאה
          </Button>
          <ThemeToggle />
        </div>
      </header>
      <main className="page">{children}</main>
    </div>
  )
}
