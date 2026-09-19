import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AuthLayout } from './AuthLayout'
import { ORG_NAME } from './Logo'

describe('AuthLayout', () => {
  it('splits the screen into a form pane and a brand pane', () => {
    const { container } = render(
      <AuthLayout asideContent={<p className="brand-h">פנייה אחת, מענה אחד</p>}>
        <h1 className="h1">כניסה לפניות מטופלים</h1>
      </AuthLayout>,
    )
    expect(container.querySelector('.screen.screen-brand')).toBeInTheDocument()
    expect(container.querySelector('.pane.form-pane')).toBeInTheDocument()
    expect(container.querySelector('.pane.brand-pane')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'כניסה לפניות מטופלים' })).toBeInTheDocument()
    expect(screen.getByText('פנייה אחת, מענה אחד')).toBeInTheDocument()
  })

  it('uses the light side pane for the staff variant', () => {
    const { container } = render(
      <AuthLayout aside="side" asideContent={<p>מה מותר מהמסך הזה</p>}>
        <h1 className="h1">כניסת צוות רפואי</h1>
      </AuthLayout>,
    )
    expect(container.querySelector('.screen.screen-side')).toBeInTheDocument()
    expect(container.querySelector('.pane.side-pane')).toBeInTheDocument()
    expect(container.querySelector('.brand-pane')).toBeNull()
  })

  it('has the lockup, the language pill and a floating theme toggle', () => {
    render(<AuthLayout asideContent={null}>{null}</AuthLayout>)
    expect(screen.getByText(ORG_NAME)).toBeInTheDocument()
    const lang = screen.getByRole('group', { name: 'בחירת שפה' })
    expect(lang).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'עב' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'EN' })).toHaveAttribute('aria-disabled', 'true')
    expect(document.querySelector('.theme-toggle.floating')).toBeInTheDocument()
  })
})
