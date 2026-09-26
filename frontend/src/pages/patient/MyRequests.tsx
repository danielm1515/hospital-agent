import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import * as api from '../../api/client'
import type { PatientView } from '../../api/types'
import { Alert } from '../../components/Alert'
import { AppointmentsPanel } from '../../components/AppointmentsPanel'
import { Button } from '../../components/Button'
import { StatusPill } from '../../components/StatusPill'
import { NEW_REQUEST, requestPath } from './paths'
import { errorMessage, formatDateTime, isMoving, statusText, truncate, usePolling } from './helpers'

/**
 * The patient's main screen (sub-project 16, design D10): the shared
 * `AppointmentsPanel` (`listMyAppointments`) above "הפניות שלי" - one card per
 * request - the shortened text, a StatusPill, one line on what the status
 * means and the date - and a button for a new one. The two panels load and
 * fail independently: an appointments error never hides the requests list,
 * and the reverse also holds. While any case is still moving the list
 * refreshes every 3 s, because the agent advances it in the background
 * (`docs/api.md` §4).
 */
export function MyRequests() {
  const navigate = useNavigate()
  const [requests, setRequests] = useState<PatientView[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const list = await api.listRequests()
      setRequests(list)
      setError(null)
    } catch (caught) {
      setError(errorMessage(caught))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  usePolling(
    (requests ?? []).some((request) => isMoving(request.status)),
    () => void load(),
  )

  return (
    <div className="patient-home">
      <AppointmentsPanel audience="patient" load={api.listMyAppointments} />

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
          !error && (
            <p className="page-loading" role="status">
              טוען…
            </p>
          )
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
      </section>
    </div>
  )
}
