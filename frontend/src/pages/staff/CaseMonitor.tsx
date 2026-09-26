import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import * as api from '../../api/client'
import type { CaseDetail, CaseSummary, EscalationKind, ReviewContext, StateGroup } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { Loading } from '../../components/Loading'
import { StatusPill } from '../../components/StatusPill'
import { AuditTimeline } from './AuditTimeline'
import { PatientThread } from './PatientThread'
import {
  DOCUMENT_LABELS,
  ESCALATION_LABELS,
  STATE_GROUP_LABELS,
  STATE_GROUP_ORDER,
  detailOf,
  escalationLabel,
  formatDateTime,
  intentLabel,
  safetyLabel,
  stateLabel,
} from './labels'

const ESCALATION_KIND_OPTIONS = Object.keys(ESCALATION_LABELS) as EscalationKind[]

/**
 * The Case Monitor (§1), behind staff authentication: every case from
 * `GET /api/staff/cases` (staff-fixes design Task 3), filtered by State, with the case
 * detail, the correspondence and the Audit trace of a row that is expanded. The list
 * route now carries every column the table shows - id, patient, State, intent, safety
 * level, escalation kind and time - so a row renders straight from the list with no
 * per-row follow-up call; only expanding a row fetches its plan/document detail
 * (`GET /api/staff/cases/{id}`) and its correspondence (`GET /api/staff/cases/{id}/context`).
 * The list is keyset-paginated: "טעינת עוד" asks for the page after `next_cursor`.
 *
 * An expanded row's context answers for a case in any State (`docs/api.md` §5) and
 * carries the Data Log and the trace together. Nothing here decides anything, so its
 * `shown_context_ref` is not used - that binds a decision, and decisions are made on the
 * review screen.
 */
