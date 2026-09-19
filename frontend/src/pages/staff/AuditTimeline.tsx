import type { TraceRow } from '../../api/types'
import { formatDateTime } from './labels'

/**
 * The Audit trace as a timeline (design decision 6: no table/timeline exists in
 * the design set, so it is built from the same tokens and the `.state` mono style).
 *
 * It renders the rows exactly as the API returned them - `record_type`, the event,
 * `state_before` → `state_after`, the time and `policy_reasons` - and never adds
 * content of its own: the Audit holds no message or document text (§12.3).
 */
export function AuditTimeline({ rows, label }: { rows: TraceRow[]; label?: string }) {
  if (rows.length === 0) {
    return <p className="empty-note">אין רשומות ביומן הביקורת לפנייה הזו.</p>
  }
  return (
    <ol className="audit-timeline" aria-label={label ?? 'יומן הביקורת של הפנייה'}>
      {rows.map((row) => (
        <li className="audit-timeline-item" key={row.audit_id}>
          <div className="audit-timeline-head">
            <span className={`audit-timeline-kind kind-${row.record_type}`}>{row.record_type}</span>
            <span className="audit-timeline-time">{formatDateTime(row.recorded_at)}</span>
          </div>
          <p className="audit-timeline-event mono">{row.event}</p>
          <p className="audit-timeline-states">
            <span className="state">{row.state_before ?? '—'}</span>
            <span className="audit-timeline-arrow" aria-hidden="true">
              ←
            </span>
            <span className="state">{row.state_after ?? '—'}</span>
          </p>
          {row.action && (
            <p className="audit-timeline-meta">
              פעולה: <span className="mono">{row.action}</span>
            </p>
          )}
          {row.policy_result && (
            <p className="audit-timeline-meta">
              החלטת מדיניות: <span className="mono">{row.policy_result}</span>
            </p>
          )}
          {row.policy_reasons.length > 0 && (
            <ul className="reasons">
              {row.policy_reasons.map((reason) => (
                <li className="mono" key={reason}>
                  {reason}
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ol>
  )
}
