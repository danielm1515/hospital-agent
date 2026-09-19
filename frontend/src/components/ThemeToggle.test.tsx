import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { THEME_STORAGE_KEY, ThemeToggle } from './ThemeToggle'

function withSystemDark(dark: boolean) {
  vi.spyOn(window, 'matchMedia').mockImplementation(
    (query: string) => ({ matches: dark, media: query }) as MediaQueryList,
  )
}

describe('ThemeToggle', () => {
  it('starts from the OS preference and toggles data-theme', async () => {
    withSystemDark(false)
    render(<ThemeToggle />)
    expect(document.documentElement).toHaveAttribute('data-theme', 'light')
    const button = screen.getByRole('button', { name: 'מצב כהה' })
    await userEvent.click(button)
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
    expect(screen.getByRole('button', { name: 'מצב בהיר' })).toBeInTheDocument()
  })

  it('persists the explicit choice in localStorage', async () => {
    withSystemDark(false)
    render(<ThemeToggle />)
    await userEvent.click(screen.getByRole('button'))
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
  })

  it('prefers the saved choice over the OS preference', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'dark')
    withSystemDark(false)
    render(<ThemeToggle />)
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
  })

  it('survives unavailable storage', async () => {
    withSystemDark(true)
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    render(<ThemeToggle />)
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
    await userEvent.click(screen.getByRole('button'))
    expect(document.documentElement).toHaveAttribute('data-theme', 'light')
    getItem.mockRestore()
    setItem.mockRestore()
  })

  it('floats when asked', () => {
    withSystemDark(false)
    render(<ThemeToggle floating />)
    expect(screen.getByRole('button')).toHaveClass('theme-toggle', 'floating')
  })
})
