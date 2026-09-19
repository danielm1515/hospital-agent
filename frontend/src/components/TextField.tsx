import { useId } from 'react'
import type { ChangeEvent, InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from 'react'
import { ErrorIcon } from './icons'

/**
 * Design `components.html` → TextField: `.field` → `.label` → `.control` (optional `.prefix`)
 * → `.input`, then `.hint` / `.hint.error`. An error sets `aria-invalid` and points
 * `aria-describedby` at the message. `multiline` renders a textarea; `counter` shows
 * `length/maxLength` under the control.
 */
interface CommonProps {
  label: ReactNode
  id?: string
  prefix?: ReactNode
  hint?: ReactNode
  error?: ReactNode
  disabled?: boolean
  /** Show a character counter (needs `maxLength`). */
  counter?: boolean
  maxLength?: number
  value?: string
  className?: string
}

type InputOnlyProps = Omit<InputHTMLAttributes<HTMLInputElement>, keyof CommonProps | 'prefix' | 'onChange'> & {
  multiline?: false
  onChange?: (event: ChangeEvent<HTMLInputElement>) => void
}

type TextareaOnlyProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, keyof CommonProps | 'prefix' | 'onChange'> & {
  multiline: true
  onChange?: (event: ChangeEvent<HTMLTextAreaElement>) => void
}

export type TextFieldProps = CommonProps & (InputOnlyProps | TextareaOnlyProps)

export function TextField(props: TextFieldProps) {
  const { label, id, prefix, hint, error, disabled = false, counter = false, maxLength, value, className, ...rest } = props
  const autoId = useId()
  const inputId = id ?? `tf-${autoId}`
  const hintId = `${inputId}-hint`
  const errorId = `${inputId}-err`
  const counterId = `${inputId}-count`
  const invalid = Boolean(error)
  const showCounter = counter && maxLength !== undefined

  const describedBy = [invalid ? errorId : hint ? hintId : null, showCounter ? counterId : null]
    .filter(Boolean)
    .join(' ') || undefined

  const controlClass = ['control', props.multiline && 'multiline', invalid && 'invalid', disabled && 'disabled']
    .filter(Boolean)
    .join(' ')

  const shared = {
    id: inputId,
    className: 'input',
    disabled,
    maxLength,
    value,
    'aria-invalid': invalid ? true : undefined,
    'aria-describedby': describedBy,
  } as const

  let control: ReactNode
  if (rest.multiline) {
    const { multiline: _multiline, ...textareaProps } = rest as TextareaOnlyProps
    control = <textarea rows={5} {...textareaProps} {...shared} />
  } else {
    const { multiline: _multiline, ...inputProps } = rest as InputOnlyProps
    control = <input {...inputProps} {...shared} />
  }

  return (
    <div className={className ? `field ${className}` : 'field'}>
      <label className="label" htmlFor={inputId}>
        {label}
      </label>
      <div className={controlClass}>
        {prefix !== undefined && (
          <span className="prefix" aria-hidden="true">
            {prefix}
          </span>
        )}
        {control}
      </div>
      {(invalid || hint || showCounter) && (
        <div className="hint-row">
          {invalid ? (
            <p className="hint error" id={errorId}>
              <ErrorIcon className="hint-ico" size={16} />
              {error}
            </p>
          ) : hint ? (
            <p className="hint" id={hintId}>
              {hint}
            </p>
          ) : null}
          {showCounter && (
            <p className="hint counter" id={counterId} dir="ltr">
              {(value ?? '').length}/{maxLength}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
