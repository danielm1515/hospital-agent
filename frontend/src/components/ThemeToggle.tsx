import { useEffect, useState } from 'react'

/**
 * The design's theme toggle: an explicit `data-theme` on `<html>`, starting from
 * `prefers-color-scheme`, and (as DESIGN-NOTES §7 recommends) the user's explicit
 * choice persisted in `localStorage`, every access wrapped in try/catch.
 */
export type Theme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'ramon-theme'

function savedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    return null
  }
}

function systemTheme(): Theme {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export function initialTheme(): Theme {
  return savedTheme() ?? systemTheme()
}

interface ThemeToggleProps {
  /** Float in the top inline-start corner, as on the design's standalone pages. */
  floating?: boolean
}

export function ThemeToggle({ floating = false }: ThemeToggleProps) {
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  function toggle() {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next)
    } catch {
      /* storage unavailable: the choice lasts for this page only */
    }
  }

  return (
    <button
      type="button"
      className={floating ? 'theme-toggle floating' : 'theme-toggle'}
      title="החלפת ערכת צבע"
      onClick={toggle}
    >
      {theme === 'dark' ? 'מצב בהיר' : 'מצב כהה'}
    </button>
  )
}
