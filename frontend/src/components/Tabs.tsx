import { useRef, type KeyboardEvent, type ReactNode } from 'react'

/** One tab: its label, an optional count shown beside it, and the panel it opens. */
export interface TabItem {
  id: string
  label: string
  count?: number
  content: ReactNode
}

interface TabsProps {
  /** The tablist's accessible name. */
  label: string
  /** Prefix for the tab/panel element ids - unique per page (two lists may share a page). */
  idPrefix: string
  tabs: TabItem[]
  selected: string
  onSelect: (id: string) => void
}

/**
 * WAI-ARIA tabs (automatic activation): a roving tabindex, arrows move and select, Home/End
 * jump. The page is RTL, so ArrowLeft is "next" and ArrowRight "previous".
 *
 * Every panel stays mounted and the unselected ones are `hidden`: a reviewer who half-wrote a
 * decision reason, opened the audit tab to check something and came back, finds the text
 * still there.
 */
export function Tabs({ label, idPrefix, tabs, selected, onSelect }: TabsProps) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({})

  function move(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = tabs.length - 1
    const target =
      event.key === 'ArrowLeft'
        ? index === last
          ? 0
          : index + 1
        : event.key === 'ArrowRight'
          ? index === 0
            ? last
            : index - 1
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? last
              : null
    if (target === null) return
    event.preventDefault()
    const id = tabs[target].id
    onSelect(id)
    refs.current[id]?.focus()
  }

  return (
    <div className="tabs">
      <div className="tab-list" role="tablist" aria-label={label}>
        {tabs.map((tab, index) => {
          const active = tab.id === selected
          return (
            <button
              key={tab.id}
              ref={(node) => {
                refs.current[tab.id] = node
              }}
              type="button"
              role="tab"
              id={`${idPrefix}-tab-${tab.id}`}
              aria-controls={`${idPrefix}-panel-${tab.id}`}
              aria-selected={active}
              tabIndex={active ? 0 : -1}
              className={active ? 'tab active' : 'tab'}
              onClick={() => onSelect(tab.id)}
              onKeyDown={(event) => move(event, index)}
            >
              {tab.label}
              {tab.count !== undefined && <span className="tab-count">{tab.count}</span>}
            </button>
          )
        })}
      </div>
      {tabs.map((tab) => (
        <div
          key={tab.id}
          role="tabpanel"
          id={`${idPrefix}-panel-${tab.id}`}
          aria-labelledby={`${idPrefix}-tab-${tab.id}`}
          className="tab-panel"
          hidden={tab.id !== selected}
          tabIndex={0}
        >
          {tab.content}
        </div>
      ))}
    </div>
  )
}
