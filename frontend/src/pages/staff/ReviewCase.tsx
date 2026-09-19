import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import * as api from '../../api/client'
import type { DataLogEntry, Decision, DecisionBody, ReviewContext, ReviewItem } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { StatusPill } from '../../components/StatusPill'
import { TextField } from '../../components/TextField'
import { AuditTimeline } from './AuditTimeline'
import {
  DECISION_LABELS,
  REQUIRED_FIELD_HINTS,
  REQUIRED_FIELD_LABELS,
  dataKindLabel,
  detailOf,
  escalationLabel,
  formatDateTime,
  groupByKind,
  toIsoWithOffset,
} from './labels'

/**
 * One case in the review queue (`docs/api.md` §5), in three columns: the Data Log
 * content, the Audit trace and the decision form.
 *
 * The decision is bound to what the reviewer read: it carries the
 * `shown_context_ref` of the context on screen, and a `409 context_changed`
 * offers to fetch the context again instead of retrying blindly. The buttons and
 * the extra fields are exactly the queue item's `allowed_decisions` and
 * `required_fields` - nothing is inferred locally.
 */
export function ReviewCase() {
  const { caseId = '' } = useParams<{ caseId: string }>()
  const navigate = useNavigate()

  const [context, setContext] = useState<ReviewContext | null>(null)
  const [item, setItem] = useState<ReviewItem | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [identityRef, setIdentityRef] = useState('')
  const [deadline, setDeadline] = useState('')
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [decisionError, setDecisionError] = useState<string | null>(null)
  const [contextChanged, setContextChanged] = useState(false)
  const [busy, setBusy] = useState<Decision | null>(null)

  const [pendingDelete, setPendingDelete] = useState<DataLogEntry | null>(null)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    try {
      const [fresh, queue] = await Promise.all([api.getContext(caseId), api.listReviews()])
      setContext(fresh)
      setItem(queue.find((entry) => entry.case_id === caseId) ?? null)
      setLoadError(null)
    } catch (caught) {
      setLoadError(detailOf(caught))
    }
  }, [caseId])

  useEffect(() => {
    void load()
  }, [load])

  const refreshContext = useCallback(async () => {
    setContextChanged(false)
    setDecisionError(null)
    await load()
  }, [load])

  const allowed = item?.allowed_decisions ?? []
  const required = item?.required_fields ?? []

  async function submit(decision: Decision) {
    if (!context) return
    setDecisionError(null)
    setFieldError(null)

    const trimmedReason = reason.trim()
    if (!trimmedReason) {
      setReasonError('נדרשת סיבה להכרעה.')
      return
    }
    setReasonError(null)

    const body: DecisionBody = {
      decision,
      reason: trimmedReason,
      shown_context_ref: context.shown_context_ref,
    }

    if (decision === 'approve') {
      if (required.includes('verified_identity_ref')) {
        if (!identityRef.trim()) {
          setFieldError('נדרשת אסמכתת זיהוי לאישור הפנייה.')
          return
        }
        body.verified_identity_ref = identityRef.trim()
      }
      if (required.includes('patient_deadline')) {
        const iso = toIsoWithOffset(deadline)
        if (!iso) {
          setFieldError('נדרש מועד יעד חדש ותקין למטופל.')
          return
        }
        body.patient_deadline = iso
      }
    }

    setBusy(decision)
    try {
      const result = await api.decide(caseId, body)
      navigate('/staff', {
        replace: true,
        state: { notice: `הפנייה ${result.case_id} עברה למצב ${result.state}.` },
      })
    } catch (caught) {
      const detail = detailOf(caught)
      if (detail === 'context_changed') setContextChanged(true)
      else setDecisionError(detail)
    } finally {
      setBusy(null)
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await api.tombstone(caseId, pendingDelete.entry_id)
      setPendingDelete(null)
      // The previous shown_context_ref is no longer valid (docs/api.md §5).
      await load()
    } catch (caught) {
      setLoadError(detailOf(caught))
      setPendingDelete(null)
    } finally {
      setDeleting(false)
    }
  }

  if (!context) {
    return (
      <section className="staff-page">
        {loadError ? (
          <Alert variant="error" title="טעינת הפנייה נכשלה">
            <span className="mono">{loadError}</span>
          </Alert>
        ) : (
          <p className="page-loading" role="status">
            טוען…
          </p>
        )}
      </section>
    )
  }

  const groups = groupByKind(context.data)

  return (
    <section className="staff-page review-page">
      <header className="page-head">
        <h1 className="page-h">
          פנייה <span className="mono">{context.case_id}</span>
        </h1>
        <div className="case-facts">
          <span>
            מטופל: <span className="mono">{context.patient_id}</span>
          </span>
          <StatusPill state={context.state} />
          <span>
            הסלמה: {escalationLabel(context.escalation_kind)}{' '}
            {context.escalation_kind && <span className="state">{context.escalation_kind}</span>}
          </span>
          {context.escalated_from_state && (
            <span>
              ממצב <span className="state">{context.escalated_from_state}</span>
            </span>
          )}
        </div>
        {context.reasons.length > 0 && (
          <ul className="reasons">
            {context.reasons.map((entry) => (
              <li className="mono" key={entry}>
                {entry}
              </li>
            ))}
          </ul>
        )}
      </header>

      {loadError && (
        <Alert variant="error" title="רענון ההקשר נכשל">
          <span className="mono">{loadError}</span>
        </Alert>
      )}

      <div className="review-layout">
        <section className="card col" aria-labelledby="col-data">
          <h2 className="col-h" id="col-data">
            תוכן הפנייה
          </h2>
          <p className="col-note">התוכן נשמר ב-Data Log בלבד. יומן הביקורת שומר רק את ה-hash.</p>
          {groups.length === 0 ? (
            <p className="empty-note">אין תוכן שמור לפנייה הזו.</p>
          ) : (
            groups.map(([kind, entries]) => (
              <div className="data-group" key={kind}>
                <h3 className="data-h">{dataKindLabel(kind)}</h3>
                {entries.map((entry) => (
                  <article className="data-item" key={entry.entry_id}>
                    <p className="data-content">{entry.content}</p>
                    <p className="data-meta">
                      <span className="mono">{entry.entry_id}</span>
                      <span>{formatDateTime(entry.created_at)}</span>
                    </p>
                    <p className="data-hash mono" dir="ltr" title={entry.content_hash}>
                      {entry.content_hash.slice(0, 16)}…
                    </p>
                    <Button variant="danger" onClick={() => setPendingDelete(entry)}>
                      מחיקה לפי בקשת מטופל
                    </Button>
                  </article>
                ))}
              </div>
            ))
          )}
        </section>

        <section className="card col" aria-labelledby="col-audit">
          <h2 className="col-h" id="col-audit">
            יומן הביקורת
          </h2>
          <p className="col-note">כל הרשומות של הפנייה, מהישנה לחדשה.</p>
          <AuditTimeline rows={context.trace} />
        </section>

        <section className="card col" aria-labelledby="col-decision">
          <h2 className="col-h" id="col-decision">
            הכרעה
          </h2>
          {allowed.length === 0 ? (
            <Alert variant="info" title="הפנייה אינה ממתינה להכרעה">
              המסך מציג את ההקשר בלבד. פניות להכרעה מופיעות בתור ההסלמות.
            </Alert>
          ) : (
            <>
              <p className="col-note">
                ההכרעה נשלחת עם ההקשר שמוצג כאן: <span className="mono">{context.shown_context_ref}</span>
              </p>
              <form
                className="form decision-form"
                onSubmit={(event) => {
                  event.preventDefault()
                }}
              >
                <TextField
                  multiline
                  label="סיבת ההכרעה"
                  value={reason}
                  maxLength={2000}
                  counter
                  error={reasonError ?? undefined}
                  hint="הסיבה נשמרת על רשומת האישור (§12.5)."
                  onChange={(event) => setReason(event.target.value)}
                />

                {required.includes('verified_identity_ref') && (
                  <TextField
                    label={REQUIRED_FIELD_LABELS.verified_identity_ref}
                    value={identityRef}
                    maxLength={200}
                    hint={REQUIRED_FIELD_HINTS.verified_identity_ref}
                    onChange={(event) => setIdentityRef(event.target.value)}
                  />
                )}
                {required.includes('patient_deadline') && (
                  <TextField
                    label={REQUIRED_FIELD_LABELS.patient_deadline}
                    type="datetime-local"
                    dir="ltr"
                    value={deadline}
                    hint={REQUIRED_FIELD_HINTS.patient_deadline}
                    onChange={(event) => setDeadline(event.target.value)}
                  />
                )}

                {fieldError && (
                  <Alert variant="error" title="חסר שדה חובה">
                    {fieldError}
                  </Alert>
                )}

                <div className="decision-actions">
                  {allowed.includes('approve') && (
                    <Button
                      variant="primary"
                      busy={busy === 'approve'}
                      disabled={busy !== null}
                      onClick={() => void submit('approve')}
                    >
                      {DECISION_LABELS.approve}
                    </Button>
                  )}
                  {allowed.includes('resolve') && (
                    <Button
                      variant="secondary"
                      busy={busy === 'resolve'}
                      disabled={busy !== null}
                      onClick={() => void submit('resolve')}
                    >
                      {DECISION_LABELS.resolve}
                    </Button>
                  )}
                  {allowed.includes('reject') && (
                    <Button
                      variant="danger"
                      busy={busy === 'reject'}
                      disabled={busy !== null}
                      onClick={() => void submit('reject')}
                    >
                      {DECISION_LABELS.reject}
                    </Button>
                  )}
                </div>
              </form>

              {contextChanged && (
                <Alert variant="error" title="ההקשר השתנה">
                  <p>ההקשר שהוצג כבר אינו העדכני. רעננו אותו, קראו שוב את התוכן והכריעו על המצב הנוכחי.</p>
                  <Button variant="secondary" onClick={() => void refreshContext()}>
                    רענון הקשר
                  </Button>
                </Alert>
              )}
              {decisionError && (
                <Alert variant="error" title="ההכרעה נדחתה">
                  <span className="mono">{decisionError}</span>
                </Alert>
              )}
            </>
          )}
        </section>
      </div>

      {pendingDelete && (
        <div className="dialog-scrim">
          <div className="dialog card" role="dialog" aria-modal="true" aria-labelledby="del-h">
            <h2 className="col-h" id="del-h">
              מחיקת תוכן לפי בקשת מטופל
            </h2>
            <p className="col-note">
              התוכן יימחק מה-Data Log ותישאר רשומת מצבה (tombstone). יומן הביקורת אינו משתנה, וההקשר ייטען מחדש.
            </p>
            <p className="data-content">{pendingDelete.content}</p>
            <div className="dialog-actions">
              <Button variant="danger" busy={deleting} onClick={() => void confirmDelete()}>
                מחיקה
              </Button>
              <Button variant="quiet" disabled={deleting} onClick={() => setPendingDelete(null)}>
                ביטול
              </Button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
