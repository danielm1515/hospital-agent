import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import * as api from '../../api/client'
import type { ReviewItem } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { detailOf, escalationLabel, formatDateTime, returnedByLabel, stateLabel } from './labels'

/** The queue is polled rather than pushed (design decision 4). */
export const QUEUE_POLL_MS = 5000

/** The `location.state` a finished decision navigates back with. */
export interface QueueNotice {
  notice?: string
}

/**
 * `GET /api/staff/reviews` (staff-fixes design Task 5): the cases in `AwaitingHumanReview`,
 * newest entry into that State first - a case that returns from a patient's reply or a
 * timed-out request re-enters and jumps to the top, since it needs attention again. Every
 * column comes from the response - the escalation kind is shown with its code, and
 * `reasons` exactly as the API returned them. Keyset-paginated: "טעינת עוד" asks for the
 * page after `next_cursor`; each poll still reloads the first page from the top.
 */
export function ReviewQueue() {
  const navigate = useNavigate()
  const location = useLocation()
  const notice = (location.state as QueueNotice | null)?.notice ?? null

  const [items, setItems] = useState<ReviewItem[] | null>(null)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const page = await api.listReviews()
        if (cancelled) return
        setItems(page.items)
        setNextCursor(page.next_cursor)
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

  async function loadMore() {
    if (nextCursor === null) return
    setLoadingMore(true)
    try {
      const page = await api.listReviews({ cursor: nextCursor })
      setItems((previous) => [...(previous ?? []), ...page.items])
      setNextCursor(page.next_cursor)
    } catch (caught) {
      setError(detailOf(caught))
    } finally {
      setLoadingMore(false)
    }
  }

  const open = useCallback((caseId: string) => navigate(`/staff/cases/${encodeURIComponent(caseId)}`), [navigate])

  return (
    <section className="staff-page">
      <header className="page-head">
        <h1 className="page-h">תור הסלמות</h1>
        <p className="lede">
          פניות שממתינות להכרעת אדם, מהחדשה שנכנסה לתור ועד הישנה. הרשימה מתרעננת כל חמש
          שניות; פנייה שחזרה מתשובת מטופל או מבקשה שפג זמנה נכנסת מחדש וקופצת לראש התור, כי
          היא זקוקה שוב לתשומת לב.
        </p>
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
                <th scope="col">נכנסה לתור</th>
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
                    {formatDateTime(item.entered_at)}
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
          {nextCursor !== null && (
            <div className="table-footer">
              <Button variant="secondary" busy={loadingMore} onClick={() => void loadMore()}>
                טעינת עוד
              </Button>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
