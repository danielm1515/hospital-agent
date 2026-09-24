/**
 * The metrics screen's pieces (sub-project 14; dataviz): a stat tile, a one-series bar list
 * and a meter. No chart library - the project keeps no UI dependency beyond React and the
 * router.
 */
import { formatCount, formatPercent } from './metricsLabels'
import type { BarRow } from './metricsLabels'

/** Value, label, optional note. The value keeps proportional figures (not the mono `.num`). */
export function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="metrics-tile">
      <span className="metrics-tile-v">{value}</span>
      <span className="metrics-tile-k">{label}</span>
      {note && <span className="metrics-tile-note">{note}</span>}
    </div>
  )
}

/**
 * A label beside the code it translates - or, when a code is its own label (the labels don't
 * have anything to add), the code alone. Always inside `.mono`, so a Latin run is never left
 * outside the isolation that keeps it from picking its own direction on this RTL page.
 */
export function Coded({ label, code }: { label: string; code: string }) {
  return label === code ? (
    <span className="mono metrics-code">{code}</span>
  ) : (
    <>
      {label}
      {' '}
      <span className="mono metrics-code">{code}</span>
    </>
  )
}

/**
 * One series, so one color for every bar - never a value ramp on nominal categories. Every
 * value is written at its bar's tip, so the list is its own table view and no tooltip is
 * needed to read anything; the bar itself is decoration for assistive technology.
 */
export function Bars({ rows, empty }: { rows: BarRow[]; empty: string }) {
  if (rows.length === 0) return <p className="metrics-empty">{empty}</p>
  const top = Math.max(...rows.map((row) => row.count))
  return (
    <ul className="metrics-bars">
      {rows.map((row) => (
        <li key={row.key ?? row.code} className="metrics-bar">
          <span className="metrics-bar-k">
            <Coded label={row.label} code={row.code} />
          </span>
          <span className="metrics-bar-lane" aria-hidden="true">
            <span className="metrics-bar-fill" style={{ width: `${top ? (row.count / top) * 100 : 0}%` }} />
          </span>
          <span className="metrics-bar-v">{formatCount(row.count)}</span>
        </li>
      ))}
    </ul>
  )
}

/** A ratio against 100%: the fill carries it, the track is a lighter step of the same ramp. */
export function Meter({ ratio, label }: { ratio: number | null; label: string }) {
  return (
    <span className="metrics-meter" role="img" aria-label={`${label}: ${formatPercent(ratio)}`}>
      <span className="metrics-meter-fill" style={{ width: `${ratio === null ? 0 : ratio * 100}%` }} />
    </span>
  )
}
