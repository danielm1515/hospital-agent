import { useEffect, useState } from 'react'
import * as api from '../../api/client'
import type { CaseDetail, CaseSummary, ReviewContext, State } from '../../api/types'
import { STATES } from '../../api/types'
import { Alert } from '../../components/Alert'
import { StatusPill } from '../../components/StatusPill'
import { AuditTimeline } from './AuditTimeline'
import { PatientThread } from './PatientThread'
import {
  detailOf,
  escalationLabel,
  formatDateTime,
  intentLabel,
  safetyLabel,
  stateLabel,
} from './labels'

/**
 * The Case Monitor (§1), behind staff authentication: every case from
 * `GET /api/staff/cases`, filtered by State, with the case detail, the correspondence
 * and the Audit trace of a row that is expanded. The list route carries only id, State,
 * escalation kind and time, so patient, intent and safety come from
 * `GET /api/staff/cases/{id}` per row.
 *
 * An expanded row reads `GET /api/staff/cases/{id}/context`, which answers for a case in
 * any State (`docs/api.md` §5) and carries the Data Log and the trace together. Nothing
 * here decides anything, so its `shown_context_ref` is not used - that binds a decision,
 * and decisions are made on the review screen.
 */
export function CaseMonitor() {
  const [filter, setFilter] = useState<State | ''>('')
  const [rows, setRows] = useState<CaseSummary[] | null>(null)
  const [details, setDetails] = useState<Record<string, CaseDetail>>({})
  const [error, setError] = useState<string | null>(null)

  const [expanded, setExpanded] = useState<string | null>(null)
  const [contexts, setContexts] = useState<Record<string, ReviewContext>>({})
  const [contextErrors, setContextErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    let cancelled = false
    async function load() {
      setRows(null)
      setError(null)
      try {
        const list = await api.listCases(filter === '' ? undefined : filter)
        if (cancelled) return
        setRows(list)
        const loaded = await Promise.all(
          list.map(async (row) => {
            try {
              return await api.getCase(row.case_id)
            } catch {
              return null
            }
          }),
        )
        if (cancelled) return
        const byId: Record<string, CaseDetail> = {}
        for (const detail of loaded) {
          if (detail) byId[detail.case_id] = detail
        }
        setDetails(byId)
      } catch (caught) {
        if (!cancelled) setError(detailOf(caught))
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [filter])

  async function toggle(caseId: string) {
    if (expanded === caseId) {
      setExpanded(null)
      return
    }
    setExpanded(caseId)
    if (contexts[caseId]) return
    try {
      const context = await api.getContext(caseId)
      setContexts((previous) => ({ ...previous, [caseId]: context }))
    } catch (caught) {
      setContextErrors((previous) => ({ ...previous, [caseId]: detailOf(caught) }))
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

      <div className="field filter-field">
        <label className="label" htmlFor="state-filter">
          סינון לפי State
        </label>
        <div className="control">
          <select
            id="state-filter"
            className="input"
            value={filter}
            onChange={(event) => setFilter(event.target.value as State | '')}
          >
            <option value="">כל המצבים</option>
            {STATES.map((state) => (
              <option key={state} value={state}>
                {stateLabel(state)} ({state})
              </option>
            ))}
          </select>
        </div>
      </div>

      {error ? (
        <Alert variant="error" title="טעינת הפניות נכשלה">
          <span className="mono">{error}</span>
        </Alert>
      ) : rows === null ? (
        <p className="page-loading" role="status">
          טוען…
        </p>
      ) : rows.length === 0 ? (
        <Alert variant="info" title="אין פניות להצגה">
          לא נמצאו פניות במצב שנבחר.
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
                const detail = details[row.case_id]
                const open = expanded === row.case_id
                return (
                  <ExpandableRow
                    key={row.case_id}
                    row={row}
                    detail={detail}
                    open={open}
                    context={contexts[row.case_id]}
                    contextError={contextErrors[row.case_id]}
                    onToggle={() => void toggle(row.case_id)}
                  />
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

interface RowProps {
  row: CaseSummary
  detail?: CaseDetail
  open: boolean
  context?: ReviewContext
  contextError?: string
  onToggle: () => void
}

function ExpandableRow({ row, detail, open, context, contextError, onToggle }: RowProps) {
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
        <td className="mono">{detail?.patient_id ?? '—'}</td>
        <td>
          <StatusPill state={row.state} />
          <span className="cell-sub">{stateLabel(row.state)}</span>
        </td>
        <td>
          <Coded label={intentLabel(detail?.intent)} code={detail?.intent} />
        </td>
        <td>
          <Coded label={safetyLabel(detail?.safety_level)} code={detail?.safety_level} />
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
              {detail ? <CaseFacts detail={detail} /> : <p className="empty-note">פרטי הפנייה לא נטענו.</p>}

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
                <p className="page-loading" role="status">
                  טוען…
                </p>
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
          <Fact label="נדרשים" code="required_documents" mono value={required.join(', ') || '—'} />
          <Fact label="שהתקבלו" code="held_documents" mono value={detail.held_documents.join(', ') || '—'} />
          <Fact label="חסרים" value={missing.length > 0 ? missing.join(', ') : 'אין'} mono={missing.length > 0} />
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

function Fact({ label, code, value, mono = false }: { label: string; code?: string; value: string; mono?: boolean }) {
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
