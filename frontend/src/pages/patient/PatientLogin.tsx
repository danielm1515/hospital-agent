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
 * The patient sign-in screen, built from `design/ramon-ui/patient-login.html`:
 * the same AuthLayout, the same brand panel (pattern, eyebrow, heading, copy and
 * facts, verbatim) and the same step-indicator styling. The design's phone + OTP
 * pair is replaced by an id and a password, because the demo IdP is a fixed user
 * list (§18.3, design §3 / decision 2).
 */

/** The §18.3 patient users; the buttons only fill the id field in (design §3). */
const DEMO_PATIENTS = ['P-10041', 'P-20000', 'P-30000'] as const

export function PatientLogin() {
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
      setError('יש להזין מזהה מטופל וסיסמה.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await login(id, password)
      navigate(PATIENT_HOME, { replace: true })
    } catch (caught) {
      const detail = caught instanceof ApiError ? caught.detail : 'network_error'
      setError(
        detail === 'invalid_credentials'
          ? 'מזהה מטופל או סיסמה שגויים. בדקו את הפרטים ונסו שוב.'
          : 'ההתחברות נכשלה. נסו שוב בעוד רגע.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout aside="brand" asideContent={<BrandPane />}>
      <div className="step">
        <div className="steps">
          <span className="dot on" aria-hidden="true" />
          <span className="dot on" aria-hidden="true" />
          <span className="steps-txt">כניסה מאובטחת</span>
        </div>
        <h1 className="h1">כניסה לפניות מטופלים</h1>
        <p className="lede">הזינו את מזהה המטופל ואת הסיסמה שקיבלתם מהמרכז הרפואי.</p>

        <form className="form" onSubmit={submit}>
          <TextField
            label="מזהה מטופל"
            value={userId}
            dir="ltr"
            autoComplete="username"
            placeholder="P-10041"
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

        <div className="demo-users">
          <p className="demo-users-t" id="demo-users-label">
            משתמשי דמו
          </p>
          <div className="demo-users-row" role="group" aria-labelledby="demo-users-label">
            {DEMO_PATIENTS.map((demoId) => (
              <Button key={demoId} variant="quiet" onClick={() => setUserId(demoId)}>
                <span dir="ltr">{demoId}</span>
              </Button>
            ))}
          </div>
          <p className="hint">
            הסיסמה להדגמה היא <span className="code">demo</span>.
          </p>
        </div>

        {error && (
          <Alert variant="error" title="ההתחברות נכשלה">
            {error}
          </Alert>
        )}
      </div>
    </AuthLayout>
  )
}

/** `patient-login.html`'s brand panel: the pattern, the copy and the facts, verbatim. */
function BrandPane() {
  return (
    <>
      <svg className="pattern" viewBox="0 0 420 760" aria-hidden="true" preserveAspectRatio="xMidYMid slice" focusable="false">
        <rect x="48" y="96" width="120" height="120" rx="18" fill="var(--brand-500)" opacity=".5" />
        <rect x="184" y="96" width="56" height="120" rx="18" fill="var(--teal-500)" opacity=".55" />
        <rect x="48" y="232" width="192" height="56" rx="18" fill="#ffffff" opacity=".12" />
        <circle cx="300" cy="156" r="28" fill="var(--teal-500)" opacity=".4" />
        <path
          d="M48 620h48l24-48 32 96 24-48h96"
          fill="none"
          stroke="var(--teal-500)"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity=".6"
        />
      </svg>

      <div className="brand-body">
        <p className="eyebrow">PATIENT SERVICES</p>
        <p className="brand-h">
          פנייה אחת, מענה אחד,
          <br />
          בלי להמתין על הקו.
        </p>
        <p className="brand-p">
          הסוכן הדיגיטלי אוסף את הפרטים, מאתר את המסמכים בתיק שלכם ומעביר לרופא רק את מה שדורש החלטה רפואית.
        </p>

        <ul className="facts">
          <li>
            <span className="dotlive" aria-hidden="true" />
            זמין בכל שעה, כל ימות השבוע
          </li>
          <li>
            <span className="tick" aria-hidden="true">
              ✓
            </span>
            כל פנייה רפואית עוברת אישור אדם
          </li>
          <li>
            <span className="tick" aria-hidden="true">
              ✓
            </span>
            הפרטים נשמרים בתיק הרפואי בלבד
          </li>
        </ul>
      </div>
    </>
  )
}
