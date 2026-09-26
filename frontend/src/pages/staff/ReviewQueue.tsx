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

/** How long the decision notice stays on screen before it dismisses itself (Task 7). */
export const NOTICE_DISMISS_MS = 8000

/** The `location.state` a finished decision navigates back with. */
export interface QueueNotice {
  notice?: string
  title?: string
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
  const headingRef = useRef<HTMLHeadingElement>(null)

  // Copied out of `location.state` once, on mount (a lazy initializer runs exactly once) -
  // never read from `location.state` again after that, because a reload keeps the browser's
  // `history.state` around, and the notice must not come back from a reload or Back (Task 7).
  // Fix round 1 (M4): no fallback title - the title is whatever `ReviewCase` sent (or none),
  // never invented here, so the decide/onSent/onAnswered titles are pinned by their own tests.
  const [notice, setNotice] = useState<{ text: string; title?: string } | null>(() => {
    const state = location.state as QueueNotice | null
    return state?.notice ? { text: state.notice, title: state.title } : null
  })
  // Fix round 1 (M8): hovering or focusing the notice pauses its auto-dismiss.
  const [noticePaused, setNoticePaused] = useState(false)

  useEffect(() => {
    // Replaces the history entry's state with `null` right after reading it, so a refresh
    // or the Back button finds nothing to show again.
    if (location.state !== null) {
      navigate(location.pathname, { replace: true, state: null })
    }
  }, [])

  // Fix round 1 (M8): a real pause, not a restart - the remaining time survives a
  // hover/focus-driven pause and resume, tracked across the effect's own start/stop.
  const remainingMsRef = useRef(NOTICE_DISMISS_MS)
  useEffect(() => {
    if (!notice || noticePaused) return
    const startedAt = Date.now()
    const timer = setTimeout(() => setNotice(null), remainingMsRef.current)
    return () => {
      clearTimeout(timer)
      remainingMsRef.current -= Date.now() - startedAt
    }
  }, [notice, noticePaused])

  function closeNotice() {
    setNotice(null)
    // Fix round 1 (M8): focus goes to the page heading, not lost to the document body.
    headingRef.current?.focus()
  }

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
      // A reload/poll supersedes any "load more" in flight - its own result (if it ever
      // arrives) is now guarded out below, so the button must not stay busy waiting for it
      // (fix round 2, N1).
      setLoadingMore(false)
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
      // Unconditional (fix round 2, N1): a generation that has since moved on still means
      // this request is over - only the data write above stays guarded, not the busy flag,
      // or the button would stay busy forever after a race with a poll/reload.
      setLoadingMore(false)
    }
  }

  const open = useCallback((caseId: string) => navigate(`/staff/cases/${encodeURIComponent(caseId)}`), [navigate])

  return (
    <section className="staff-page">
      <header className="page-head">
        <h1 className="page-h" ref={headingRef} tabIndex={-1}>
          תור הסלמות
        </h1>
        <p className="lede">
          פניות שממתינות להכרעת אדם, מהחדשה שנכנסה לתור ועד הישנה. הרשימה מתרעננת כל חמש
          שניות; פנייה שחזרה מתשובת מטופל או מבקשה שפג זמנה נכנסת מחדש וקופצת לראש התור, כי
          היא זקוקה שוב לתשומת לב.
        </p>
      </header>

      {notice && (
        <div
          onMouseEnter={() => setNoticePaused(true)}
          onMouseLeave={() => setNoticePaused(false)}
          onFocus={() => setNoticePaused(true)}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setNoticePaused(false)
          }}
        >
          <Alert variant="ok" title={notice.title} onClose={closeNotice}>
            {notice.text}
          </Alert>
        </div>
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
                <Alert variant="error" title="טעינת פניות נוספות נכשלה">
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
