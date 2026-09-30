import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import * as api from '../../api/client'
import type { Metrics as MetricsData } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { Loading } from '../../components/Loading'
import { detailOf, escalationLabel, formatDateTime, stateLabel } from './labels'
import {
  ERROR_TEXT,
  EVENT_LABELS,
  FORMAL_KINDS,
  LLM_CALL_LABELS,
  LLM_SOURCE_LABELS,
  OUTCOME_LABELS,
  PRESETS,
  REASON_LABELS,
  SOURCE_LABELS,
  costText,
  durationsText,
  entryCostText,
  failureRow,
  formatCount,
  formatPercent,
  formatSeconds,
  formatUsd,
  fromLocalInput,
  labelOf,
  presetRange,
  toRows,
} from './metricsLabels'
import type { LocalRange } from './metricsLabels'
import { ConsistencyGroup } from './ConsistencyGroup'
import { Bars, Coded, Meter, Tile } from './MetricsParts'
import { Usd } from './Usd'

const event = (code: string) => labelOf(EVENT_LABELS, code)

/**
 * "מדדי מערכת" (sub-project 14, design §8): admin_staff only - the server's require_admin is
 * the gate, StaffRoutes only hides the link. One filter row scopes every group below it
 * (dataviz: date range first, presets before a custom range). A refetch keeps the previous
 * numbers on screen, dimmed, instead of flashing a loading state.
 */
export function Metrics() {
  const [range, setRange] = useState<LocalRange>(() => presetRange(24 * 7, new Date()))
  const [data, setData] = useState<MetricsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Requests can overlap (mount, a preset click, submit, "נסה שוב") and are not guaranteed to
  // resolve in the order they were sent; a sequence counter lets only the most recently sent
  // request's result land, so a slower, older request can never overwrite the range the admin
  // most recently asked for.
  const requestSeq = useRef(0)

  const load = useCallback(async (next: LocalRange) => {
    const seq = ++requestSeq.current
    const start = fromLocalInput(next.from)
    const end = fromLocalInput(next.to)
    if (!start || !end) {
      if (seq === requestSeq.current) setError('invalid_range')
      return
    }
    setLoading(true)
    try {
      const result = await api.getMetrics(start, end)
      if (seq !== requestSeq.current) return
      setData(result)
      setError(null)
    } catch (caught) {
      if (seq !== requestSeq.current) return
      setError(detailOf(caught))
    } finally {
      if (seq === requestSeq.current) setLoading(false)
    }
  }, [])

  const initial = useRef(range)
  useEffect(() => {
    void load(initial.current)
  }, [load])

  function choosePreset(hours: number) {
    const next = presetRange(hours, new Date())
    setRange(next)
    void load(next)
  }

  return (
    <section className="staff-page metrics-page">
      <header className="page-head">
        <h1 className="page-h">מדדי מערכת</h1>
        <p className="lede">מספרים מצטברים מתוך ה־Audit. אין בהם פרטי מטופל.</p>
      </header>

      <form
        className="metrics-filters card"
        onSubmit={(submitted) => {
          submitted.preventDefault()
          void load(range)
        }}
      >
        <div className="metrics-presets" role="group" aria-label="טווחים מוכנים">
          {PRESETS.map((preset) => (
            <Button key={preset.key} variant="secondary" onClick={() => choosePreset(preset.hours)}>
              {preset.label}
            </Button>
          ))}
        </div>
        <label className="metrics-field">
          <span>מ־</span>
          <input
            type="datetime-local"
            value={range.from}
            onChange={(changed) => setRange({ ...range, from: changed.target.value })}
          />
        </label>
        <label className="metrics-field">
          <span>עד</span>
          <input
            type="datetime-local"
            value={range.to}
            onChange={(changed) => setRange({ ...range, to: changed.target.value })}
          />
        </label>
        <Button type="submit" variant="primary" busy={loading}>
          הצג
        </Button>
      </form>

      {error && (
        <Alert variant="error" title="לא הצלחנו לטעון את המדדים">
          {ERROR_TEXT[error] && <>{ERROR_TEXT[error]} </>}
          <span className="mono">{error}</span>{' '}
          <button type="button" className="linkbtn" onClick={() => void load(range)}>
            נסה שוב
          </button>
        </Alert>
      )}

      {data === null ? (
        !error && <Loading />
      ) : (
        <div className={loading ? 'metrics-body is-stale' : 'metrics-body'} aria-busy={loading}>
          <p className="metrics-window">
            {formatDateTime(data.window.start)} – {formatDateTime(data.window.end)} · חושב ב־
            {formatDateTime(data.generated_at)}
          </p>
          <FlowGroup flow={data.flow} />
          <HumanLoadGroup load={data.human_load} />
          <ToolsGroup tools={data.tools} />
          <PatientSlaGroup sla={data.patient_sla} />
          <PolicyGroup policy={data.policy} decidedByKind={data.human_load.decided_by_kind} />
          <LlmGroup llm={data.llm} />
        </div>
      )}
      <ConsistencyGroup />
    </section>
  )
}

