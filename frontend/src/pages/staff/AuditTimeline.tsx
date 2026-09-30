import type { TraceRow } from '../../api/types'
import {
  PHASE_LABELS,
  actionLabel,
  enginesOf,
  eventLabel,
  guardLabel,
  isEvidence,
  opaRulesOf,
  outcomeLabel,
  phaseOf,
  policyResultLabel,
  reasonLabel,
  recordTypeLabel,
} from './auditLabels'
import type { Phase } from './auditLabels'
import { formatAuditTime, formatDate, gapAfter } from './labels'

/**
 * The Audit trace as a journal (design decision 6: no table/timeline exists in the design
 * set, so it is built from the same tokens and the `.state` mono style).
 *
 * It renders the rows exactly as the API returned them and never adds content of its own:
 * the Audit holds no message or document text (§12.3). What it adds is reading aids - a
 * summary of the gates, policy decisions, blocks and execution attempts; the rows grouped
 * into the phase of the flow they belong to; and a Hebrew label beside every code (the
 * event, the gates each row passed, the policy result, each reason). A code the labels
 * don't know is shown alone.
 *
 * A case's rows are written within a second or two, so each row carries its position in
 * the trace, its time to the millisecond, and the gap from the row before it - otherwise a
 * 35-row trace reads as 35 identical timestamps.
 */

/**
 * What a row did to the State, as a Hebrew sentence. An arrow between two Latin state
 * names reads either way on an RTL line - which of the two came first is exactly what a
 * reader needs, so the words say it instead.
 *
 * `state_before === null` is Initial (`docs/api.md` §5); a row that leaves the State alone
 * (an execution row, a Blocked row) says so rather than pointing at itself.
 */
function Transition({ before, after }: { before: string | null; after: string | null }) {
  if (after === null && before === null) return <span className="audit-timeline-same">ללא שינוי מצב</span>
  if (after === null) {
    return (
      <>
        נשאר במצב <span className="state">{before}</span>
      </>
    )
  }
  if (before === null) {
    return (
      <>
        נפתח במצב <span className="state">{after}</span>
      </>
    )
  }
  if (before === after) {
    return (
      <>
        נשאר במצב <span className="state">{after}</span>
      </>
    )
  }
  return (
    <>
      ממצב <span className="state">{before}</span> למצב <span className="state">{after}</span>
    </>
  )
}

type Tone = 'good' | 'bad' | 'warn' | 'quiet' | 'neutral'

/** The colour a row gets: a block or a failed/denied step is red, a hand-off to staff is amber. */
function toneOf(row: TraceRow): Tone {
  if (['Blocked', 'ExecutionFailed', 'ExecutionUnknown'].includes(row.record_type)) return 'bad'
  if (row.policy_result === 'Deny') return 'bad'
  if (row.event === 'HUMAN_REVIEW_REQUIRED' || row.policy_result === 'RequireHumanReview') return 'warn'
  if (row.event === 'AUDIT_RECORDED') return 'quiet'
  if (row.record_type === 'ExecutionSucceeded' || ['READINESS_PASSED', 'CASE_RESOLVED'].includes(row.event)) {
    return 'good'
  }
  return 'neutral'
}

function guardsOf(row: TraceRow): Array<[string, boolean]> {
  return Object.entries(row.guards ?? {})
}

