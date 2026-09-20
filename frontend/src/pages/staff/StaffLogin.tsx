import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { Alert } from '../../components/Alert'
import { AuthLayout } from '../../components/AuthLayout'
import { Button } from '../../components/Button'
import { TextField } from '../../components/TextField'
import { KeyIcon, ShieldCheckIcon } from '../../components/icons'
import { useAuth } from '../../auth/AuthContext'
import { DEMO_PASSWORD } from '../../auth/demo'
import { STAFF_HOME } from '../../routes'

/**
 * The staff sign-in screen, built from `design/ramon-ui/admin-login.html`: the
 * same AuthLayout and side pane, the STAFF ACCESS badge, the scope list and the
 * audit line, all verbatim. The design's phone + Authenticator code are replaced
 * by an id and a password, because the demo IdP is a fixed user list (§18.3), so
 * the side pane's "how to get the code" block becomes a short demo-users note
 * (design §3, decision 2).
 */

/** The §18.3 staff users. The demo password is the same for everyone (`DEMO_PASSWORD`). */
export const STAFF_DEMO_USERS = [
  { user_id: 'coordinator_nurse', display_name: 'אחות מתאמת', role: 'clinical_staff' },
  { user_id: 'admin_coordinator', display_name: 'רכזת מנהלה', role: 'admin_staff' },
] as const

export { DEMO_PASSWORD }

export function StaffLogin() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [userId, setUserId] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const id = userId.trim()
    if (!id || !password) {
      setError('יש להזין מזהה משתמש וסיסמה.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await login(id, password)
      navigate(STAFF_HOME, { replace: true })
    } catch (caught) {
      const detail = caught instanceof ApiError ? caught.detail : 'network_error'
      setError(detail === 'invalid_credentials' ? 'מזהה או סיסמה שגויים.' : 'ההתחברות נכשלה. נסו שוב.')
    } finally {
      setBusy(false)
    }
  }

  function fillDemoUser(demoUserId: string) {
    setUserId(demoUserId)
    setPassword(DEMO_PASSWORD)
    setError(null)
  }

  return (
    <AuthLayout
      aside="side"
      asideContent={
        <div className="side-body">
          <h2 className="side-h">משתמשי דמו</h2>
          <div className="note">
            <p className="note-t">אין אימות דו-שלבי בדמו</p>
            <p className="note-b">
              ספק הזהויות הוא רשימת משתמשים קבועה. משתמשי הצוות הם <span className="mono">coordinator_nurse</span>{' '}
              ו-<span className="mono">admin_coordinator</span>, והסיסמה לכולם היא סיסמת הדמו (ברירת המחדל:{' '}
              <span className="mono">demo</span>).
            </p>
          </div>

          <div className="scope">
            <p className="scope-t">מה מותר מהמסך הזה</p>
            <ul className="scope-l">
              <li>
                <span className="yes">✓</span>אישור, דחייה וסגירה של פניות שהוסלמו
              </li>
              <li>
                <span className="yes">✓</span>צפייה ביומן הביקורת של פנייה
              </li>
              <li>
                <span className="no">✕</span>עריכת תיק רפואי או מסמכים
              </li>
            </ul>
          </div>

          <p className="audit">
            <ShieldCheckIcon />
            כל כניסה, אישור ודחייה נרשמים ביומן הביקורת עם מזהה המשתמש וחותמת זמן.
          </p>
          <p className="foot">גישה בלתי מורשית למערכות רפואיות היא עבירה פלילית. הפעילות מנוטרת.</p>
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
            maxLength={64}
            onChange={(event) => setUserId(event.target.value)}
          />
          <TextField
            label="סיסמה"
            type="password"
            value={password}
            autoComplete="current-password"
            maxLength={256}
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

        <div className="demo-users">
          <p className="scope-t">משתמשי דמו</p>
          <div className="demo-users-row">
            {STAFF_DEMO_USERS.map((user) => (
              <Button key={user.user_id} variant="secondary" onClick={() => fillDemoUser(user.user_id)}>
                {user.display_name} <span className="mono">{user.user_id}</span>
              </Button>
            ))}
          </div>
          <p className="hint">
            הכפתורים ממלאים את המזהה ואת סיסמת הדמו (<span className="mono">demo</span>).
          </p>
        </div>
      </div>
    </AuthLayout>
  )
}
