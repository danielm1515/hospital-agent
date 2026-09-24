import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Bars, Meter, Tile } from './MetricsParts'

describe('Tile', () => {
  it('shows the value above its label', () => {
    render(<Tile label="נפתחו" value="12" note="בטווח" />)
    const tile = screen.getByText('נפתחו').closest('.metrics-tile')!
    expect(tile).toHaveTextContent('12')
    expect(tile).toHaveTextContent('בטווח')
  })
})

describe('Bars', () => {
  it('writes every value as text, scales to the largest and shows the code beside its label', () => {
    const { container } = render(
      <Bars
        rows={[
          { code: 'MedicalQuestion', label: 'שאלה רפואית', count: 4 },
          { code: 'Unsupported', label: 'לא נתמכת', count: 1 },
        ]}
        empty="אין"
      />,
    )
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('שאלה רפואית')
    expect(items[0]).toHaveTextContent('MedicalQuestion')
    expect(items[0]).toHaveTextContent('4')
    const fills = container.querySelectorAll<HTMLElement>('.metrics-bar-fill')
    expect(fills[0].style.width).toBe('100%')
    expect(fills[1].style.width).toBe('25%')
  })

  it('does not repeat a code that is its own label, and keeps it inside .mono', () => {
    render(<Bars rows={[{ code: 'guard_failed', label: 'guard_failed', count: 1 }]} empty="אין" />)
    const matches = screen.getAllByText('guard_failed')
    expect(matches).toHaveLength(1)
    expect(matches[0]).toHaveClass('mono')
  })

  it('says so when there is nothing to draw', () => {
    render(<Bars rows={[]} empty="אין פניות בטווח." />)
    expect(screen.getByText('אין פניות בטווח.')).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })
})

describe('Meter', () => {
  it('fills to the ratio and names it for assistive technology', () => {
    const { container } = render(<Meter ratio={0.6} label="עמידה בזמן" />)
    expect(screen.getByRole('img', { name: 'עמידה בזמן: 60%' })).toBeInTheDocument()
    expect(container.querySelector<HTMLElement>('.metrics-meter-fill')!.style.width).toBe('60%')
  })
})
