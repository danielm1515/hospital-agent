/**
 * Sub-project 19: one money output (`formatUsd` / `costText` / `entryCostText`) on an RTL page.
 * A dollar amount is a Latin run, so it sits in its own LTR run (`dir="ltr"`, which the UA
 * isolates) - otherwise the RTL cell reorders its neutrals and "<$0.0001" or "$0.0010+" come
 * out with the "<" or the "+" on the wrong side. The partial marker belongs to the amount and
 * sits inside the same run. A Hebrew text ("—", "מחיר לא ידוע") stays in the page's direction.
 */
export const PARTIAL_COST = 'חלק מהקריאות ללא מחיר ידוע'

export function Usd({ text, partial = false }: { text: string; partial?: boolean }) {
  if (!text.startsWith('$') && !text.startsWith('<$')) return <>{text}</>
  return (
    <span className="usd" dir="ltr">
      {text}
      {partial && (
        <span className="llm-partial" role="img" title={PARTIAL_COST} aria-label={PARTIAL_COST}>
          +
        </span>
      )}
    </span>
  )
}
