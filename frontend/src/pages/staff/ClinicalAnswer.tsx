import { useState } from 'react'
import * as api from '../../api/client'
import type { Role } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { TextField } from '../../components/TextField'
import { detailOf } from './labels'

/**
 * §5 AnswerClinicalQuestion: a clinical answer to a medical question.
 *
 * What is approved is the text itself - the server binds a ContentApproval (§12.4) to its
 * content_hash - so this form keeps the answer separate from the decision's reason, which
 * stays internal. Only `clinical_staff` may grant that approval; every other role sees the
 * block locked with the reason, rather than not seeing it at all.
 */
const MAX = 2000

export function ClinicalAnswer({
  caseId,
  shownContextRef,
  role,
  onAnswered,
  onContextChanged,
}: {
  caseId: string
  shownContextRef: string
  role: Role
  onAnswered: () => void
  /**
   * Re-fetches the context and the queue (`ReviewCase`'s own `refreshContext`, reused
   * verbatim rather than duplicated here) - the recovery from a `409 context_changed`,
   * kept as a callback so the typed answer and reason, both local state, survive it.
   */
  onContextChanged: () => void
}) {
  const [text, setText] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [contextChanged, setContextChanged] = useState(false)
  const [busy, setBusy] = useState(false)

  if (role !== 'clinical_staff') {
    return (
      <Alert variant="info" title="תשובה למטופל">
        אישור תוכן רפואי הוא של צוות קליני בלבד (§12.4). אפשר לסגור או לדחות את הפנייה.
      </Alert>
    )
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!text.trim() || !reason.trim()) {
      setError('יש לכתוב תשובה וסיבה.')
      return
    }
    setError(null)
    setContextChanged(false)
    setBusy(true)
    try {
      await api.answer(caseId, { answer: text.trim(), reason: reason.trim(), shown_context_ref: shownContextRef })
      onAnswered()
    } catch (caught) {
      if (detailOf(caught) === 'context_changed') setContextChanged(true)
      else setError(answerError(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="form answer-form" onSubmit={submit}>
      <h3 className="col-sub">תשובה למטופל</h3>
      <p className="col-note">
        הטקסט הזה בדיוק הוא מה שיאושר ויוצג למטופל. עריכה אחריו מחייבת אישור חדש.
      </p>
      <TextField
        multiline
        label="התשובה למטופל"
        value={text}
        maxLength={MAX}
        counter
        rows={5}
        onChange={(event) => setText(event.target.value)}
      />
      <TextField
        multiline
        label="סיבה (פנימית)"
        value={reason}
        maxLength={MAX}
        counter
        hint="נשמרת על שתי רשומות האישור וביומן הביקורת. אינה נשלחת למטופל."
        onChange={(event) => setReason(event.target.value)}
      />
      {error && <Alert variant="error">{error}</Alert>}
      {contextChanged && (
        <Alert variant="error" title="ההקשר השתנה">
          <p>ההקשר השתנה מאז שנטען. רעננו את ההקשר וכתבו את התשובה שוב.</p>
          <Button
            variant="secondary"
            onClick={() => {
              setContextChanged(false)
              onContextChanged()
            }}
          >
            רענון הקשר
          </Button>
        </Alert>
      )}
      <div className="actions">
        <Button type="submit" variant="primary" busy={busy}>
          אישור ושליחת התשובה
        </Button>
      </div>
    </form>
  )
}

/** One Hebrew sentence per `docs/api.md` §5 code; an unknown code stays generic. */
function answerError(caught: unknown): string {
  const code = detailOf(caught)
  if (code === 'clinical_staff_only') return 'רק צוות קליני רשאי לאשר תוכן רפואי.'
  if (code === 'decision_not_allowed') return 'לפנייה הזו אי אפשר לשלוח תשובה קלינית.'
  if (code === 'not_in_review') return 'הפנייה כבר אינה ממתינה להכרעה.'
  return 'שליחת התשובה נכשלה. נסו שוב.'
}
