import { useId, useState } from 'react'
import * as api from '../../api/client'
import type { DocumentType, MessageBody, MessageTemplate, Role } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { TextField } from '../../components/TextField'
import { detailOf, requestErrorLabel, toIsoWithOffset } from './labels'

/**
 * Sub-project 15 (design §7, §12): ask the patient a question or for one catalog document.
 * The texts come from the server's templates; free text is offered to clinical_staff only,
 * because only they may grant the ContentApproval it needs (§12.4). The reason stays internal.
 */
export type MessageChoice =
  | { mode: 'none' }
  | { mode: 'template'; template_id: string; param?: string }
  | { mode: 'text'; text: string }

export function toMessageBody(choice: MessageChoice): MessageBody | undefined {
  if (choice.mode === 'template') return { template_id: choice.template_id, param: choice.param }
  if (choice.mode === 'text') return { text: choice.text }
  return undefined
}

const FREE_TEXT = 'text'

/** One select for a template of `purpose` (or free text, for clinical staff), plus its parameter. */
export function MessagePicker({
  purpose,
  role,
  templates,
  value,
  onChange,
  allowNone = false,
}: {
  purpose: 'question' | 'closing'
  role: Role
  templates: MessageTemplate[]
  value: MessageChoice
  onChange: (next: MessageChoice) => void
  allowNone?: boolean
}) {
  const selectId = useId()
  const paramId = useId()
  const own = templates.filter((t) => t.purpose === purpose)
  const selected = value.mode === 'template' ? own.find((t) => t.template_id === value.template_id) : undefined
  const current = value.mode === 'template' ? value.template_id : value.mode === 'text' ? FREE_TEXT : ''
  return (
    <>
      <div className="field">
        <label className="label" htmlFor={selectId}>
          הודעה
        </label>
        <div className="control">
          <select
            id={selectId}
            className="input select"
            value={current}
            onChange={(event) => {
              const next = event.target.value
              if (next === '') onChange({ mode: 'none' })
              else if (next === FREE_TEXT) onChange({ mode: 'text', text: '' })
              else onChange({ mode: 'template', template_id: next })
            }}
          >
            <option value="">{allowNone ? 'בלי הודעה' : 'בחירת הודעה…'}</option>
            {own.map((t) => (
              <option key={t.template_id} value={t.template_id}>
                {t.text}
              </option>
            ))}
            {role === 'clinical_staff' && <option value={FREE_TEXT}>טקסט חופשי (צוות קליני)</option>}
          </select>
        </div>
      </div>
      {selected?.param && value.mode === 'template' && (
        <div className="field">
          <label className="label" htmlFor={paramId}>
            {selected.param === 'topic' ? 'נושא' : 'פרמטר'}
          </label>
          <div className="control">
            <select
              id={paramId}
              className="input select"
              value={value.param ?? ''}
              onChange={(event) => onChange({ ...value, param: event.target.value || undefined })}
            >
              <option value="">בחירה…</option>
              {Object.entries(selected.options).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}
      {value.mode === 'text' && (
        <TextField
          multiline
          label="הטקסט למטופל"
          value={value.text}
          maxLength={2000}
          counter
          hint="נשלח למטופל כפי שנכתב, עם אישור תוכן (ContentApproval) שלך."
          onChange={(event) => onChange({ mode: 'text', text: event.target.value })}
        />
      )}
    </>
  )
}

const pad = (n: number) => String(n).padStart(2, '0')
function inTwentyFourHours(): string {
  const d = new Date(Date.now() + 24 * 3600_000)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function PatientRequest({
  caseId,
  shownContextRef,
  role,
  templates,
  onSent,
  onContextChanged,
}: {
  caseId: string
  shownContextRef: string
  role: Role
  templates: MessageTemplate[]
  onSent: () => void
  onContextChanged: () => void
}) {
  const documentSelectId = useId()
  const [kind, setKind] = useState<'question' | 'document'>('question')
  const [choice, setChoice] = useState<MessageChoice>({ mode: 'none' })
  const [documentType, setDocumentType] = useState<DocumentType | ''>('')
  const [deadline, setDeadline] = useState(inTwentyFourHours)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const documents = templates.find((t) => t.purpose === 'document')?.options ?? {}

  async function send() {
    setBusy(true)
    setError(null)
    try {
      await api.requestFromPatient(caseId, {
        kind,
        reason,
        shown_context_ref: shownContextRef,
        deadline: toIsoWithOffset(deadline) ?? undefined,
        ...(kind === 'document' ? { document_type: documentType || undefined } : toMessageBody(choice)),
      })
      onSent()
    } catch (caught) {
      const detail = detailOf(caught)
      if (detail === 'context_changed') onContextChanged()
      setError(detail)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="patient-request" aria-labelledby="patient-request-h">
      <h3 className="section-h" id="patient-request-h">
        בקשה מהמטופל
      </h3>
      <p className="col-note">הפנייה תמתין לתשובת המטופל ותחזור לתור. היא לא תחזור לטיפול אוטומטי.</p>
      <div className="patient-request-kind" role="radiogroup" aria-label="סוג הבקשה">
        <label>
          <input type="radio" name="request-kind" checked={kind === 'question'} onChange={() => setKind('question')} />
          שאלת הבהרה
        </label>
        <label>
          <input type="radio" name="request-kind" checked={kind === 'document'} onChange={() => setKind('document')} />
          בקשת מסמך
        </label>
      </div>
      {kind === 'question' ? (
        <MessagePicker purpose="question" role={role} templates={templates} value={choice} onChange={setChoice} />
      ) : (
        <div className="field">
          <label className="label" htmlFor={documentSelectId}>
            סוג המסמך
          </label>
          <div className="control">
            <select
              id={documentSelectId}
              className="input select"
              value={documentType}
              onChange={(event) => setDocumentType(event.target.value as DocumentType)}
            >
              <option value="">בחירה…</option>
              {Object.entries(documents).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}
      <TextField
        label="עד מתי (ברירת מחדל: 24 שעות)"
        type="datetime-local"
        dir="ltr"
        value={deadline}
        onChange={(event) => setDeadline(event.target.value)}
      />
      <TextField
        multiline
        label="סיבה (פנימית)"
        value={reason}
        maxLength={2000}
        onChange={(event) => setReason(event.target.value)}
      />
      {error && (
        <Alert variant="error" title="הבקשה לא נשלחה">
          {requestErrorLabel(error)} <span className="mono">{error}</span>
        </Alert>
      )}
      <Button variant="secondary" busy={busy} onClick={() => void send()}>
        שליחה למטופל
      </Button>
    </section>
  )
}
