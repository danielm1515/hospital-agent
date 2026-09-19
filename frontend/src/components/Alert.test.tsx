import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
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
})
