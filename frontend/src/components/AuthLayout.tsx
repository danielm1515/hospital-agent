import type { ReactNode } from 'react'
import { Logo } from './Logo'
import { ThemeToggle } from './ThemeToggle'

/**
 * The login pages' skeleton (`patient-login.html`, `admin-login.html`): `.screen`
 * split into a wide `.form-pane` (a header with the lockup and the language pill,
 * then the page's form) and a side pane. Below 900px the panes stack.
 *
 * - `aside="brand"`: the patient page's `--surface-brand` panel, 58/42, shown first when stacked.
 * - `aside="side"`: the staff page's light `.side-pane`, 60/40, shown after the form when stacked.
 */
interface AuthLayoutProps {
  children: ReactNode
  asideContent: ReactNode
  aside?: 'brand' | 'side'
}

export function AuthLayout({ children, asideContent, aside = 'brand' }: AuthLayoutProps) {
  return (
    <div className="auth-root">
      <div className={`screen screen-${aside}`}>
        <section className="pane form-pane">
          <header className="top">
            <Logo variant="full" withProduct={false} markSize={32} />
            <LanguageSwitch />
          </header>
          {children}
        </section>
        <aside className={aside === 'brand' ? 'pane brand-pane' : 'pane side-pane'}>{asideContent}</aside>
      </div>
      <ThemeToggle floating />
    </div>
  )
}

/**
 * The design's עב / EN pill. English is out of scope (design §1), so, as in the
 * design itself, only Hebrew is active; EN is shown but marked unavailable.
 */
function LanguageSwitch() {
  return (
    <div className="lang" role="group" aria-label="בחירת שפה">
      <button type="button" className="lang-btn on" aria-pressed="true">
        עב
      </button>
      <button type="button" className="lang-btn" aria-pressed="false" aria-disabled="true" title="בקרוב">
        EN
      </button>
    </div>
  )
}
