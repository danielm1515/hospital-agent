import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import * as api from '../../api/client'
import type { ReviewItem } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { Loading } from '../../components/Loading'
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
 * page after `next_cursor`.
 *
 * Fix round 1 (I1): a poll tick asks for a page sized to cover every row already loaded
 * (through one or more "load more" clicks), so it never shrinks the list back to the
 * first page; a generation counter drops any `loadMore` (or poll) response that is no
 * longer the latest request, so a slow one arriving late can't clobber a newer reload.
 */
export function ReviewQueue() {
  const navigate = useNavigate()
  const location = useLocation()
  const notice = (location.state as QueueNotice | null)?.notice ?? null

  const [items, setItems] = useState<ReviewItem[] | null>(null)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null)

  const itemsRef = useRef<ReviewItem[]>([])
  const generationRef = useRef(0)

  useEffect(() => {
    let cancelled = false
    async function load() {
      const generation = ++generationRef.current
      // Covers every row loaded so far, so a poll tick does not undo a "load more" (I1).
      const limit = Math.min(200, Math.max(50, itemsRef.current.length))
      try {
        const page = await api.listReviews({ limit })
        if (cancelled || generation !== generationRef.current) return
        itemsRef.current = page.items
        setItems(page.items)
        setNextCursor(page.next_cursor)
        setError(null)
      } catch (caught) {
        if (!cancelled && generation === generationRef.current) setError(detailOf(caught))
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
    const generation = ++generationRef.current
    setLoadingMore(true)
    setLoadMoreError(null)
    try {
      const page = await api.listReviews({ cursor: nextCursor })
      if (generation !== generationRef.current) return // a poll/reload has since replaced the list
      const merged = [...itemsRef.current, ...page.items]
      itemsRef.current = merged
      setItems(merged)
      setNextCursor(page.next_cursor)
    } catch (caught) {
      if (generation === generationRef.current) setLoadMoreError(detailOf(caught))
    } finally {
      if (generation === generationRef.current) setLoadingMore(false)
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
        <Loading label="טוען פניות" />
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
              {loadMoreError && (
                <Alert variant="error" title="טעינת העוד נכשלה">
                  <span className="mono">{loadMoreError}</span>
                </Alert>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
