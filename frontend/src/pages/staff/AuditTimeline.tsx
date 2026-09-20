import type { TraceRow } from '../../api/types'
import { formatAuditTime, formatDate, gapAfter } from './labels'

/**
 * The Audit trace as a timeline (design decision 6: no table/timeline exists in
 * the design set, so it is built from the same tokens and the `.state` mono style).
 *
 * It renders the rows exactly as the API returned them - `record_type`, the event,
 * `state_before` → `state_after`, the time and `policy_reasons` - and never adds
 * content of its own: the Audit holds no message or document text (§12.3).
 *
 * A case's rows are written within a second or two, so each row carries its position
 * in the trace, its time to the millisecond, and the gap from the row before it -
 * otherwise a 35-row trace reads as 35 identical timestamps.
 */
/**
 * What a row did to the State, as a Hebrew sentence. An arrow between two Latin
 * state names reads either way on an RTL line - which of the two came first is
 * exactly what a reader needs, so the words say it instead.
 *
 * `state_before === null` is Initial (`docs/api.md` §5); a row that leaves the State
 * alone (an execution row, a Blocked row) says so rather than pointing at itself.
 */
function Transition({ before, after }: { before: string | null; after: string | null }) {
  if (after === null && before === null) return <span className="audit-timeline-same">ללא שינוי מצב</span>
  if (after === null) {
    return (
      <>
        נשאר במצב <span className="state">{before}</span>
      </>
    )
  }
  if (before === null) {
    return (
      <>
        נפתח במצב <span className="state">{after}</span>
      </>
    )
  }
  if (before === after) {
    return (
      <>
        נשאר במצב <span className="state">{after}</span>
      </>
    )
  }
  return (
    <>
      ממצב <span className="state">{before}</span> למצב <span className="state">{after}</span>
    </>
  )
}

export function AuditTimeline({ rows, label }: { rows: TraceRow[]; label?: string }) {
  if (rows.length === 0) {
    return <p className="empty-note">אין רשומות ביומן הביקורת לפנייה הזו.</p>
  }
  return (
    <>
      <p className="audit-timeline-day">
        {rows.length} רשומות · {formatDate(rows[0].recorded_at)}
      </p>
      <ol className="audit-timeline" aria-label={label ?? 'יומן הביקורת של הפנייה'}>
        {rows.map((row, index) => {
          const gap = gapAfter(index > 0 ? rows[index - 1].recorded_at : null, row.recorded_at)
          return (
            <li className="audit-timeline-item" key={row.audit_id}>
              <div className="audit-timeline-head">
                <span className="audit-timeline-index mono" aria-hidden="true">
                  {index + 1}.
                </span>
                <span className={`audit-timeline-kind kind-${row.record_type}`}>{row.record_type}</span>
                <span className="audit-timeline-time">
                  <time dateTime={row.recorded_at}>{formatAuditTime(row.recorded_at)}</time>
                  {gap && <span className="audit-timeline-gap">{gap}</span>}
                </span>
              </div>
              <p className="audit-timeline-event">
                <span className="mono">{row.event}</span>
              </p>
              <p className="audit-timeline-states">
                <Transition before={row.state_before} after={row.state_after} />
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
          )
        })}
      </ol>
    </>
  )
}
