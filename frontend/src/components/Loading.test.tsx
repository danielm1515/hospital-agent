import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Loading } from './Loading'

describe('Loading', () => {
  it('is an accessible, live status with the default Hebrew text', () => {
    render(<Loading />)
    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-live', 'polite')
    expect(status).toHaveTextContent('טוען…')
  })

  it('shows a caller-supplied label instead of the default', () => {
    render(<Loading label="טוען פניות" />)
    expect(screen.getByRole('status')).toHaveTextContent('טוען פניות')
  })

  it('defaults to the page-sized ring', () => {
    const { container } = render(<Loading />)
    expect(screen.getByRole('status')).toHaveClass('loader-page')
    expect(container.querySelector('.loader-ring')).toBeInTheDocument()
  })

  it('renders the smaller inline ring when asked', () => {
    render(<Loading size="inline" />)
    expect(screen.getByRole('status')).toHaveClass('loader-inline')
  })
})