function Group({ id, title, scope, children }: { id: string; title: string; scope: string; children: ReactNode }) {
  return (
    <section className="card metrics-group" aria-labelledby={id}>
      <h2 className="section-h" id={id}>
        {title}
      </h2>
      <p className="metrics-scope">{scope}</p>
      {children}
    </section>
  )
}

function FlowGroup({ flow }: { flow: MetricsData['flow'] }) {
  const state = (code: string) => flow.by_state[code] ?? 0
  const named = ['Completed', 'Failed', 'AwaitingHumanReview', 'AwaitingPatientInput', 'AwaitingPatientReply']
  const inProgress = flow.opened - named.reduce((sum, code) => sum + state(code), 0)
  return (
    <Group id="metrics-flow" title="זרימת פניות" scope="הפניות שנפתחו בטווח, במצבן הנוכחי">
      <div className="metrics-tiles">
        <Tile label="נפתחו" value={formatCount(flow.opened)} />
        <Tile label="הושלמו" value={formatCount(state('Completed'))} />
        <Tile label="נדחו ע״י צוות" value={formatCount(state('Failed'))} />
        <Tile label="ממתינות לאדם" value={formatCount(state('AwaitingHumanReview'))} />
        <Tile label="ממתינות למטופל" value={formatCount(state('AwaitingPatientInput'))} />
        <Tile label="ממתינות לתשובת מטופל" value={formatCount(state('AwaitingPatientReply'))} />
        <Tile label="בטיפול" value={formatCount(inProgress)} />
      </div>
      <h3 className="metrics-sub">תוצאת הסיווג</h3>
      <Bars rows={toRows(flow.by_outcome, (code) => labelOf(OUTCOME_LABELS, code))} empty="אין פניות בטווח." />
      <h3 className="metrics-sub">לפי מצב</h3>
      <Bars rows={toRows(flow.by_state, stateLabel)} empty="אין פניות בטווח." />
      <h3 className="metrics-sub">זמן עד השלמה</h3>
      <dl className="metrics-facts">
        {Object.entries(flow.completion).map(([code, durations]) => (
          <div key={code} className="metrics-fact">
            <dt>
              {event(code)} <span className="mono metrics-code">{code}</span>
            </dt>
            <dd>{durationsText(durations)}</dd>
          </div>
        ))}
      </dl>
    </Group>
  )
}

function HumanLoadGroup({ load }: { load: MetricsData['human_load'] }) {
  return (
    <Group id="metrics-human" title="עומס על הצוות" scope="אירועים שקרו בטווח. התור הפתוח — נכון לעכשיו">
      <div className="metrics-tiles">
        <Tile
          label="כניסות לתור ההסלמות"
          value={formatCount(load.escalations_entered)}
          note="כולל גם חזרות מתשובת מטופל ומתפוגת מועד"
        />
        <Tile
          label="פתוחות עכשיו"
          value={formatCount(load.open_now)}
          note={load.oldest_open_seconds === null ? undefined : `הוותיקה ממתינה ${formatSeconds(load.oldest_open_seconds)}`}
        />
        <Tile
          label="זמן עד פעולה אנושית ראשונה (p50)"
          value={formatSeconds(load.time_to_decision.p50)}
          note={durationsText(load.time_to_decision)}
        />
      </div>
      <h3 className="metrics-sub">הכרעות</h3>
      <Bars rows={toRows(load.decisions, event)} empty="אין הכרעות בטווח." />
      <h3 className="metrics-sub">הוכרעו בטווח, לפי סוג הסלמה</h3>
      <Bars rows={toRows(load.decided_by_kind, escalationLabel)} empty="אין הכרעות בטווח." />
      <h3 className="metrics-sub">פתוחות עכשיו, לפי סוג הסלמה</h3>
      <Bars rows={toRows(load.open_by_kind, escalationLabel)} empty="התור ריק." />
    </Group>
  )
}

