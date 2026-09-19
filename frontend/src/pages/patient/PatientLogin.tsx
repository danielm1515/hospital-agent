import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { Alert } from '../../components/Alert'
import { AuthLayout } from '../../components/AuthLayout'
import { Button } from '../../components/Button'
import { TextField } from '../../components/TextField'
import { useAuth } from '../../auth/AuthContext'
import { PATIENT_HOME } from '../../App'

/**
 * Placeholder patient login (plan Task 2 builds the full screen from
 * `patient-login.html`: the brand pane, the step indicator and the demo users).
 * It already signs in, so the route guards can be used end to end.
 */
export function PatientLogin() {
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
      navigate(PATIENT_HOME, { replace: true })
    } catch (caught) {
      const detail = caught instanceof ApiError ? caught.detail : 'network_error'
      setError(detail === 'invalid_credentials' ? 'מזהה או סיסמה שגויים.' : 'ההתחברות נכשלה. נסו שוב.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout
      aside="brand"
      asideContent={
        <div className="brand-body">
          <p className="eyebrow">PATIENT SERVICES</p>
          <p className="brand-h">
            פנייה אחת, מענה אחד,
            <br />
            בלי להמתין על הקו.
          </p>
        </div>
      }
    >
      <div className="step">
        <h1 className="h1">כניסה לפניות מטופלים</h1>
        <p className="lede">הזינו את המזהה והסיסמה שקיבלתם.</p>
        <form className="form" onSubmit={submit}>
          <TextField
            label="מזהה מטופל"
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
