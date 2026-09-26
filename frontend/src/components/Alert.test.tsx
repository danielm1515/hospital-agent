import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Alert } from './Alert'

describe('Alert', () => {
  it.each(['info', 'warn', 'ok'] as const)('renders the %s variant as a status', (variant) => {
    const { container } = render(
      <Alert variant={variant} title="ממתין לאישור רופא">
        הפנייה הועברה לבדיקת אדם.
      </Alert>,
    )
    const alert = screen.getByRole('status')
    expect(alert).toHaveClass('alert', variant)
    expect(screen.getByText('ממתין לאישור רופא')).toHaveClass('title')
    expect(container.querySelector('svg.ico')).toBeInTheDocument()
  })

  it('renders the error variant with role="alert"', () => {
    render(
      <Alert variant="error" title="החשבון ננעל לחמש עשרה דקות">
        חמישה ניסיונות כניסה שגויים.
      </Alert>,
    )
    const alert = screen.getByRole('alert')
    expect(alert).toHaveClass('alert', 'error')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('keeps a mono fragment inside the text', () => {
    const { container } = render(
      <Alert variant="warn" title="ממתין לאישור רופא">
        הפנייה הועברה לבדיקת אדם. <span className="mono">AWAITING_HUMAN_REVIEW</span>
      </Alert>,
    )
    expect(container.querySelector('.text .mono')).toHaveTextContent('AWAITING_HUMAN_REVIEW')
  })

  it('renders without a body when there is only a title', () => {
    const { container } = render(<Alert title="אושר" />)
    expect(container.querySelector('.text')).toBeNull()
  })

  it('shows no close button by default', () => {
    render(<Alert title="הודעה">תוכן</Alert>)
    expect(screen.queryByRole('button', { name: 'סגירה' })).not.toBeInTheDocument()
  })

  it('shows a close button that calls onClose when one is given', async () => {
    const onClose = vi.fn()
    render(
      <Alert variant="ok" title="ההכרעה נשמרה" onClose={onClose}>
        תוכן
      </Alert>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'סגירה' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('places the close button as a sibling of the status region, not inside it (M8)', () => {
    render(
      <Alert variant="ok" title="ההכרעה נשמרה" onClose={() => {}}>
        תוכן
      </Alert>,
    )
    const status = screen.getByRole('status')
    const button = screen.getByRole('button', { name: 'סגירה' })
    expect(status.contains(button)).toBe(false)
    expect(button.parentElement).toBe(status.parentElement)
  })
})
