import type { LlmCall, TraceRow, UploadAttempt } from '../../api/types'
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
  uploadOutcomeLabel,
  uploadReasonLabel,
} from './auditLabels'
import type { EngineVerdict, Phase } from './auditLabels'
import { datalogOf, prologChecksOf, z3ModelOf } from './engineRules'
import type { RuleList, RuleStatus } from './engineRules'
import { formatAuditTime, formatDate, gapAfter, intentLabel, safetyLabel } from './labels'
import { LLM_CALL_LABELS } from './metricsLabels'

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

function Summary({ rows, calls, uploads }: { rows: TraceRow[]; calls: LlmCall[]; uploads: UploadAttempt[] }) {
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
    {
      label: 'קריאות LLM',
      value: calls.length,
      note: calls.length ? `${calls.filter((c) => c.outcome !== 'ok').length} נכשלו` : undefined,
      tone: calls.some((c) => c.outcome !== 'ok') ? 'warn' : undefined,
    },
    {
      label: 'ניסיונות העלאה',
      value: uploads.length,
      note: uploads.length ? `${uploads.filter((u) => u.outcome !== 'accepted').length} לא התקבלו` : undefined,
      tone: uploads.some((u) => u.outcome === 'document_service_unavailable') ? 'warn' : undefined,
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
const STATUS_MARK: Record<RuleStatus, string> = { pass: '✓', fail: '✗', skip: '…', na: '–', info: '•' }
const STATUS_NOTE: Partial<Record<RuleStatus, string>> = { skip: 'לא נבדק', na: 'לא רלוונטי לפעולה' }

/** One engine's checks on the row, collapsed behind a one-line summary. */
function RuleDetails({ list, className }: { list: RuleList; className?: string }) {
  return (
    <details className={['audit-rule-list', list.failed && 'failed', className].filter(Boolean).join(' ')}>
      <summary>{list.summary}</summary>
      <ul>
        {list.items.map((item) => (
          <li className={`audit-gate ${item.status}`} key={item.code}>
            <span aria-hidden="true">{STATUS_MARK[item.status]}</span> {item.label}{' '}
            <span className="mono">{item.code}</span>
            {(item.note ?? STATUS_NOTE[item.status]) && (
              <span className="audit-rule-note"> · {item.note ?? STATUS_NOTE[item.status]}</span>
            )}
          </li>
        ))}
      </ul>
    </details>
  )
}

/** Which engine decided the row and what each one checked (derived from the row alone). */
function Engines({ row }: { row: TraceRow }) {
  const engines = enginesOf(row)
  const opa = opaRulesOf(row)
  const prolog = prologChecksOf(row)
  const datalog = datalogOf(row)
  const z3 = z3ModelOf(row)
  if (engines.length === 0 && !datalog) return null
  const opaList: RuleList | null = opa && {
    engine: 'OPA',
    summary: `OPA: ${opa.filter((rule) => rule.passed).length}/${opa.length} כללי deny עברו`,
    failed: opa.some((rule) => !rule.passed),
    items: opa.map((rule) => ({ code: rule.code, label: rule.label, status: rule.passed ? 'pass' : 'fail' })),
  }
  const chips: EngineVerdict[] = datalog
    ? [
        ...engines,
        { engine: 'Datalog', verdict: datalog.failed ? 'מזעור נכשל' : 'המזעור נשמר', tone: datalog.failed ? 'bad' : 'good' },
      ]
    : engines
  return (
    <div className="audit-engines">
      <div className="audit-gates-group">
        <span className="audit-gates-label">מנועים</span>
        <ul>
          {chips.map((engine) => (
            <li className={`audit-engine tone-${engine.tone}`} key={engine.engine}>
              <span className="audit-engine-name">{engine.engine}</span> {engine.verdict}
            </li>
          ))}
        </ul>
      </div>
      {opaList && <RuleDetails list={opaList} className="audit-opa-rules" />}
      {prolog && <RuleDetails list={prolog} />}
      {datalog && <RuleDetails list={datalog} />}
      {z3 && <RuleDetails list={z3} />}
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

/** Where a row says what the case was classified as. */
const CLASSIFICATION_EVENTS = ['INTENT_CLASSIFIED', 'MEDICAL_QUESTION_DETECTED']

interface Classification {
  intent: string | null
  safety_level: string | null
  /** False on a classification the case later replaced: its values were not kept. */
  latest: boolean
}

/**
 * The case's classification beside the row that produced it. The Audit row holds neither
 * value and the case keeps only its latest, so an earlier classification of a re-classified
 * case says it was replaced instead of showing a value it never had.
 */
function ClassificationLine({ classification }: { classification: Classification }) {
  if (!classification.latest) {
    return <p className="audit-classification replaced">סיווג זה הוחלף בסיווג מאוחר יותר; ערכיו לא נשמרו.</p>
  }
  return (
    <p className="audit-classification">
      סיווג: כוונה <b>{intentLabel(classification.intent)}</b>{' '}
      {classification.intent && <span className="mono">{classification.intent}</span>} · רמת בטיחות{' '}
      <b>{safetyLabel(classification.safety_level)}</b>{' '}
      {classification.safety_level && <span className="mono">{classification.safety_level}</span>}
    </p>
  )
}

/** The LLM attempts made just before this row (attached in `callsByRow`). */
function LlmCalls({ calls }: { calls: LlmCall[] }) {
  if (calls.length === 0) return null
  return (
    <div className="audit-gates-group">
      <span className="audit-gates-label">קריאות LLM</span>
      <ul>
        {calls.map((call, index) => {
          const ok = call.outcome === 'ok'
          return (
            <li className={`audit-gate ${ok ? 'pass' : 'fail'}`} key={`${call.call}-${index}`}>
              <span aria-hidden="true">{ok ? '✓' : '✗'}</span> {LLM_CALL_LABELS[call.call] ?? call.call}{' '}
              <span className="mono">{call.call}</span>
              {!ok && <span className="audit-rule-note"> · {call.outcome}</span>}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/**
 * Each LLM attempt belongs to the first audit row written at or after it - the call happens,
 * then its result is applied. Attempts after the last row (nothing applied yet) stay on it.
 */
function byRow<T extends { created_at: string }>(rows: TraceRow[], items: T[]): Map<number, T[]> {
  const attached = new Map<number, T[]>()
  for (const item of items) {
    const at = new Date(item.created_at).getTime()
    const row = rows.find((candidate) => new Date(candidate.recorded_at).getTime() >= at) ?? rows[rows.length - 1]
    attached.set(row.audit_id, [...(attached.get(row.audit_id) ?? []), item])
  }
  return attached
}

/**
 * Row 98: the patient's upload attempts made just before this row - the refused and unanswered
 * ones add no event of their own, so this is the only place the journal shows them.
 */
function UploadAttempts({ attempts }: { attempts: UploadAttempt[] }) {
  if (attempts.length === 0) return null
  return (
    <div className="audit-gates-group">
      <span className="audit-gates-label">ניסיונות העלאה</span>
      <ul>
        {attempts.map((attempt, index) => {
          const ok = attempt.outcome === 'accepted'
          const reason = uploadReasonLabel(attempt.reason)
          return (
            <li className={`audit-gate ${ok ? 'pass' : 'fail'}`} key={`${attempt.created_at}-${index}`}>
              <span aria-hidden="true">{ok ? '✓' : '✗'}</span> {uploadOutcomeLabel(attempt.outcome)}{' '}
              <span className="mono">{attempt.outcome}</span>
              {attempt.reason && (
                <span className="audit-rule-note">
                  {' · '}
                  {reason ? `${reason} ` : ''}
                  <span className="mono">{attempt.reason}</span>
                </span>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function Item({
  row,
  index,
  previous,
  classification,
  calls,
  uploads,
}: {
  row: TraceRow
  index: number
  previous: TraceRow | null
  classification?: Classification
  calls: LlmCall[]
  uploads: UploadAttempt[]
}) {
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
      {classification && <ClassificationLine classification={classification} />}
      <LlmCalls calls={calls} />
      <UploadAttempts attempts={uploads} />
      <Engines row={row} />
      <Gates row={row} />
      {row.policy_reasons.length > 0 && (
        <div className="audit-reasons">
          <span className="audit-gates-label">{row.record_type === 'Blocked' ? 'סיבת החסימה' : 'נימוקים'}</span>
          <ul className="reasons">
            {row.policy_reasons.map((reason) => {
              const meaning = reasonLabel(reason, row.action)
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

export function AuditTimeline({
  rows,
  label,
  context,
}: {
  rows: TraceRow[]
  label?: string
  /** The case's latest classification and its LLM attempts (`/context`), when known. */
  context?: {
    intent?: string | null
    safety_level?: string | null
    llm_calls?: LlmCall[]
    upload_attempts?: UploadAttempt[]
  }
}) {
  if (rows.length === 0) {
    return <p className="empty-note">אין רשומות ביומן הביקורת לפנייה הזו.</p>
  }
  const calls = byRow(rows, context?.llm_calls ?? [])
  const uploads = byRow(rows, context?.upload_attempts ?? [])
  const lastClassified = [...rows].reverse().find((row) => CLASSIFICATION_EVENTS.includes(row.event))
  const classificationOf = (row: TraceRow): Classification | undefined =>
    context && CLASSIFICATION_EVENTS.includes(row.event)
      ? {
          intent: context.intent ?? null,
          safety_level: context.safety_level ?? null,
          latest: row.audit_id === lastClassified?.audit_id,
        }
      : undefined
  return (
    <div className="audit-journal">
      <Summary rows={rows} calls={context?.llm_calls ?? []} uploads={context?.upload_attempts ?? []} />
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
                  <Item
                    key={row.audit_id}
                    row={row}
                    index={index}
                    previous={index > 0 ? rows[index - 1] : null}
                    classification={classificationOf(row)}
                    calls={calls.get(row.audit_id) ?? []}
                    uploads={uploads.get(row.audit_id) ?? []}
                  />
                )
              })}
            </ol>
          </li>
        ))}
      </ol>
    </div>
  )
}