/** A span of time in Hebrew: milliseconds for a fast trace, days for one that waited on a patient. */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '—'
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(3)} שנ׳`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} דק׳ ${Math.floor(seconds % 60)} שנ׳`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} שע׳ ${minutes % 60} דק׳`
  return `${Math.floor(hours / 24)} ימים ${hours % 24} שע׳`
}

function Summary({ rows }: { rows: TraceRow[] }) {
  const gates = rows.flatMap(guardsOf).filter(([key, value]) => !isEvidence(key) && value).length
  const decisions = rows.filter((row) => row.policy_result)
  const allowed = decisions.filter((row) => row.policy_result === 'Allow').length
  const blocked = rows.filter((row) => row.record_type === 'Blocked').length
  const attempts = rows.filter((row) => row.event === 'TOOL_EXECUTION_STARTED').length
  const succeeded = rows.filter((row) => row.outcome === 'success').length
  const failed = rows.filter((row) => row.outcome === 'failed' || row.outcome === 'unknown').length
  const z3Runs = rows.flatMap(enginesOf).filter((e) => e.engine === 'Z3' && e.tone !== 'neutral').length
  const span = new Date(rows[rows.length - 1].recorded_at).getTime() - new Date(rows[0].recorded_at).getTime()
  const stats: Array<{ label: string; value: string | number; note?: string; tone?: Tone }> = [
    { label: 'שערים שעברו', value: gates, tone: 'good' },
    {
      label: 'החלטות מדיניות (OPA + Prolog)',
      value: decisions.length,
      note: decisions.length ? `${allowed} אושרו · ${decisions.length - allowed} לא אושרו` : undefined,
    },
    { label: 'בדיקות Z3', value: z3Runs },
    { label: 'חסימות', value: blocked, tone: blocked ? 'bad' : undefined },
    {
      label: 'ניסיונות ביצוע',
      value: attempts,
      note: attempts ? `${succeeded} הצליחו · ${failed} נכשלו` : undefined,
      tone: failed ? 'warn' : undefined,
    },
    { label: 'משך כולל', value: formatDuration(span) },
  ]
  return (
    <dl className="audit-summary" aria-label="סיכום יומן המעקב">
      {stats.map((stat) => (
        <div className={`audit-stat${stat.tone ? ` tone-${stat.tone}` : ''}`} key={stat.label}>
          <dt>{stat.label}</dt>
          <dd>{stat.value}</dd>
          {stat.note && <p className="audit-stat-note">{stat.note}</p>}
        </div>
      ))}
    </dl>
  )
}

/** Which engine decided the row (derived in `enginesOf`, from the row alone). */
function Engines({ row }: { row: TraceRow }) {
  const engines = enginesOf(row)
  const rules = opaRulesOf(row)
  if (engines.length === 0) return null
  const passed = rules?.filter((rule) => rule.passed).length ?? 0
  return (
    <div className="audit-engines">
      <div className="audit-gates-group">
        <span className="audit-gates-label">מנועים</span>
        <ul>
          {engines.map((engine) => (
            <li className={`audit-engine tone-${engine.tone}`} key={engine.engine}>
              <span className="audit-engine-name">{engine.engine}</span> {engine.verdict}
            </li>
          ))}
        </ul>
      </div>
      {rules && (
        <details className="audit-opa-rules">
          <summary>
            OPA: {passed}/{rules.length} כללי deny עברו
          </summary>
          <ul>
            {rules.map((rule) => (
              <li className={`audit-gate ${rule.passed ? 'pass' : 'fail'}`} key={rule.code}>
                <span aria-hidden="true">{rule.passed ? '✓' : '✗'}</span> {rule.label}{' '}
                <span className="mono">{rule.code}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

/**
 * What the journal can and cannot show about the engines: the Audit keeps each policy
 * row's folded result, so the per-engine verdict is derived; Datalog runs at build time,
 * not per case; the Temporal Monitor records a violation, never a pass.
 */
function EngineLegend() {
  return (
    <ul className="audit-legend" aria-label="מקרא מנועי ההחלטה">
      <li>
        <b>OPA + Prolog</b> מכריעים יחד כל צעד: אישור רק כששניהם מאשרים.
      </li>
      <li>
        <b>Datalog</b> (<span className="mono">flows.dl</span>) רץ בזמן build ומזין את נתוני המזעור של OPA, לא
        בכל פנייה.
      </li>
      <li>
        <b>Z3</b> רץ בבדיקת המוכנות כשחסר מסמך: UNSAT = בטוח לבקש מהמטופל.
      </li>
      <li>
        <b>Temporal Monitor</b> בודק את T1–T13 בכל מעבר; ביומן מופיעות רק הפרות.
      </li>
    </ul>
  )
}

function Gates({ row }: { row: TraceRow }) {
  const guards = guardsOf(row)
  const gates = guards.filter(([key]) => !isEvidence(key))
  const evidence = guards.filter(([key]) => isEvidence(key))
  if (guards.length === 0) return null
  return (
    <div className="audit-gates">
      {gates.length > 0 && (
        <div className="audit-gates-group">
          <span className="audit-gates-label">שערים</span>
          <ul>
            {gates.map(([key, passed]) => (
              <li className={`audit-gate ${passed ? 'pass' : 'fail'}`} key={key}>
                <span aria-hidden="true">{passed ? '✓' : '✗'}</span> {guardLabel(key)}{' '}
                <span className="mono">{key}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {evidence.length > 0 && (
        <div className="audit-gates-group">
          <span className="audit-gates-label">בדיקות</span>
          <ul>
            {evidence.map(([key, value]) => (
              <li className="audit-gate check" key={key}>
                {guardLabel(key)}: {value ? 'כן' : 'לא'} <span className="mono">{key}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function Item({ row, index, previous }: { row: TraceRow; index: number; previous: TraceRow | null }) {
  const gap = gapAfter(previous?.recorded_at ?? null, row.recorded_at)
  const label = eventLabel(row.event)
  const attempt = row.attempt_number ?? null
  const cycle = row.retry_cycle ?? null
  return (
    <li className={`audit-timeline-item tone-${toneOf(row)}`}>
      <div className="audit-timeline-head">
        <span className="audit-timeline-index mono" aria-hidden="true">
          {index + 1}.
        </span>
        <span className={`audit-timeline-kind kind-${row.record_type}`} title={row.record_type}>
          {recordTypeLabel(row.record_type)}
        </span>
        <span className="audit-timeline-time">
          <time dateTime={row.recorded_at}>{formatAuditTime(row.recorded_at)}</time>
          {gap && <span className="audit-timeline-gap">{gap}</span>}
        </span>
      </div>
      <p className="audit-timeline-event">
        {label !== row.event && <strong className="audit-timeline-title">{label}</strong>}
        <span className="mono">{row.event}</span>
      </p>
      <p className="audit-timeline-states">
        <Transition before={row.state_before} after={row.state_after} />
      </p>
      {row.action && (
        <p className="audit-timeline-meta">
          פעולה: {actionLabel(row.action)} <span className="mono">{row.action}</span>
          {attempt !== null && <span className="audit-attempt"> · ניסיון {attempt}</span>}
          {cycle !== null && cycle > 1 && <span className="audit-attempt"> · מחזור {cycle}</span>}
          {row.outcome && <span className={`audit-badge outcome-${row.outcome}`}>{outcomeLabel(row.outcome)}</span>}
        </p>
      )}
      {row.policy_result && (
        <p className="audit-timeline-meta">
          החלטת מדיניות:{' '}
          <span className={`audit-badge policy-${row.policy_result}`}>{policyResultLabel(row.policy_result)}</span>{' '}
          <span className="mono">{row.policy_result}</span>
        </p>
      )}
      <Engines row={row} />
      <Gates row={row} />
      {row.policy_reasons.length > 0 && (
        <div className="audit-reasons">
          <span className="audit-gates-label">{row.record_type === 'Blocked' ? 'סיבת החסימה' : 'נימוקים'}</span>
          <ul className="reasons">
            {row.policy_reasons.map((reason) => {
              const meaning = reasonLabel(reason)
              return (
                <li key={reason}>
                  <span className="mono">{reason}</span>
                  {meaning && <span className="audit-reason-label">{meaning}</span>}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </li>
  )
}

/** Consecutive rows of the same phase, so the journal reads as the flow's stages in order. */
function segmentsOf(rows: TraceRow[]): Array<{ phase: Phase; start: number; rows: TraceRow[] }> {
  const segments: Array<{ phase: Phase; start: number; rows: TraceRow[] }> = []
  rows.forEach((row, index) => {
    const phase = phaseOf(row.event)
    const last = segments[segments.length - 1]
    if (last && last.phase === phase) last.rows.push(row)
    else segments.push({ phase, start: index, rows: [row] })
  })
  return segments
}

export function AuditTimeline({ rows, label }: { rows: TraceRow[]; label?: string }) {
  if (rows.length === 0) {
    return <p className="empty-note">אין רשומות ביומן הביקורת לפנייה הזו.</p>
  }
  return (
    <div className="audit-journal">
      <Summary rows={rows} />
      <EngineLegend />
      <p className="audit-timeline-day">
        {rows.length} רשומות · {formatDate(rows[0].recorded_at)}
      </p>
      <ol className="audit-timeline" aria-label={label ?? 'יומן הביקורת של הפנייה'}>
        {segmentsOf(rows).map((segment) => (
          <li className={`audit-phase phase-${segment.phase}`} key={rows[segment.start].audit_id}>
            <p className="audit-phase-title">{PHASE_LABELS[segment.phase]}</p>
            <ol className="audit-phase-rows">
              {segment.rows.map((row, offset) => {
                const index = segment.start + offset
                return (
                  <Item key={row.audit_id} row={row} index={index} previous={index > 0 ? rows[index - 1] : null} />
                )
              })}
            </ol>
          </li>
        ))}
      </ol>
    </div>
  )
}
