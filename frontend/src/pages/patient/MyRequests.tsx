import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import * as api from '../../api/client'
import type { PatientView } from '../../api/types'
import { Alert } from '../../components/Alert'
import { AppointmentsPanel } from '../../components/AppointmentsPanel'
import { Button } from '../../components/Button'
import { Loading } from '../../components/Loading'
import { StatusPill } from '../../components/StatusPill'
import { NEW_REQUEST, requestPath } from './paths'
import { errorMessage, formatDateTime, isMoving, statusText, truncate, usePolling } from './helpers'

/**
 * The patient's main screen (sub-project 16, design D10): the shared
 * `AppointmentsPanel` (`listMyAppointments`) above "הפניות שלי" - one card per
 * request - the shortened text, a StatusPill, one line on what the status
 * means and the date - and a button for a new one. Only the newest FIRST_PAGE requests load
 * at first (one more is asked for, to know whether there are others); "הצגת כל הפניות" loads
 * the rest. The two panels load and
 * fail independently: an appointments error never hides the requests list,
 * and the reverse also holds. While any case is still moving the list
 * refreshes every 3 s, because the agent advances it in the background
 * (`docs/api.md` §4).
 */
/** How many requests the screen shows before "הצגת כל הפניות". */
export const FIRST_PAGE = 5

export function MyRequests() {
  const navigate = useNavigate()
  const [requests, setRequests] = useState<PatientView[] | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadingAll, setLoadingAll] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Read by the polling refresh, so it keeps whichever view the patient chose; a ref, not
  // state, so choosing "show all" does not refetch on its own.
  const showAll = useRef(false)

  const load = useCallback(async () => {
    try {
      const all = showAll.current
      // One more than the page: if it comes back, there are others to offer.
      const list = await (all ? api.listRequests() : api.listRequests({ limit: FIRST_PAGE + 1 }))
      setHasMore(!all && list.length > FIRST_PAGE)
      setRequests(all ? list : list.slice(0, FIRST_PAGE))
      setError(null)
    } catch (caught) {
      setError(errorMessage(caught))
    }
  }, [])

  /** "הצגת כל הפניות": the button shows its spinner until the full list is in; on a failure
   * the error stays on screen and the first page and the button stay as they were. */
  async function loadAll() {
    setLoadingAll(true)
    try {
      const list = await api.listRequests()
      showAll.current = true
      setRequests(list)
      setHasMore(false)
      setError(null)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setLoadingAll(false)
    }
  }

  useEffect(() => {
    void load()
  }, [load])

  usePolling(
    (requests ?? []).some((request) => isMoving(request.status)),
    () => void load(),
  )

  return (
    <div className="patient-home">
      <AppointmentsPanel
        audience="patient"
        load={api.listMyAppointments}
        loadInstruction={api.getPatientInstruction}
      />

      <section className="card">
        <div className="page-head">
          <h1 className="page-h">הפניות שלי</h1>
          <Button variant="primary" onClick={() => navigate(NEW_REQUEST)}>
            פנייה חדשה
          </Button>
        </div>

        {error && (
          <Alert variant="error" title="לא הצלחנו לטעון את הפניות">
            {error}
          </Alert>
        )}

        {requests === null ? (
          !error && <Loading label="טוען פניות" />
        ) : requests.length === 0 ? (
          <p className="empty">עדיין אין פניות. אפשר לפתוח פנייה חדשה בכל שעה.</p>
        ) : (
          <ul className="req-list">
            {requests.map((request) => (
              <li key={request.case_id} className="req-card">
                <Link className="req-link" to={requestPath(request.case_id)}>
                  <span className="req-text">
                    {request.request_text ? truncate(request.request_text) : 'תוכן הפנייה נמחק מהמערכת.'}
                  </span>
                  <span className="req-meta">
                    <StatusPill status={request.status} />
                    <span className="req-note">{statusText(request.status).note}</span>
                    <time className="req-date" dateTime={request.created_at}>
                      נפתחה ב־{formatDateTime(request.created_at)}
                    </time>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}

        {hasMore && (
          <div className="req-more">
            <Button variant="quiet" busy={loadingAll} onClick={() => void loadAll()}>
              {loadingAll ? 'טוען את כל הפניות…' : 'הצגת כל הפניות'}
            </Button>
          </div>
        )}
      </section>
    </div>
  )
}
