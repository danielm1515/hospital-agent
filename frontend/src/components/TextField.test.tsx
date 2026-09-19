import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { TextField } from './TextField'

describe('TextField', () => {
  it('links the label, the control and the hint', () => {
    const { container } = render(<TextField label="מספר טלפון נייד" hint="נשלח קוד חד-פעמי למספר זה." />)
    const input = screen.getByLabelText('מספר טלפון נייד')
    expect(input).toHaveClass('input')
    expect(container.querySelector('.control')).toBeInTheDocument()
    const hint = screen.getByText('נשלח קוד חד-פעמי למספר זה.')
    expect(input).toHaveAttribute('aria-describedby', hint.id)
    expect(input).not.toHaveAttribute('aria-invalid')
  })

  it('renders a prefix inside the control', () => {
    const { container } = render(<TextField label="טלפון" prefix={<span className="cc">972+</span>} />)
    const prefix = container.querySelector('.control .prefix')!
    expect(prefix).toHaveAttribute('aria-hidden', 'true')
    expect(prefix).toHaveTextContent('972+')
  })

  it('marks an error with aria-invalid and aria-describedby', () => {
    const { container } = render(<TextField label="טלפון" hint="רמז" error="מספר לא תקין. הזינו עשר ספרות." />)
    const input = screen.getByLabelText('טלפון')
    const message = screen.getByText('מספר לא תקין. הזינו עשר ספרות.')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAttribute('aria-describedby', message.id)
    expect(message).toHaveClass('hint', 'error')
    expect(container.querySelector('.control.invalid')).toBeInTheDocument()
    expect(screen.queryByText('רמז')).not.toBeInTheDocument()
  })

  it('dims and disables the control', () => {
    const { container } = render(<TextField label="מספר תעודת זהות" value="03•••••9" disabled readOnly />)
    expect(screen.getByLabelText('מספר תעודת זהות')).toBeDisabled()
    expect(container.querySelector('.control.disabled')).toBeInTheDocument()
  })

  it('renders a textarea with a counter when multiline', async () => {
    const onChange = vi.fn()
    render(
      <TextField
        multiline
        counter
        maxLength={2000}
        label="תוכן הפנייה"
        value="שלום"
        onChange={onChange}
      />,
    )
    const textarea = screen.getByLabelText('תוכן הפנייה')
    expect(textarea.tagName).toBe('TEXTAREA')
    expect(textarea).toHaveAttribute('maxlength', '2000')
    const counter = screen.getByText('4/2000')
    expect(textarea.getAttribute('aria-describedby')).toContain(counter.id)
    await userEvent.type(textarea, 'ו')
    expect(onChange).toHaveBeenCalled()
  })
})