export function CaseMonitor() {
  const [group, setGroup] = useState<StateGroup | ''>('')
  const [escalationKind, setEscalationKind] = useState<EscalationKind | ''>('')
  const [rows, setRows] = useState<CaseSummary[] | null>(null)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null)

  const [details, setDetails] = useState<Record<string, CaseDetail>>({})
  const [detailErrors, setDetailErrors] = useState<Record<string, string>>({})
  const [expanded, setExpanded] = useState<string | null>(null)
  const [contexts, setContexts] = useState<Record<string, ReviewContext>>({})
  const [contextErrors, setContextErrors] = useState<Record<string, string>>({})

  // Fix round 1 (M6): a generation counter, bumped on every filter-driven reload, so a
  // "load more" response that arrives after the filter has since changed is dropped
  // instead of appending stale rows onto a list that no longer matches the filter shown.
  const generationRef = useRef(0)

  useEffect(() => {
    let cancelled = false
    const generation = ++generationRef.current
    async function load() {
      setRows(null)
      setNextCursor(null)
      setError(null)
      setLoadMoreError(null)
      // A reload supersedes any "load more" in flight - its own result (if it ever arrives)
      // is now guarded out below, so the button must not stay busy waiting for it (fix
      // round 2, N1).
      setLoadingMore(false)
      setExpanded(null)
      // A filter change makes every previously loaded detail/context stale (a different
      // row set, possibly the same case id reused across filters is not a concern here,
      // but a stale cache would still show yesterday's content on next expand).
      setDetails({})
      setDetailErrors({})
      setContexts({})
      setContextErrors({})
      try {
        const page = await api.listCases({
          group: group === '' ? undefined : group,
          escalationKind: escalationKind === '' ? undefined : escalationKind,
        })
        if (cancelled || generation !== generationRef.current) return
        setRows(page.items)
        setNextCursor(page.next_cursor)
      } catch (caught) {
        if (!cancelled && generation === generationRef.current) setError(detailOf(caught))
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [group, escalationKind])

  async function loadMore() {
    if (nextCursor === null) return
    const generation = generationRef.current
    setLoadingMore(true)
    setLoadMoreError(null)
    try {
      const page = await api.listCases({
        group: group === '' ? undefined : group,
        escalationKind: escalationKind === '' ? undefined : escalationKind,
        cursor: nextCursor,
      })
      if (generation !== generationRef.current) return // the filter changed meanwhile
      setRows((previous) => [...(previous ?? []), ...page.items])
      setNextCursor(page.next_cursor)
    } catch (caught) {
      if (generation === generationRef.current) setLoadMoreError(detailOf(caught))
    } finally {
      // Unconditional (fix round 2, N1): a generation that has since moved on still means
      // this request is over - only the data write above stays guarded, not the busy flag,
      // or the button would stay busy forever after a race with a filter change.
      setLoadingMore(false)
    }
  }

  function selectGroup(value: StateGroup | '') {
    setGroup(value)
    // escalation_kind is only accepted with group=staff (docs/api.md §5); leaving it set
    // while switching to another group would otherwise ask the API for an invalid_filter
    // combination on the very next load.
    if (value !== 'staff') setEscalationKind('')
  }

  async function toggle(caseId: string) {
    if (expanded === caseId) {
      setExpanded(null)
      return
    }
    setExpanded(caseId)
    if (!details[caseId] && !detailErrors[caseId]) {
      try {
        const detail = await api.getCase(caseId)
        setDetails((previous) => ({ ...previous, [caseId]: detail }))
      } catch (caught) {
        setDetailErrors((previous) => ({ ...previous, [caseId]: detailOf(caught) }))
      }
    }
    if (!contexts[caseId] && !contextErrors[caseId]) {
      try {
        const context = await api.getContext(caseId)
        setContexts((previous) => ({ ...previous, [caseId]: context }))
      } catch (caught) {
        setContextErrors((previous) => ({ ...previous, [caseId]: detailOf(caught) }))
      }
    }
  }

  return (
    <section className="staff-page">
      <header className="page-head">
        <h1 className="page-h">כל הפניות</h1>
        <p className="lede">
          מצב כל הפניות במערכת. לחיצה על שורה פותחת את פרטי הפנייה ואת יומן הביקורת שלה. לצד כל תווית בעברית
          מופיע שם השדה או הקוד שה־API החזיר, כי זה מה שמופיע באפיון וביומן.
        </p>
      </header>

      <div className="filter-row">
        <div className="field filter-field">
          <label className="label" htmlFor="group-filter">
            סינון לפי קבוצה
          </label>
          <div className="control">
            <select
              id="group-filter"
              className="input"
              value={group}
              onChange={(event) => selectGroup(event.target.value as StateGroup | '')}
            >
              <option value="">הכול</option>
              {STATE_GROUP_ORDER.map((option) => (
                <option key={option} value={option}>
                  {STATE_GROUP_LABELS[option]}
                </option>
              ))}
            </select>
          </div>
        </div>

        {group === 'staff' && (
          <div className="field filter-field">
            <label className="label" htmlFor="escalation-kind-filter">
              סינון לפי סוג הסלמה (escalation_kind)
            </label>
            <div className="control">
              <select
                id="escalation-kind-filter"
                className="input"
                value={escalationKind}
                onChange={(event) => setEscalationKind(event.target.value as EscalationKind | '')}
              >
                <option value="">הכול</option>
                {ESCALATION_KIND_OPTIONS.map((kind) => (
                  <option key={kind} value={kind}>
                    {ESCALATION_LABELS[kind]} ({kind})
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}
      </div>

      {error ? (
        <Alert variant="error" title="טעינת הפניות נכשלה">
          <span className="mono">{error}</span>
        </Alert>
      ) : rows === null ? (
        <Loading label="טוען פניות" />
      ) : rows.length === 0 ? (
        <Alert variant="info" title="אין פניות להצגה">
          אין פניות בקבוצה שנבחרה.
        </Alert>
      ) : (
        <div className="table-wrap card">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">פנייה</th>
                <th scope="col">מטופל</th>
                <th scope="col">מצב (State)</th>
                <th scope="col">כוונה (intent)</th>
                <th scope="col">רמת בטיחות</th>
                <th scope="col">הסלמה</th>
                <th scope="col">עדכון אחרון</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const open = expanded === row.case_id
                return (
                  <ExpandableRow
                    key={row.case_id}
                    row={row}
                    detail={details[row.case_id]}
                    detailError={detailErrors[row.case_id]}
                    open={open}
                    context={contexts[row.case_id]}
                    contextError={contextErrors[row.case_id]}
                    onToggle={() => void toggle(row.case_id)}
                  />
                )
              })}
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

interface RowProps {
  row: CaseSummary
  detail?: CaseDetail
  detailError?: string
  open: boolean
  context?: ReviewContext
  contextError?: string
  onToggle: () => void
}

function ExpandableRow({ row, detail, detailError, open, context, contextError, onToggle }: RowProps) {
  return (
    <>
      <tr className="row-link" onClick={onToggle}>
        <td>
          <button
            type="button"
            className="cell-link mono"
            aria-expanded={open}
            onClick={(event) => {
              event.stopPropagation()
              onToggle()
            }}
          >
            {row.case_id}
          </button>
        </td>
        <td className="mono">{row.patient_id}</td>
        <td>
          <StatusPill state={row.state} />
          <span className="cell-sub">{stateLabel(row.state)}</span>
        </td>
        <td>
          <Coded label={intentLabel(row.intent)} code={row.intent} />
        </td>
        <td>
          <Coded label={safetyLabel(row.safety_level)} code={row.safety_level} />
        </td>
        <td>
          <Coded label={escalationLabel(row.escalation_kind)} code={row.escalation_kind} />
        </td>
        <td className="nowrap">{formatDateTime(row.updated_at)}</td>
      </tr>
      {open && (
        <tr className="detail-row">
          <td colSpan={7}>
            <div className="case-detail">
              {detail ? (
                <CaseFacts detail={detail} />
              ) : detailError ? (
                <Alert variant="error" title="טעינת פרטי הפנייה נכשלה">
                  <span className="mono">{detailError}</span>
                </Alert>
              ) : (
                <Loading size="inline" label="טוען פרטים" />
              )}

              {detail?.ordered_steps && detail.ordered_steps.length > 0 && (
                <section className="fact-group">
                  <h3 className="fact-group-h">התוכנית (ordered_steps)</h3>
                  <ol className="plan-steps">
                    {detail.ordered_steps.map((step) => (
                      <li className={planStepClass(step.step, detail.current_step)} key={step.step}>
                        <span className="step-n">{step.step}</span>
                        <span className="mono">{step.action}</span>
                        <span className="step-state">{planStepLabel(step.step, detail.current_step)}</span>
                      </li>
                    ))}
                  </ol>
                </section>
              )}

              {contextError ? (
                <Alert variant="error" title="טעינת תוכן הפנייה נכשלה">
                  <span className="mono">{contextError}</span>
                </Alert>
              ) : context ? (
                <>
                  <section className="fact-group">
                    <h3 className="fact-group-h">התכתובת עם המטופל</h3>
                    <PatientThread entries={context.data} />
                  </section>

                  <h3 className="fact-group-h">יומן הביקורת (Audit)</h3>
                  <AuditTimeline rows={context.trace} label={`יומן הביקורת של ${row.case_id}`} />
                </>
              ) : (
                <Loading size="inline" label="טוען תכתובת" />
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

/** A cell that reads in Hebrew and still shows the code the API returned (§12.3, D-tests). */
function Coded({ label, code }: { label: string; code?: string | null }) {
  if (!code) return <>—</>
  return (
    <>
      {label}
      <span className="cell-sub mono">{code}</span>
    </>
  )
}

/** Where a plan step stands relative to `current_step`; `null` means the plan has not started. */
function planStepClass(step: number, current: number | null): string {
  if (current === null) return 'step-todo'
  if (step < current) return 'step-done'
  return step === current ? 'step-current' : 'step-todo'
}

function planStepLabel(step: number, current: number | null): string {
  if (current === null) return 'טרם'
  if (step < current) return 'בוצע'
  return step === current ? 'השלב הנוכחי' : 'טרם'
}

/**
 * The case as a person reads it: four groups, each fact with a Hebrew label and,
 * beside it, the field name from `docs/api.md` §5 - the raw name stays visible
 * because it is what the spec, the guards and the Audit all use.
 */
function CaseFacts({ detail }: { detail: CaseDetail }) {
  const steps = detail.ordered_steps ?? []
  const required = detail.required_documents ?? []
  const missing = required.filter((documentId) => !detail.held_documents.includes(documentId))
  const currentAction = steps.find((step) => step.step === detail.current_step)?.action

  const stepValue =
    detail.current_step === null
      ? 'טרם התחילה תוכנית'
      : `${detail.current_step}${steps.length > 0 ? ` מתוך ${steps.length}` : ''}${
          currentAction ? ` · ${currentAction}` : ''
        }`

  return (
    <div className="fact-groups">
      <section className="fact-group">
        <h3 className="fact-group-h">התקדמות</h3>
        <dl className="fact-list">
          <Fact label="שלב נוכחי" code="current_step" value={stepValue} />
          <Fact label="ניסיונות בשלב הנוכחי" code="attempt_count" value={String(detail.attempt_count)} />
          <Fact label="מחזור ניסיונות" code="retry_cycle" value={String(detail.retry_cycle)} />
          <Fact label="גרסת המצב" code="state_version" value={String(detail.state_version)} />
          <Fact
            label="חתימת התוכנית"
            code="plan_hash"
            mono
            value={detail.plan_hash ? `${detail.plan_hash.slice(0, 16)}…` : '—'}
          />
        </dl>
      </section>

      <section className="fact-group">
        <h3 className="fact-group-h">מסמכים</h3>
        <dl className="fact-list">
          <Fact label="נדרשים" code="required_documents" value={<DocumentCodes ids={required} empty="—" />} />
          <Fact
            label="שהתקבלו"
            code="held_documents"
            value={<DocumentCodes ids={detail.held_documents} empty="—" />}
          />
          <Fact label="חסרים" value={<DocumentCodes ids={missing} empty="אין" />} />
        </dl>
      </section>

      <section className="fact-group">
        <h3 className="fact-group-h">זהות ומועדים</h3>
        <dl className="fact-list">
          <Fact label="המטופל זוהה" code="identity_verified" value={detail.identity_verified ? 'כן' : 'לא'} />
          <Fact label="מועד היעד למטופל" code="patient_deadline" value={formatDateTime(detail.patient_deadline)} />
          <Fact label="נפתחה" code="created_at" value={formatDateTime(detail.created_at)} />
          <Fact label="עודכנה" code="updated_at" value={formatDateTime(detail.updated_at)} />
        </dl>
      </section>

      <section className="fact-group">
        <h3 className="fact-group-h">הסלמה</h3>
        <dl className="fact-list">
          <Fact
            label="סוג ההסלמה"
            code="escalation_kind"
            value={detail.escalation_kind ? `${escalationLabel(detail.escalation_kind)} (${detail.escalation_kind})` : 'לא הוסלמה'}
          />
          <Fact
            label="הוסלמה ממצב"
            code="escalated_from_state"
            value={
              detail.escalated_from_state
                ? `${stateLabel(detail.escalated_from_state)} (${detail.escalated_from_state})`
                : '—'
            }
          />
        </dl>
      </section>
    </div>
  )
}

function Fact({
  label,
  code,
  value,
  mono = false,
}: {
  label: string
  code?: string
  value: ReactNode
  mono?: boolean
}) {
  return (
    <div className="fact">
      <dt className="fact-k">
        {label}
        {code && <span className="fact-code mono">{code}</span>}
      </dt>
      <dd className={mono ? 'fact-v mono' : 'fact-v'}>{value}</dd>
    </div>
  )
}

/**
 * A required/held/missing document list as isolated codes, each with its Hebrew label
 * beside it - never one joined string, so a Latin code does not pick its own bidi
 * direction inside Hebrew text (CLAUDE.md), the same pattern the patient screen uses
 * (`RequestDetail.tsx`: `span.code[dir=ltr]`). The label is shown only for a code this
 * version knows; an unknown one is shown alone, never invented.
 */
function DocumentCodes({ ids, empty }: { ids: string[]; empty: string }) {
  if (ids.length === 0) return <>{empty}</>
  return (
    <>
      {ids.map((id, index) => (
        <span className="doc-code-item" key={id}>
          {index > 0 && ', '}
          <span className="code" dir="ltr">
            {id}
          </span>
          {DOCUMENT_LABELS[id] && ` ${DOCUMENT_LABELS[id]}`}
        </span>
      ))}
    </>
  )
}
