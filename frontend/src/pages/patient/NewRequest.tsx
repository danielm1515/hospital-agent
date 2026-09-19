import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import * as api from '../../api/client'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { TextField } from '../../components/TextField'
import { MY_REQUESTS, requestPath } from './paths'
import { errorMessage } from './helpers'

/** `POST /api/patient/requests` trims the text and then requires 1-2000 characters. */
const MAX_LENGTH = 2000

/**
 * "פנייה חדשה" (design §4): one multiline field with a counter. The text is sent
 * trimmed, and the case the server answers with decides where we go next - the
 * screen assumes nothing (`docs/api.md` §6, "nothing is optimistic").
 */
export function NewRequest() {
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = text.trim()
    if (!trimmed) {
      setFieldError('יש לכתוב את תוכן הפנייה.')
      return
    }
    setFieldError(null)
    setError(null)
    setBusy(true)
    try {
      const view = await api.createRequest(trimmed)
      navigate(requestPath(view.case_id), { replace: true })
    } catch (caught) {
      setError(errorMessage(caught))
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <h1 className="page-h">פנייה חדשה</h1>
      <p className="lede">כתבו במילים שלכם במה אפשר לעזור. הסוכן מטפל בפניות תפעוליות; שאלה רפואית מועברת לצוות.</p>

      {error && (
        <Alert variant="error" title="הפנייה לא נשלחה">
          {error}
        </Alert>
      )}

      <form className="form" onSubmit={submit}>
        <TextField
          multiline
          label="תוכן הפנייה"
          value={text}
          maxLength={MAX_LENGTH}
          counter
          rows={7}
          error={fieldError ?? undefined}
          hint="עד 2000 תווים."
          onChange={(event) => setText(event.target.value)}
        />
        <div className="actions">
          <Button type="submit" variant="primary" busy={busy}>
            שליחת הפנייה
          </Button>
          <Button variant="quiet" onClick={() => navigate(MY_REQUESTS)}>
            ביטול
          </Button>
        </div>
      </form>
    </section>
  )
}
