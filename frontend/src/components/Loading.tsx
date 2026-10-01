export interface LoadingProps {
  /** The visible text beside the ring. Defaults to a generic "טוען…". */
  label?: string
  /**
   * `page` (default): a centred ring for a full screen or section load.
   * `inline`: a smaller ring for a nested loading spot, e.g. an expanded row.
   */
  size?: 'page' | 'inline'
}

/**
 * The one shared loading indicator (staff-fixes design Task 6), used by every
 * loading place in both UIs instead of each screen inlining its own "טוען…" text.
 * A ring (track `--surface-200`, arc `--brand-500`) sits beside a visible status
 * text. `role="status"` and `aria-live="polite"` so a screen reader announces it
 * once. The ring always rotates, even under `prefers-reduced-motion` - the owner's
 * decision (2026-10-01): a stopped or hidden ring read as a frozen screen.
 */
export function Loading({ label = 'טוען…', size = 'page' }: LoadingProps) {
  return (
    <p className={`loader loader-${size}`} role="status" aria-live="polite">
      <span className="loader-ring" aria-hidden="true" />
      {label}
    </p>
  )
}
