/** Inline icons from `design/ramon-ui/` (all decorative: `aria-hidden`). */

export function InfoIcon({ className = 'ico' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" focusable="false">
      <circle cx="10" cy="10" r="7.5" />
      <path d="M10 9.2v4.3" />
      <path d="M10 6.4v.1" />
    </svg>
  )
}

export function WarnIcon({ className = 'ico' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M10 3.2 17.5 16.3h-15Z" />
      <path d="M10 8.2v3.4" />
      <path d="M10 13.8v.1" />
    </svg>
  )
}

export function ErrorIcon({ className = 'ico', size }: { className?: string; size?: number }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" focusable="false">
      <circle cx="10" cy="10" r="7.5" />
      <path d="M10 6.5v4.5" />
      <path d="M10 13.6v.1" />
    </svg>
  )
}

export function CheckIcon({ className = 'ico', size }: { className?: string; size?: number }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M4.5 10.5 8.5 14.5 15.5 6" />
    </svg>
  )
}

export function PhoneIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <rect x="5" y="2.5" width="10" height="15" rx="1.8" />
      <path d="M9 15h2" />
    </svg>
  )
}

export function KeyIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <rect x="4" y="8.5" width="12" height="8" rx="2" />
      <path d="M7 8.5V6a3 3 0 0 1 6 0v2.5" />
    </svg>
  )
}

export function ShieldCheckIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M10 2.8 16 5v5c0 3.4-2.4 6.1-6 7.2-3.6-1.1-6-3.8-6-7.2V5Z" />
      <path d="M7.6 10.2 9.4 12l3.2-3.4" />
    </svg>
  )
}
