/**
 * Sub-project 19 (design D7, `docs/api.md` §10): the Case Monitor's LLM cost strip - the average
 * LLM cost per case, with the total, the number of cases and the number of calls beside it, for
 * the cases opened in a range (default the last 30 days). It reads `GET /api/staff/llm-costs`,
 * which any staff member may call; the range presets are the metrics screen's own
 * (`metricsLabels.PRESETS` / `presetRange`), in a compact control.
 */
import { useEffect, useState } from 'react'
import * as api from '../../api/client'
import type { LlmCosts } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Loading } from '../../components/Loading'
import { detailOf } from './labels'
import { PRESETS, costText, formatCount, formatUsd, fromLocalInput, presetRange } from './metricsLabels'

type PresetKey = (typeof PRESETS)[number]['key']

const DEFAULT_PRESET: PresetKey = '30d'

export function LlmCostStrip() {
  const [preset, setPreset] = useState<PresetKey>(DEFAULT_PRESET)
  // Bumped by "נסה שוב", so the same range is asked for again.
  const [attempt, setAttempt] = useState(0)
  const [data, setData] = useState<LlmCosts | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // One flag per load: a newer range, a retry or unmounting supersedes this request, so an
    // answer that arrives afterwards never lands (nor sets state on an unmounted component).
    let cancelled = false
    const hours = PRESETS.find((option) => option.key === preset)?.hours ?? 24 * 30
    // The metrics screen's own preset window (ending at the minute after now), read back the
    // way that screen reads it; a preset's two inputs are always well-formed.
    const range = presetRange(hours, new Date())
    const start = fromLocalInput(range.from) as Date
    const end = fromLocalInput(range.to) as Date
    setData(null)
    setError(null)
    api
      .getLlmCosts(start, end)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(detailOf(caught))
      })
    return () => {
      cancelled = true
    }
  }, [preset, attempt])

  return (
    <section className="card llm-strip" aria-labelledby="llm-strip-h">
      <div className="llm-strip-head">
        <h2 className="llm-strip-h" id="llm-strip-h">
          עלות LLM
        </h2>
        <div className="llm-strip-range" role="group" aria-label="טווח עלות LLM (לפי מועד פתיחת הפנייה)">
          {PRESETS.map((option) => (
            <button
              key={option.key}
              type="button"
              className="llm-range-btn"
              aria-pressed={preset === option.key}
              onClick={() => setPreset(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {error ? (
        <Alert variant="error" title="טעינת עלות ה־LLM נכשלה">
          <span className="mono">{error}</span>{' '}
          <button type="button" className="linkbtn" onClick={() => setAttempt((count) => count + 1)}>
            נסה שוב
          </button>
        </Alert>
      ) : data === null ? (
        <Loading size="inline" label="טוען עלות LLM" />
      ) : (
        <>
          <dl className="llm-strip-facts">
            <div className="llm-strip-fact llm-strip-main">
              <dt>עלות LLM ממוצעת לפנייה</dt>
              <dd>{formatUsd(data.avg_cost_per_case_usd)}</dd>
            </div>
            <div className="llm-strip-fact">
              <dt>סה״כ</dt>
              <dd>{costText(data.total_cost_usd, data.unpriced_calls)}</dd>
            </div>
            <div className="llm-strip-fact">
              <dt>פניות</dt>
              <dd>{formatCount(data.cases)}</dd>
            </div>
            <div className="llm-strip-fact">
              <dt>קריאות</dt>
              <dd>{formatCount(data.calls)}</dd>
            </div>
          </dl>
          {data.unpriced_calls > 0 && (
            <p className="llm-strip-note">
              ל־{formatCount(data.unpriced_calls)} מהקריאות אין מחיר ידוע, והן אינן נכללות בעלות.
            </p>
          )}
        </>
      )}
    </section>
  )
}
