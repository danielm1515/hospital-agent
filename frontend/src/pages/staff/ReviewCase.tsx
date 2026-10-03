import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import * as api from '../../api/client'
import type { DataLogEntry, Decision, DecisionBody, MessageTemplate, ReviewContext, ReviewItem } from '../../api/types'
import { Alert } from '../../components/Alert'
import { AppointmentsPanel } from '../../components/AppointmentsPanel'
import { Button } from '../../components/Button'
import { Loading } from '../../components/Loading'
import { StatusPill } from '../../components/StatusPill'
import { Tabs } from '../../components/Tabs'
import { TextField } from '../../components/TextField'
import { useAuth } from '../../auth/AuthContext'
import { AuditTimeline } from './AuditTimeline'
import { CaseAppointmentFacts } from './CaseAppointmentFacts'
import { ClinicalAnswer } from './ClinicalAnswer'
import { MessagePicker, PatientRequest, toMessageBody } from './PatientRequest'
import type { MessageChoice } from './PatientRequest'
import {
  DECISION_LABELS,
  REQUIRED_FIELD_HINTS,
  REQUIRED_FIELD_LABELS,
  dataKindLabel,
  detailOf,
  escalationLabel,
  formatDateTime,
  groupByKind,
  requestErrorLabel,
  stateLabel,
  toIsoWithOffset,
} from './labels'

/** The case's tabs, in order; the address keeps the open one (`?tab=audit`). */
const CASE_TABS = ['decision', 'data', 'appointment', 'audit'] as const
type CaseTab = (typeof CASE_TABS)[number]

