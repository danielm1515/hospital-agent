import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import * as api from '../../api/client'
import type { Appointment } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { Loading } from '../../components/Loading'
import { TextField } from '../../components/TextField'
import { ErrorIcon } from '../../components/icons'
import { MY_REQUESTS, requestPath } from './paths'
import { errorMessage } from './helpers'
import { appointmentOptionLabel, suggestOtherAppointment, upcomingScheduled } from './appointmentChoice'

/** `POST /api/patient/requests` trims the text and then requires 1-2000 characters. */
const MAX_LENGTH = 2000

/** Sub-project 18 (design D5): the picker offers the patient's next 90 days. */
const PICKER_DAYS = 90
const ONE_DAY_MS = 24 * 60 * 60 * 1000

/**
 * The "nearest appointment" option's value. It can never be an appointment id: every id
 * starts with a letter or digit (`docs/api.md` §4), and this starts with `*`. It is never
 * sent - "the nearest" is the absence of `appointment_id`, not a value.
 */
const NEAREST = '*nearest'
/** The placeholder's value, when choosing is mandatory. */
const UNCHOSEN = ''

type AppointmentChoices = { phase: 'loading' } | { phase: 'failed' } | { phase: 'ready'; appointments: Appointment[] }

/**
 * "פנייה חדשה" (design §4): one multiline field with a counter, and since sub-project 18
 * (design D5) "לאיזה תור הפנייה?" - the patient's upcoming Scheduled appointments. One
 * appointment is pre-selected, with "התור הקרוב ביותר" beside it; with more than one the
 * choice is mandatory; a list that fails to load leaves only "the nearest". Before sending,
 * a deterministic check (`appointmentChoice.ts`) offers to switch when the text seems to be
 * about another of the patient's appointments - the patient may switch or send as is. The
 * text is sent trimmed, and the case the server answers with decides where we go next - the
 * screen assumes nothing (`docs/api.md` §6, "nothing is optimistic").
 */
export function NewRequest() {
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [choices, setChoices] = useState<AppointmentChoices>({ phase: 'loading' })
  const [selected, setSelected] = useState(NEAREST)
  const [choiceError, setChoiceError] = useState<string | null>(null)
  const [suggestion, setSuggestion] = useState<Appointment | null>(null)

  useEffect(() => {
    // Guarded: an answer that lands after the screen is gone (or after StrictMode's
    // throw-away first mount) never touches state.
    let cancelled = false
    const from = new Date()
    const to = new Date(from.getTime() + PICKER_DAYS * ONE_DAY_MS)
    Promise.resolve()
      .then(() => api.listMyAppointments(from, to))
      .then((answer) => {
        if (cancelled) return
        const appointments = upcomingScheduled(answer.appointments)
        setChoices({ phase: 'ready', appointments })
        setSelected(
          appointments.length === 1 ? appointments[0].appointment_id : appointments.length > 1 ? UNCHOSEN : NEAREST,
        )
      })
      .catch(() => {
        if (cancelled) return
        setChoices({ phase: 'failed' })
        setSelected(NEAREST)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const appointments = choices.phase === 'ready' ? choices.appointments : []
  // "התור הקרוב ביותר" only while there is at most one upcoming appointment (design D5).
  const offerNearest = appointments.length <= 1

  async function send(trimmed: string, appointmentId: string | undefined) {
    setSuggestion(null)
    setError(null)
    setBusy(true)
    try {
      const view = await api.createRequest(trimmed, appointmentId)
      navigate(requestPath(view.case_id), { replace: true })
    } catch (caught) {
      setError(errorMessage(caught))
      setBusy(false)
    }
  }

  function chosenId(): string | undefined {
    return selected === NEAREST || selected === UNCHOSEN ? undefined : selected
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (choices.phase === 'loading') return
    const trimmed = text.trim()
    setFieldError(trimmed ? null : 'יש לכתוב את תוכן הפנייה.')
    const mustChoose = selected === UNCHOSEN
    setChoiceError(mustChoose ? 'יש לבחור את התור שהפנייה עוסקת בו.' : null)
    if (!trimmed || mustChoose) return

    const id = chosenId()
    const chosen = appointments.find((appointment) => appointment.appointment_id === id)
    if (chosen) {
      const other = suggestOtherAppointment(trimmed, chosen, appointments)
      if (other) {
        setSuggestion(other)
        return
      }
    }
    await send(trimmed, id)
  }

  function choose(value: string) {
    setSelected(value)
    setChoiceError(null)
    setSuggestion(null)
  }

  const choiceHint =
    choices.phase === 'failed'
      ? 'לא הצלחנו לטעון את רשימת התורים, ולכן הפנייה תתייחס לתור הקרוב ביותר.'
      : choices.phase === 'ready' && appointments.length === 0
        ? 'לא נמצאו תורים מתוכננים ב־90 הימים הקרובים.'
        : null

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
        {choices.phase === 'loading' ? (
          <Loading size="inline" label="טוען את התורים שלך" />
        ) : (
          <div className="field">
            <label className="label" htmlFor="appointment-choice">
              לאיזה תור הפנייה?
            </label>
            <div className={choiceError ? 'control invalid' : 'control'}>
              <select
                id="appointment-choice"
                className="input select"
                value={selected}
                aria-invalid={choiceError ? true : undefined}
                aria-describedby={choiceError || choiceHint ? 'appointment-choice-hint' : undefined}
                onChange={(event) => choose(event.target.value)}
              >
                {!offerNearest && (
                  <option value={UNCHOSEN} disabled>
                    יש לבחור תור
                  </option>
                )}
                {appointments.map((appointment) => (
                  <option key={appointment.appointment_id} value={appointment.appointment_id}>
                    {appointmentOptionLabel(appointment)}
                  </option>
                ))}
                {offerNearest && <option value={NEAREST}>התור הקרוב ביותר</option>}
              </select>
            </div>
            {choiceError ? (
              <div className="hint-row">
                <p className="hint error" id="appointment-choice-hint">
                  <ErrorIcon className="hint-ico" size={16} />
                  {choiceError}
                </p>
              </div>
            ) : (
              choiceHint && (
                <div className="hint-row">
                  <p className="hint" id="appointment-choice-hint">
                    {choiceHint}
                  </p>
                </div>
              )
            )}
          </div>
        )}

        <TextField
          multiline
          label="תוכן הפנייה"
          value={text}
          maxLength={MAX_LENGTH}
          counter
          rows={7}
          error={fieldError ?? undefined}
          hint="עד 2000 תווים."
          onChange={(event) => {
            setText(event.target.value)
            setSuggestion(null)
          }}
        />

        {suggestion && (
          <Alert variant="warn">
            <p className="message-text">{`נראה שכתבת על ${appointmentOptionLabel(suggestion)} - לעבור לתור הזה?`}</p>
            <div className="actions">
              <Button
                variant="secondary"
                busy={busy}
                onClick={() => {
                  setSelected(suggestion.appointment_id)
                  void send(text.trim(), suggestion.appointment_id)
                }}
              >
                כן, לעבור לתור הזה
              </Button>
              <Button variant="quiet" busy={busy} onClick={() => void send(text.trim(), chosenId())}>
                לא, לשלוח כמו שזה
              </Button>
            </div>
          </Alert>
        )}

        <div className="actions">
          <Button type="submit" variant="primary" busy={busy} disabled={choices.phase === 'loading'}>
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
