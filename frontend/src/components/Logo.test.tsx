import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Logo, ORG_NAME, ORG_SHORT, PRODUCT_NAME } from './Logo'

describe('Logo', () => {
  it('renders the full lockup with org and product names', () => {
    const { container } = render(<Logo />)
    expect(screen.getByText(ORG_NAME)).toBeInTheDocument()
    expect(screen.getByText(PRODUCT_NAME)).toBeInTheDocument()
    const mark = container.querySelector('svg.mark')!
    expect(mark).toHaveAttribute('width', '40')
    expect(mark).toHaveAttribute('aria-hidden', 'true')
  })

  it('hides the product line when asked', () => {
    render(<Logo withProduct={false} markSize={32} />)
    expect(screen.queryByText(PRODUCT_NAME)).not.toBeInTheDocument()
  })

  it('renders the compact variant', () => {
    const { container } = render(<Logo variant="compact" />)
    expect(container.querySelector('.lock.compact')).toBeInTheDocument()
    expect(screen.getByText(ORG_SHORT)).toHaveClass('compact-org')
    expect(container.querySelector('svg.mark')).toHaveAttribute('width', '28')
  })

  it('renders the onbrand variant with an inverted mark', () => {
    const { container } = render(<Logo variant="onbrand" />)
    expect(container.querySelector('.lock.onbrand')).toBeInTheDocument()
    expect(screen.getByText(ORG_NAME)).toHaveClass('invert')
    expect(container.querySelector('svg.mark rect')).toHaveAttribute('fill', '#ffffff')
  })
})