/**
 * One case in the review queue (`docs/api.md` §5), in four tabs - the decision form, the Data
 * Log content, the appointment, and the Audit trace - under a compact header.
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
  const { user } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()

  const [context, setContext] = useState<ReviewContext | null>(null)
  const [item, setItem] = useState<ReviewItem | null>(null)
  // Fix round 1 (M2): `item` alone can't tell "not fetched yet" from "fetched, no decision
  // waits here" - both leave `item` at its initial `null`. Without this, the decision panel
  // briefly showed "not awaiting a decision" while `getReviewItem` was still in flight.
  const [itemLoaded, setItemLoaded] = useState(false)
  // Fix round 3: kept separate from `loadError` (which is `getContext`'s alone) so a
  // `getReviewItem` failure never renders under the "רענון ההקשר נכשל" title - that title
  // is specifically about the context, and a first load never refreshed anything yet.
  const [itemError, setItemError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [templates, setTemplates] = useState<MessageTemplate[]>([])

  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [identityRef, setIdentityRef] = useState('')
  const [deadline, setDeadline] = useState('')
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [decisionError, setDecisionError] = useState<string | null>(null)
  const [contextChanged, setContextChanged] = useState(false)
  const [busy, setBusy] = useState<Decision | null>(null)
  const [closing, setClosing] = useState<MessageChoice>({ mode: 'none' })

  const [pendingDelete, setPendingDelete] = useState<DataLogEntry | null>(null)
  const [deleting, setDeleting] = useState(false)

  // Fix round 2 (I1, a regression from M2): `itemLoaded` is never reset back to `false`
  // here - it is only ever false before the very first load completes. A refresh
  // (`refreshContext`, or the tombstone flow) calls `load()` again, and if its own
  // `getContext` fails, the decision panel must keep showing whatever it already had
  // (the form, or "not awaiting a decision"), not fall back to a loader forever. The
  // whole body is wrapped in one try/finally so `itemLoaded` becomes (or stays) `true`
  // no matter which of the two calls below fails, and `load()` reports whether it
  // actually succeeded so `refreshContext` knows whether to consider itself resolved.
  const load = useCallback(async (): Promise<boolean> => {
    let ok = true
    try {
      try {
        const fresh = await api.getContext(caseId)
        setContext(fresh)
        setLoadError(null)
      } catch (caught) {
        setLoadError(detailOf(caught))
        return false
      }
      // A case not (or no longer) in AwaitingHumanReview is 404 not_in_review - not a load
      // failure, just no decision to offer (staff-fixes design Task 3: one queue item,
      // instead of fetching the whole queue just to find this case's row).
      try {
        setItem(await api.getReviewItem(caseId))
        setItemError(null)
      } catch (caught) {
        if (caught instanceof api.ApiError && caught.status === 404) {
          setItem(null)
          setItemError(null)
        } else {
          // Fix round 3: `itemError`, not `loadError` - this is `getReviewItem`'s own
          // failure, so it must never render under the context-refresh title, and it
          // must not be confused with a confirmed 404 (no decision here, a fine outcome).
          setItemError(detailOf(caught))
          ok = false
        }
      }
      return ok
    } finally {
      setItemLoaded(true)
    }
  }, [caseId])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    api.getMessageTemplates().then(setTemplates).catch(() => setTemplates([]))
  }, [])

  const refreshContext = useCallback(async () => {
    setDecisionError(null)
    // Fix round 2 (I1): `contextChanged` (and its "רענון הקשר" button) stays up until the
    // refresh actually succeeds - a refresh that itself fails must not silently drop the
    // only affordance to try again.
    if (await load()) setContextChanged(false)
  }, [load])

  // Memoised on the route's case id alone (not on `context`, which is a fresh object on
  // every refresh), so `AppointmentsPanel` - whose own effect loads once on mount - never
  // sees a new `load` prop from a context refresh and never refetches because of one.
  const loadAppointments = useCallback(
    (from: Date, to: Date) => api.listCaseAppointments(caseId, from, to),
    [caseId],
  )

  const allowed = item?.allowed_decisions ?? []
  const required = item?.required_fields ?? []

  // No tab in the address: the decision when one is awaited (or still loading), else the content.
  const asked = searchParams.get('tab')
  const tab: CaseTab = CASE_TABS.includes(asked as CaseTab)
    ? (asked as CaseTab)
    : itemLoaded && !itemError && allowed.length === 0
      ? 'data'
      : 'decision'
  const selectTab = (next: string) =>
    setSearchParams(
      (params) => {
        params.set('tab', next)
        return params
      },
      { replace: true },
    )

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

    if (decision === 'resolve' || decision === 'reject') {
      const message = toMessageBody(closing)
      if (message !== undefined) body.message = message
    }

    setBusy(decision)
    try {
      const result = await api.decide(caseId, body)
      navigate('/staff', {
        replace: true,
        state: {
          title: 'ההכרעה נשמרה',
          notice: `הפנייה ${result.case_id} עברה למצב ${stateLabel(result.state)} (${result.state}).`,
        },
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
          <Loading />
        )}
      </section>
    )
  }

  const groups = groupByKind(context.data)
  const latestRequest = [...context.data].reverse().find((entry) => entry.kind === 'request_text' && entry.content)

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
          <span>
            מצב: {stateLabel(context.state)} <StatusPill state={context.state} />
          </span>
          <span>
            הסלמה: {escalationLabel(context.escalation_kind)}{' '}
            {context.escalation_kind && <span className="state">{context.escalation_kind}</span>}
          </span>
          {context.escalated_from_state && (
            <span>
              ממצב {stateLabel(context.escalated_from_state)}{' '}
              <span className="state">{context.escalated_from_state}</span>
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

      <Tabs
        label="פרטי הפנייה"
        idPrefix="case"
        selected={tab}
        onSelect={selectTab}
        tabs={[
          {
            id: 'decision',
            label: 'הכרעה',
            content: (
              <section className="card col tab-card">
                {latestRequest && (
                  <div className="request-brief">
                    <h2 className="col-sub">הפנייה</h2>
                    <p className="data-content">{latestRequest.content}</p>
                    <Button variant="quiet" onClick={() => selectTab('data')}>
                      לתוכן המלא
                    </Button>
                  </div>
                )}
              {item?.escalation_kind === 'MedicalQuestion' && (
                <ClinicalAnswer
                  caseId={caseId}
                  shownContextRef={context.shown_context_ref}
                  role={user?.role ?? 'admin_staff'}
                  onAnswered={() =>
                    navigate('/staff', {
                      replace: true,
                      state: {
                        title: 'התשובה נשלחה',
                        notice: `נשלחה תשובה למטופל בפנייה ${caseId}, והפנייה נסגרה.`,
                      },
                    })
                  }
                  onContextChanged={() => void refreshContext()}
                />
              )}
              {allowed.length > 0 && (
                <PatientRequest
                  caseId={caseId}
                  shownContextRef={context.shown_context_ref}
                  role={user?.role ?? 'admin_staff'}
                  templates={templates}
                  allowsApprove={allowed.includes('approve')}
                  onSent={() =>
                    navigate('/staff', {
                      replace: true,
                      state: { title: 'הבקשה נשלחה', notice: `נשלחה בקשה למטופל בפנייה ${caseId}.` },
                    })
                  }
                  onContextChanged={() => void refreshContext()}
                />
              )}
              {!itemLoaded ? (
                <Loading size="inline" />
              ) : itemError && item === null ? (
                // Fix round 3: the item fetch itself failed and there is no reliable value to
                // fall back on (never a confirmed 404) - "not awaiting a decision" would be a
                // guess dressed as a fact, and a first load never refreshed anything to blame.
                <Alert variant="error" title="טעינת הפנייה נכשלה">
                  <span className="mono">{itemError}</span>
                </Alert>
              ) : allowed.length === 0 ? (
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
                      label="סיבת ההכרעה (פנימית)"
                      value={reason}
                      maxLength={2000}
                      counter
                      error={reasonError ?? undefined}
                      hint="נשמרת על רשומת האישור וביומן הביקורת (§12.5). אינה נשלחת למטופל: מסירת תוכן רפואי מחייבת ContentApproval של איש צוות קליני."
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

                    <div className="closing-message" role="group" aria-label="הודעת סיום למטופל">
                      <h3 className="col-sub">הודעת סיום למטופל (אופציונלי)</h3>
                      <p className="col-note">נשלחת רק עם סגירה או דחייה.</p>
                      <MessagePicker
                        purpose="closing"
                        role={user?.role ?? 'admin_staff'}
                        templates={templates}
                        value={closing}
                        onChange={setClosing}
                        allowNone
                      />
                    </div>

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
                      {requestErrorLabel(decisionError)} <span className="mono">{decisionError}</span>
                    </Alert>
                  )}
                </>
              )}
              </section>
            ),
          },
          {
            id: 'data',
            label: 'תוכן הפנייה',
            count: context.data.length,
            content: (
              <section className="card col tab-card">
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
            ),
          },
          {
            id: 'appointment',
            label: 'תור ומסמכים',
            content: (
              <div className="tab-stack">
                <div className="fact-groups">
                  <CaseAppointmentFacts facts={context} />
                </div>
                <AppointmentsPanel
                  key={caseId}
                  audience="staff"
                  load={loadAppointments}
                  loadInstruction={api.getStaffInstruction}
                />
              </div>
            ),
          },
          {
            id: 'audit',
            label: 'יומן ביקורת',
            count: context.trace.length,
            content: (
              <section className="card col tab-card">
                <AuditTimeline rows={context.trace} context={context} label="יומן הביקורת" />
              </section>
            ),
          },
        ]}
      />

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
