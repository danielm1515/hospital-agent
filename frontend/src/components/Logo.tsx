/** The Ramon lockup (design `components.html` → Logo): full, compact and onbrand. */

export type LogoVariant = 'full' | 'compact' | 'onbrand'

export const ORG_NAME = 'המרכז הרפואי רמון'
export const PRODUCT_NAME = 'סוכן פניות מטופלים'
export const ORG_SHORT = 'רמון'

interface MarkProps {
  size: number
  onBrand?: boolean
}

/** The 40×40 mark; decorative, the org name next to it is the accessible text. */
export function LogoMark({ size, onBrand = false }: MarkProps) {
  return (
    <svg className="mark" width={size} height={size} viewBox="0 0 40 40" aria-hidden="true" focusable="false">
      <rect x="0" y="0" width="40" height="40" rx="12" fill={onBrand ? '#ffffff' : 'var(--brand-500)'} />
      <path d="M22 40h6a12 12 0 0 0 12-12v-6a18 18 0 0 1-18 18Z" fill="var(--teal-500)" />
      <path
        d="M8 21h5l3-6 4 12 3-6h5"
        fill="none"
        stroke={onBrand ? 'var(--brand-700)' : '#ffffff'}
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

interface LogoProps {
  variant?: LogoVariant
  /** `full` only: show the product line under the org name (default true). */
  withProduct?: boolean
  /** Override the mark size (px); defaults follow the design: 40 full, 28 compact/onbrand. */
  markSize?: number
  className?: string
}

export function Logo({ variant = 'full', withProduct = true, markSize, className }: LogoProps) {
  const extra = className ? ` ${className}` : ''
  if (variant === 'compact') {
    return (
      <div className={`lock compact${extra}`}>
        <LogoMark size={markSize ?? 28} />
        <span className="org compact-org">{ORG_SHORT}</span>
      </div>
    )
  }
  if (variant === 'onbrand') {
    return (
      <div className={`lock onbrand${extra}`}>
        <LogoMark size={markSize ?? 28} onBrand />
        <span className="org invert">{ORG_NAME}</span>
      </div>
    )
  }
  return (
    <div className={`lock full${extra}`}>
      <LogoMark size={markSize ?? 40} />
      <span className="words">
        <span className="org">{ORG_NAME}</span>
        {withProduct && <span className="product">{PRODUCT_NAME}</span>}
      </span>
    </div>
  )
}
