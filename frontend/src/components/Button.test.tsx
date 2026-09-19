import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Button } from './Button'

describe('Button', () => {
  it('is a primary, non-submitting button by default', () => {
    render(<Button>שלחו לי קוד</Button>)
    const button = screen.getByRole('button', { name: 'שלחו לי קוד' })
    expect(button).toHaveClass('btn', 'primary')
    expect(button).toHaveAttribute('type', 'button')
  })

  it.each(['primary', 'secondary', 'quiet', 'danger'] as const)('renders the %s variant', (variant) => {
    render(<Button variant={variant}>כפתור</Button>)
    expect(screen.getByRole('button')).toHaveClass('btn', variant)
  })

  it('shows a spinner and aria-busy while busy, and swallows clicks', async () => {
    const onClick = vi.fn()
    const { container } = render(
      <Button busy onClick={onClick}>
        מאמת קוד
      </Button>,
    )
    const button = screen.getByRole('button')
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(button).toHaveClass('busy')
    expect(container.querySelector('.spin')).toBeInTheDocument()
    await userEvent.click(button)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('is disabled and full width when asked', async () => {
    const onClick = vi.fn()
    render(
      <Button disabled block onClick={onClick}>
        כניסה למערכת
      </Button>,
    )
    const button = screen.getByRole('button')
    expect(button).toBeDisabled()
    expect(button).toHaveClass('block')
    await userEvent.click(button)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('calls onClick when idle', async () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>אישור</Button>)
    await userEvent.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})
