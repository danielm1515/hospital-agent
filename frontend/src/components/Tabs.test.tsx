import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { Tabs, type TabItem } from './Tabs'

const TABS: TabItem[] = [
  { id: 'decision', label: 'הכרעה', content: <input aria-label="סיבה" /> },
  { id: 'data', label: 'תוכן הפנייה', count: 3, content: <p>תוכן</p> },
  { id: 'audit', label: 'יומן ביקורת', count: 12, content: <p>יומן</p> },
]

function Harness({ initial = 'decision', onSelect }: { initial?: string; onSelect?: (id: string) => void }) {
  const [selected, setSelected] = useState(initial)
  return (
    <Tabs
      label="פרטי הפנייה"
      idPrefix="case"
      tabs={TABS}
      selected={selected}
      onSelect={(id) => {
        setSelected(id)
        onSelect?.(id)
      }}
    />
  )
}

describe('Tabs', () => {
  it('renders a labelled tablist with one tab per item, the selected one marked', () => {
    render(<Harness />)
    expect(screen.getByRole('tablist', { name: 'פרטי הפנייה' })).toBeInTheDocument()
    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(3)
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    expect(tabs[1]).toHaveAttribute('aria-selected', 'false')
  })

  it('shows the count beside the label', () => {
    render(<Harness />)
    expect(screen.getByRole('tab', { name: /תוכן הפנייה/ })).toHaveTextContent('3')
  })

  it('shows only the selected panel, labelled by its tab', () => {
    render(<Harness />)
    const panel = screen.getByRole('tabpanel')
    expect(panel).toHaveAccessibleName('הכרעה')
    expect(screen.queryByText('יומן')).not.toBeVisible()
  })

  it('switches panels on click and reports the choice', async () => {
    const onSelect = vi.fn()
    render(<Harness onSelect={onSelect} />)
    await userEvent.click(screen.getByRole('tab', { name: /יומן ביקורת/ }))
    expect(onSelect).toHaveBeenCalledWith('audit')
    expect(screen.getByText('יומן')).toBeVisible()
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName(/יומן ביקורת/)
  })

  it('keeps an unselected panel mounted, so typed text survives a switch', async () => {
    render(<Harness />)
    await userEvent.type(screen.getByLabelText('סיבה'), 'טיוטה')
    await userEvent.click(screen.getByRole('tab', { name: /יומן ביקורת/ }))
    await userEvent.click(screen.getByRole('tab', { name: 'הכרעה' }))
    expect(screen.getByLabelText('סיבה')).toHaveValue('טיוטה')
  })

  it('uses a roving tabindex: only the selected tab is in the tab order', () => {
    render(<Harness initial="data" />)
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((tab) => tab.getAttribute('tabindex'))).toEqual(['-1', '0', '-1'])
  })

  it('moves with the arrow keys in RTL (Left = next, Right = previous), wrapping, and Home/End', async () => {
    render(<Harness />)
    screen.getAllByRole('tab')[0].focus()
    await userEvent.keyboard('{ArrowLeft}')
    expect(screen.getByRole('tab', { name: /תוכן הפנייה/ })).toHaveFocus()
    expect(screen.getByRole('tab', { name: /תוכן הפנייה/ })).toHaveAttribute('aria-selected', 'true')
    await userEvent.keyboard('{ArrowRight}{ArrowRight}')
    expect(screen.getByRole('tab', { name: /יומן ביקורת/ })).toHaveFocus()
    await userEvent.keyboard('{Home}')
    expect(screen.getByRole('tab', { name: 'הכרעה' })).toHaveFocus()
    await userEvent.keyboard('{End}')
    expect(screen.getByRole('tab', { name: /יומן ביקורת/ })).toHaveAttribute('aria-selected', 'true')
  })
})