function ToolsGroup({ tools }: { tools: MetricsData['tools'] }) {
  return (
    <Group id="metrics-tools" title="כלים חיצוניים" scope="קריאות שהתחילו בטווח">
      <div className="metrics-tiles">
        <Tile label="כשלים זמניים" value={formatCount(tools.failure_events.TOOL_TRANSIENT_FAILURE ?? 0)} />
        <Tile label="ניסיונות שמוצו" value={formatCount(tools.failure_events.RETRY_EXHAUSTED ?? 0)} />
        <Tile label="קריאות חוזרות" value={formatCount(tools.retried_calls)} />
      </div>
      {tools.actions.length === 0 ? (
        <p className="metrics-empty">אין קריאות בטווח.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table metrics-table">
            <thead>
              <tr>
                <th scope="col">פעולה</th>
                <th scope="col">קריאות</th>
                <th scope="col">הצליחו</th>
                <th scope="col">נכשלו</th>
                <th scope="col">לא ידוע</th>
                <th scope="col">אחוז הצלחה</th>
                <th scope="col">p50</th>
                <th scope="col">p95</th>
                <th scope="col">max</th>
              </tr>
            </thead>
            <tbody>
              {tools.actions.map((action) => {
                const calls = Object.values(action.by_status).reduce((sum, count) => sum + count, 0)
                return (
                  <tr key={action.action}>
                    <td className="mono">{action.action}</td>
                    <td>{formatCount(calls)}</td>
                    <td>{formatCount(action.by_status.succeeded ?? 0)}</td>
                    <td>{formatCount(action.by_status.failed ?? 0)}</td>
                    <td>{formatCount(action.by_status.unknown ?? 0)}</td>
                    <td className="metrics-rate">
                      <Meter ratio={action.success_rate} label={`אחוז הצלחה ${action.action}`} />
                      {formatPercent(action.success_rate)}
                    </td>
                    <td>{formatSeconds(action.latency.p50)}</td>
                    <td>{formatSeconds(action.latency.p95)}</td>
                    <td>{formatSeconds(action.latency.max)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <h3 className="metrics-sub">סיבות כשל</h3>
      <Bars rows={tools.failure_reasons.map(failureRow)} empty="אין כשלים בטווח." />
      <h3 className="metrics-sub">מקורות מוגדרים</h3>
      <dl className="metrics-facts">
        {Object.entries(tools.sources).map(([system, source]) => (
          <div key={system} className="metrics-fact">
            <dt className="mono">{system}</dt>
            <dd>{source === null ? 'לא דווח' : <Coded label={labelOf(SOURCE_LABELS, source)} code={source} />}</dd>
          </div>
        ))}
      </dl>
    </Group>
  )
}

function PatientSlaGroup({ sla }: { sla: MetricsData['patient_sla'] }) {
  return (
    <Group id="metrics-sla" title="SLA מטופל" scope="בקשות מסמך שנשלחו בטווח, לפי מה שסיים כל המתנה">
      <div className="metrics-tiles">
        <Tile label="עמידה בזמן" value={formatPercent(sla.rate)} note="עמדו / (עמדו + חרגו)" />
        <Tile label="בקשות מסמך" value={formatCount(sla.requests)} />
        <Tile label="עמדו בזמן" value={formatCount(sla.met)} />
        <Tile label="חרגו" value={formatCount(sla.breached)} />
        <Tile label="יצאו אחרת" value={formatCount(sla.other)} />
        <Tile label="ממתינות" value={formatCount(sla.waiting)} />
      </div>
      <Meter ratio={sla.rate} label="עמידה בזמן" />
    </Group>
  )
}

function PolicyGroup({ policy, decidedByKind }: { policy: MetricsData['policy']; decidedByKind: Record<string, number> }) {
  const formal = Object.fromEntries(FORMAL_KINDS.map((kind) => [kind, decidedByKind[kind] ?? 0]))
  return (
    <Group id="metrics-policy" title="מדיניות" scope="אירועים שקרו בטווח">
      <div className="metrics-tiles">
        <Tile label="חסימות" value={formatCount(policy.blocked)} />
      </div>
      <h3 className="metrics-sub">החלטות מדיניות</h3>
      <Bars rows={toRows(policy.decisions, event)} empty="אין החלטות בטווח." />
      <h3 className="metrics-sub">חסימות לפי סיבה</h3>
      <Bars rows={toRows(policy.blocked_by_reason, (code) => labelOf(REASON_LABELS, code))} empty="אין חסימות בטווח." />
      <h3 className="metrics-sub">חסימות לפי אירוע</h3>
      <Bars rows={toRows(policy.blocked_by_event, (code) => code)} empty="אין חסימות בטווח." />
      <h3 className="metrics-sub">הסלמות של השכבות הפורמליות שהוכרעו בטווח</h3>
      <Bars rows={toRows(formal, escalationLabel)} empty="—" />
    </Group>
  )
}

/**
 * Sub-project 19 (design D7, `docs/api.md` §7, §10): the LLM cost of the window's cohort - the
 * cases opened in it, with all of their usage. Costs follow §10's NULL rule (`formatUsd`): "—"
 * where nothing was measured, "מחיר לא ידוע" where only unpriced attempts left a cost `null`.
 */
function LlmGroup({ llm }: { llm: MetricsData['llm'] }) {
  const unpricedNote =
    llm.unpriced_calls > 0 && llm.total_cost_usd !== null ? 'לא כולל קריאות ללא מחיר ידוע' : undefined
  return (
    <Group id="metrics-llm" title="עלות LLM" scope="הפניות שנפתחו בטווח, עם כל השימוש שלהן">
      <div className="metrics-tiles">
        <Tile
          label="עלות כוללת"
          value={<Usd text={costText(llm.total_cost_usd, llm.unpriced_calls)} />}
          note={unpricedNote}
        />
        <Tile
          label="ממוצע לפנייה"
          value={<Usd text={costText(llm.avg_cost_per_case_usd, llm.unpriced_calls)} />}
          note="על פני הפניות שיש להן עלות ידועה"
        />
        <Tile label="ממוצע לפנייה שהושלמה" value={<Usd text={formatUsd(llm.avg_cost_per_completed_case_usd)} />} />
        <Tile label="פניות" value={formatCount(llm.cases)} note={`${formatCount(llm.cases_with_usage)} מהן עם שימוש`} />
        <Tile label="קריאות" value={formatCount(llm.calls)} />
        <Tile label="קריאות ללא מחיר ידוע" value={formatCount(llm.unpriced_calls)} />
        <Tile label="טוקני קלט" value={formatCount(llm.input_tokens)} note={`${formatCount(llm.cached_input_tokens)} מהמטמון`} />
        <Tile label="טוקני פלט" value={formatCount(llm.output_tokens)} />
      </div>
      <h3 className="metrics-sub">לפי קריאה</h3>
      {llm.by_call.length === 0 ? (
        <p className="metrics-empty">אין קריאות בטווח.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table metrics-table">
            <thead>
              <tr>
                <th scope="col">קריאה</th>
                <th scope="col">קריאות</th>
                <th scope="col">טוקני קלט</th>
                <th scope="col">טוקני פלט</th>
                <th scope="col">עלות</th>
              </tr>
            </thead>
            <tbody>
              {llm.by_call.map((entry) => (
                <tr key={entry.call}>
                  <td>
                    <Coded label={labelOf(LLM_CALL_LABELS, entry.call)} code={entry.call} />
                  </td>
                  <td>{formatCount(entry.calls)}</td>
                  <td>{formatCount(entry.input_tokens)}</td>
                  <td>{formatCount(entry.output_tokens)}</td>
                  <td>
                    <Usd text={entryCostText(entry)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h3 className="metrics-sub">לפי מקור</h3>
      {llm.by_source.length === 0 ? (
        <p className="metrics-empty">אין קריאות בטווח.</p>
      ) : (
        <dl className="metrics-facts">
          {llm.by_source.map((entry) => (
            <div key={entry.source} className="metrics-fact">
              <dt>
                <Coded label={labelOf(LLM_SOURCE_LABELS, entry.source)} code={entry.source} />
              </dt>
              <dd>
                {formatCount(entry.calls)} קריאות · <Usd text={entryCostText(entry)} />
              </dd>
            </div>
          ))}
        </dl>
      )}
    </Group>
  )
}
