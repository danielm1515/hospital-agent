import { useEffect, useState } from 'react'
import * as api from '../../api/client'
import type { AuditRecord, CaseDetail, CaseSummary, State } from '../../api/types'
import { STATES } from '../../api/types'
import { Alert } from '../../components/Alert'
import { StatusPill } from '../../components/StatusPill'
import { AuditTimeline } from './AuditTimeline'
import { detailOf, escalationLabel, formatDateTime } from './labels'

/**
 * The Case Monitor (§1), behind staff authentication: every case from
 * `GET /api/staff/cases`, filtered by State, with the case detail and the Audit
 * trace of a row that is expanded. The list route carries only id, State,
 * escalation kind and time, so patient, intent and safety come from
 * `GET /api/staff/cases/{id}` per row.
 */
export function CaseMonitor() {
  const [filter, setFilter] = useState<State | ''>('')
  const [rows, setRows] = useState<CaseSummary[] | null>(null)
  const [details, setDetails] = useState<Record<string, CaseDetail>>({})
  const [error, setError] = useState<string | null>(null)

  const [expanded, setExpanded] = useState<string | null>(null)
  const [audits, setAudits] = useState<Record<string, AuditRecord[]>>({})
  const [auditErrors, setAuditErrors] = useState<Record<string, string>>({})

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
    if (audits[caseId]) return
    try {
      const trace = await api.getAudit(caseId)
      setAudits((previous) => ({ ...previous, [caseId]: trace }))
    } catch (caught) {
      setAuditErrors((previous) => ({ ...previous, [caseId]: detailOf(caught) }))
    }
  }

  return (
    <section className="staff-page">
      <header className="page-head">
        <h1 className="page-h">כל הפניות</h1>
        <p className="lede">מצב כל הפניות במערכת. לחיצה על שורה פותחת את פרטי הפנייה ואת יומן הביקורת שלה.</p>
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
                {state}
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
                <th scope="col">State</th>
                <th scope="col">כוונה</th>
                <th scope="col">רמת בטיחות</th>
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
                    trace={audits[row.case_id]}
                    traceError={auditErrors[row.case_id]}
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
  trace?: AuditRecord[]
  traceError?: string
  onToggle: () => void
}

function ExpandableRow({ row, detail, open, trace, traceError, onToggle }: RowProps) {
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
        </td>
        <td>{detail?.intent ?? '—'}</td>
        <td>{detail?.safety_level ?? '—'}</td>
        <td className="nowrap">{formatDateTime(row.updated_at)}</td>
      </tr>
      {open && (
        <tr className="detail-row">
          <td colSpan={6}>
            <div className="case-detail">
              <div className="detail-facts">
                {detail ? (
                  <>
                    <Fact label="state_version" value={String(detail.state_version)} mono />
                    <Fact label="current_step" value={detail.current_step === null ? '—' : String(detail.current_step)} />
                    <Fact label="retry_cycle" value={String(detail.retry_cycle)} />
                    <Fact label="attempt_count" value={String(detail.attempt_count)} />
                    <Fact label="identity_verified" value={detail.identity_verified ? 'כן' : 'לא'} />
                    <Fact label="plan_hash" value={detail.plan_hash ? `${detail.plan_hash.slice(0, 16)}…` : '—'} mono />
                    <Fact label="required_documents" value={(detail.required_documents ?? []).join(', ') || '—'} mono />
                    <Fact label="held_documents" value={detail.held_documents.join(', ') || '—'} mono />
                    <Fact label="patient_deadline" value={formatDateTime(detail.patient_deadline)} />
                    <Fact label="created_at" value={formatDateTime(detail.created_at)} />
                    <Fact
                      label="escalation_kind"
                      value={
                        detail.escalation_kind
                          ? `${escalationLabel(detail.escalation_kind)} (${detail.escalation_kind})`
                          : '—'
                      }
                    />
                    <Fact label="escalated_from_state" value={detail.escalated_from_state ?? '—'} mono />
                  </>
                ) : (
                  <p className="empty-note">פרטי הפנייה לא נטענו.</p>
                )}
              </div>

              {detail?.ordered_steps && detail.ordered_steps.length > 0 && (
                <ol className="plan-steps">
                  {detail.ordered_steps.map((step) => (
                    <li className="mono" key={step.step}>
                      <span className="step-n">{step.step}</span>
                      <span>{step.action}</span>
                    </li>
                  ))}
                </ol>
              )}

              {traceError ? (
                <Alert variant="error" title="טעינת יומן הביקורת נכשלה">
                  <span className="mono">{traceError}</span>
                </Alert>
              ) : trace ? (
                <AuditTimeline rows={trace} label={`יומן הביקורת של ${row.case_id}`} />
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

function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <p className="fact">
      <span className="fact-k mono">{label}</span>
      <span className={mono ? 'fact-v mono' : 'fact-v'}>{value}</span>
    </p>
  )
}
