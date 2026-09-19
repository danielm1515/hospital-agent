import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { Alert } from '../../components/Alert'
import { AuthLayout } from '../../components/AuthLayout'
import { Button } from '../../components/Button'
import { TextField } from '../../components/TextField'
import { KeyIcon, ShieldCheckIcon } from '../../components/icons'
import { useAuth } from '../../auth/AuthContext'
import { STAFF_HOME } from '../../App'

/**
 * Placeholder staff login (plan Task 3 builds the full screen from
 * `admin-login.html`: the side pane, the scope list and the demo users).
 */
export function StaffLogin() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [userId, setUserId] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(userId.trim(), password)
      navigate(STAFF_HOME, { replace: true })
    } catch (caught) {
      const detail = caught instanceof ApiError ? caught.detail : 'network_error'
      setError(detail === 'invalid_credentials' ? 'מזהה או סיסמה שגויים.' : 'ההתחברות נכשלה. נסו שוב.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout
      aside="side"
      asideContent={
        <div className="side-body">
          <h2 className="side-h">מה מותר מהמסך הזה</h2>
          <p className="audit">
            <ShieldCheckIcon />
            כל כניסה, אישור ודחייה נרשמים ביומן הביקורת עם מזהה המשתמש וחותמת זמן.
          </p>
        </div>
      }
    >
      <div className="step">
        <p className="badge">
          <span className="key" aria-hidden="true">
            <KeyIcon />
          </span>
          STAFF ACCESS
        </p>
        <h1 className="h1">כניסת צוות רפואי</h1>
        <p className="lede">אזור זה מיועד למאשרי פניות רפואיות.</p>
        <form className="form" onSubmit={submit}>
          <TextField
            label="מזהה משתמש"
            value={userId}
            dir="ltr"
            autoComplete="username"
            onChange={(event) => setUserId(event.target.value)}
          />
          <TextField
            label="סיסמה"
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
          />
          <Button type="submit" variant="primary" busy={busy}>
            כניסה למערכת
          </Button>
        </form>
        {error && (
          <Alert variant="error" title="ההתחברות נכשלה">
            {error}
          </Alert>
        )}
      </div>
    </AuthLayout>
  )
}
