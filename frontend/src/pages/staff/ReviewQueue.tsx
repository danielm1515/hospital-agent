import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import * as api from '../../api/client'
import type { ReviewItem } from '../../api/types'
import { Alert } from '../../components/Alert'
import { detailOf, escalationLabel, formatDateTime, returnedByLabel, stateLabel } from './labels'

/** The queue is polled rather than pushed (design decision 4). */
export const QUEUE_POLL_MS = 5000

/** The `location.state` a finished decision navigates back with. */
export interface QueueNotice {
  notice?: string
}

/**
 * `GET /api/staff/reviews`: the cases in `AwaitingHumanReview`, oldest update
 * first. Every column comes from the response - the escalation kind is shown
 * with its code, and `reasons` exactly as the API returned them.
 */
export function ReviewQueue() {
  const navigate = useNavigate()
  const location = useLocation()
  const notice = (location.state as QueueNotice | null)?.notice ?? null

  const [items, setItems] = useState<ReviewItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const rows = await api.listReviews()
        if (cancelled) return
        setItems(rows)
        setError(null)
      } catch (caught) {
        if (!cancelled) setError(detailOf(caught))
      }
    }
    void load()
    const timer = setInterval(() => void load(), QUEUE_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  const open = useCallback((caseId: string) => navigate(`/staff/cases/${encodeURIComponent(caseId)}`), [navigate])

  return (
    <section className="staff-page">
      <header className="page-head">
        <h1 className="page-h">תור הסלמות</h1>
        <p className="lede">פניות שממתינות להכרעת אדם. הרשימה מתרעננת כל חמש שניות.</p>
      </header>

      {notice && (
        <Alert variant="ok" title="ההכרעה נשמרה">
          {notice}
        </Alert>
      )}
      {error && (
        <Alert variant="error" title="טעינת התור נכשלה">
          <span className="mono">{error}</span>
        </Alert>
      )}

      {items === null ? (
        <p className="page-loading" role="status">
          טוען…
        </p>
      ) : items.length === 0 ? (
        <Alert variant="info" title="אין פניות הממתינות להכרעה">
          כל הפניות שהוסלמו הוכרעו. המסך יתעדכן מעצמו כשתגיע פנייה חדשה.
        </Alert>
      ) : (
        <div className="table-wrap card">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">פנייה</th>
                <th scope="col">מטופל</th>
                <th scope="col">סוג ההסלמה</th>
                <th scope="col">ממצב</th>
                <th scope="col">סיבות</th>
                <th scope="col">עדכון אחרון</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr className="row-link" key={item.case_id} onClick={() => open(item.case_id)}>
                  <td>
                    <Link
                      className="cell-link mono"
                      to={`/staff/cases/${encodeURIComponent(item.case_id)}`}
                      onClick={(event) => event.stopPropagation()}
                    >
                      {item.case_id}
                    </Link>
                  </td>
                  <td className="mono">{item.patient_id}</td>
                  <td>
                    <span className="cell-main">{escalationLabel(item.escalation_kind)}</span>
                    <span className="state cell-code">{item.escalation_kind}</span>
                  </td>
                  <td>
                    {item.escalated_from_state === null ? (
                      '—'
                    ) : (
                      <>
                        <span className="cell-main">{stateLabel(item.escalated_from_state)}</span>
                        <span className="state cell-code">{item.escalated_from_state}</span>
                      </>
                    )}
                  </td>
                  <td>
                    {item.reasons.length === 0 ? (
                      '—'
                    ) : (
                      <ul className="reasons">
                        {item.reasons.map((reason) => (
                          <li className="mono" key={reason}>
                            {reason}
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                  <td className="nowrap">
                    {formatDateTime(item.updated_at)}
                    {item.returned_by && (
                      <span className="cell-sub">
                        {returnedByLabel(item.returned_by)} <span className="mono">{item.returned_by}</span>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
