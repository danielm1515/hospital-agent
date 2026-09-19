import type { ReactNode } from 'react'
import { CheckIcon, ErrorIcon, InfoIcon, WarnIcon } from './icons'

/** Design `components.html` → Alert. `error` is `role="alert"`, the rest `role="status"`. */
export type AlertVariant = 'info' | 'warn' | 'error' | 'ok'

interface AlertProps {
  variant?: AlertVariant
  title?: ReactNode
  /** The `.text` part: text, a `.mono` fragment, a list or an action button. */
  children?: ReactNode
  className?: string
}

export function Alert({ variant = 'info', title, children, className }: AlertProps) {
  return (
    <div
      className={['alert', variant, className].filter(Boolean).join(' ')}
      role={variant === 'error' ? 'alert' : 'status'}
    >
      {variant === 'info' && <InfoIcon />}
      {variant === 'warn' && <WarnIcon />}
      {variant === 'error' && <ErrorIcon />}
      {variant === 'ok' && <CheckIcon />}
      <div className="body">
        {title !== undefined && <p className="title">{title}</p>}
        {children !== undefined && <div className="text">{children}</div>}
      </div>
    </div>
  )
}
