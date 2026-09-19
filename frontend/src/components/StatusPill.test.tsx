import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { PatientStatus } from '../api/types'
import { StatusPill, STATUS_LABELS } from './StatusPill'

const CASES: Array<[PatientStatus, string]> = [
  ['received', 'התקבלה'],
  ['in_progress', 'בטיפול'],
  ['needs_document', 'ממתינה למסמך'],
  ['in_review', 'אצל צוות'],
  ['completed', 'הושלמה'],
  ['closed', 'נסגרה'],
]

describe('StatusPill', () => {
  it.each(CASES)('labels %s in Hebrew', (status, label) => {
    const { container } = render(<StatusPill status={status} />)
    expect(screen.getByText(label)).toBeInTheDocument()
    expect(container.querySelector('.pill')).toHaveClass(`pill-${status}`)
  })

  it('covers every patient status', () => {
    expect(Object.keys(STATUS_LABELS).sort()).toEqual(CASES.map(([status]) => status).sort())
  })

  it('shows a raw State in the mono staff style', () => {
    render(<StatusPill state="AwaitingHumanReview" />)
    const pill = screen.getByText('AwaitingHumanReview')
    expect(pill).toHaveClass('pill', 'pill-state', 'state')
    expect(pill).toHaveAttribute('dir', 'ltr')
  })
})
