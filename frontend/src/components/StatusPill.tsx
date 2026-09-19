import type { PatientStatus } from '../api/types'

/** The patient-facing status labels (react-ui plan, Task 1). */
export const STATUS_LABELS: Record<PatientStatus, string> = {
  received: 'התקבלה',
  in_progress: 'בטיפול',
  needs_document: 'ממתינה למסמך',
  in_review: 'אצל צוות',
  completed: 'הושלמה',
  closed: 'נסגרה',
}

type StatusPillProps =
  /** Patient variant: a Hebrew label per patient status. */
  | { status: PatientStatus; state?: never }
  /** Staff variant: the raw State name in the mono `.state` style. */
  | { state: string; status?: never }

/**
 * Not in the design set (design decision 6): built from the same tokens —
 * surfaces, lines, `--radius-pill`, and the `.state` mono class for the staff variant.
 */
export function StatusPill(props: StatusPillProps) {
  if (props.state !== undefined) {
    return (
      <span className="pill pill-state state" dir="ltr">
        {props.state}
      </span>
    )
  }
  const status = props.status
  return (
    <span className={`pill pill-${status}`} data-status={status}>
      <span className="pill-dot" aria-hidden="true" />
      {STATUS_LABELS[status]}
    </span>
  )
}
