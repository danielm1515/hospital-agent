/**
 * Sub-project 19 (design D7, `docs/api.md` §5, §10): the case's LLM usage - its attempts, their
 * tokens and cost, and one row per call code - as one fact group in the Case Monitor's expanded
 * row. Staff-only. Each Hebrew label keeps the API's field name or call code beside it (CLAUDE.md),
 * and a cost follows §10's NULL rule: "—" when there was no attempt, "מחיר לא ידוע" when only
 * unpriced attempts made it `null`.
 */
import type { ReactNode } from 'react'
import type { CaseLlmUsage } from '../../api/types'
import { LLM_CALL_LABELS, costText, entryCostText, formatCount, labelOf } from './metricsLabels'

function Fact({ label, code, value }: { label: string; code: string; value: ReactNode }) {
  return (
    <div className="fact">
      <dt className="fact-k">
        {label}
        <span className="fact-code mono">{code}</span>
      </dt>
      <dd className="fact-v">{value}</dd>
    </div>
  )
}

export function CaseLlmFacts({ usage }: { usage: CaseLlmUsage }) {
  return (
    <section className="fact-group">
      <h3 className="fact-group-h">עלות LLM</h3>
      <dl className="fact-list">
        <Fact label="קריאות" code="calls" value={formatCount(usage.calls)} />
        <Fact label="טוקני קלט" code="input_tokens" value={formatCount(usage.input_tokens)} />
        <Fact label="מתוכם מהמטמון" code="cached_input_tokens" value={formatCount(usage.cached_input_tokens)} />
        <Fact label="טוקני פלט" code="output_tokens" value={formatCount(usage.output_tokens)} />
        <Fact label="עלות" code="cost_usd" value={costText(usage.cost_usd, usage.unpriced_calls)} />
        <Fact label="קריאות ללא מחיר ידוע" code="unpriced_calls" value={formatCount(usage.unpriced_calls)} />
      </dl>
      {usage.unpriced_calls > 0 && usage.cost_usd !== null && (
        <p className="llm-note">העלות אינה כוללת את הקריאות שאין להן מחיר ידוע.</p>
      )}

      {usage.by_call.length === 0 ? (
        <p className="llm-note">לא נעשו קריאות LLM בפנייה הזו.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table llm-calls">
            <caption className="llm-calls-caption">לפי קריאה (by_call)</caption>
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
              {usage.by_call.map((entry) => {
                const label = labelOf(LLM_CALL_LABELS, entry.call)
                return (
                  <tr key={entry.call}>
                    <td>
                      {/* An unknown code is its own label: shown once, still inside .mono. */}
                      {label !== entry.call && label}
                      <span className="mono cell-sub">{entry.call}</span>
                    </td>
                    <td>{formatCount(entry.calls)}</td>
                    <td>{formatCount(entry.input_tokens)}</td>
                    <td>{formatCount(entry.output_tokens)}</td>
                    <td className="nowrap">{entryCostText(entry)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
