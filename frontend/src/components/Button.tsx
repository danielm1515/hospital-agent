import type { ButtonHTMLAttributes, MouseEvent } from 'react'

/** Design `components.html` → Button (`.btn` + variant). */
export type ButtonVariant = 'primary' | 'secondary' | 'quiet' | 'danger'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  /** Shows the spinner and `aria-busy`; clicks are ignored meanwhile (no double submit). */
  busy?: boolean
  /** Full width (`.block`). */
  block?: boolean
}

export function Button({
  variant = 'primary',
  busy = false,
  block = false,
  type = 'button',
  className,
  children,
  onClick,
  ...rest
}: ButtonProps) {
  const classes = ['btn', variant, busy && 'busy', block && 'block', className].filter(Boolean).join(' ')

  function handleClick(event: MouseEvent<HTMLButtonElement>) {
    if (busy) {
      event.preventDefault()
      return
    }
    onClick?.(event)
  }

  return (
    <button
      {...rest}
      type={type}
      className={classes}
      aria-busy={busy ? true : undefined}
      onClick={handleClick}
    >
      {busy && <span className="spin" aria-hidden="true" />}
      {children}
    </button>
  )
}
