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

/** A template's text with its `{param}` placeholder replaced - by the chosen option's label
 *  once one is picked, otherwise an ellipsis. The raw `{param}` syntax is never shown. */
function templatePreview(text: string, paramLabel?: string): string {
  return text.replace(/\{[^}]*\}/g, paramLabel ?? '…')
}

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
            {own.map((t) => {
              const isCurrent = value.mode === 'template' && value.template_id === t.template_id
              const paramLabel = isCurrent && value.param ? selected?.options[value.param] : undefined
              return (
                <option key={t.template_id} value={t.template_id}>
                  {templatePreview(t.text, paramLabel)}
                </option>
              )
            })}
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
function localInputValue(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

const MAX_REPLY_WINDOW_DAYS = 7

export function PatientRequest({
  caseId,
  shownContextRef,
  role,
  templates,
  allowsApprove = false,
  onSent,
  onContextChanged,
}: {
  caseId: string
  shownContextRef: string
  role: Role
  templates: MessageTemplate[]
  /** Whether this case's `allowed_decisions` still includes `approve` (design §12). */
  allowsApprove?: boolean
  onSent: () => void
  onContextChanged: () => void
}) {
  const documentSelectId = useId()
  const [kind, setKind] = useState<'question' | 'document'>('question')
  const [choice, setChoice] = useState<MessageChoice>({ mode: 'none' })
  const [documentType, setDocumentType] = useState<DocumentType | ''>('')
  // Empty by default: the server's own default (24h, never past the appointment) only
  // applies when no deadline is sent at all (docs/api.md §8, human_review.py).
  const [deadline, setDeadline] = useState('')
  const [reason, setReason] = useState('')
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [contextChanged, setContextChanged] = useState(false)
  const [busy, setBusy] = useState(false)
  const documents = templates.find((t) => t.purpose === 'document')?.options ?? {}

  async function send() {
    setFieldError(null)
    setError(null)
    setContextChanged(false)

    const trimmedReason = reason.trim()
    if (!trimmedReason) {
      setFieldError(requestErrorLabel('reason_required'))
      return
    }

    if (kind === 'document') {
      if (!documentType) {
        setFieldError('יש לבחור סוג מסמך')
        return
      }
    } else {
      if (choice.mode === 'none') {
        setFieldError('יש לבחור הודעה למטופל')
        return
      }
      if (choice.mode === 'template') {
        const selectedTemplate = templates.find((t) => t.purpose === 'question' && t.template_id === choice.template_id)
        if (selectedTemplate?.param && !choice.param) {
          setFieldError('יש לבחור ערך לפרמטר ההודעה')
          return
        }
      }
      if (choice.mode === 'text' && !choice.text.trim()) {
        setFieldError('יש לכתוב את הטקסט למטופל')
        return
      }
    }

    setBusy(true)
    try {
      await api.requestFromPatient(caseId, {
        kind,
        reason: trimmedReason,
        shown_context_ref: shownContextRef,
        deadline: toIsoWithOffset(deadline) ?? undefined,
        ...(kind === 'document' ? { document_type: documentType || undefined } : toMessageBody(choice)),
      })
      onSent()
    } catch (caught) {
      const detail = detailOf(caught)
      if (detail === 'context_changed') setContextChanged(true)
      else setError(detail)
    } finally {
      setBusy(false)
    }
  }

  const deadlineMin = localInputValue(new Date())
  const deadlineMax = localInputValue(new Date(Date.now() + MAX_REPLY_WINDOW_DAYS * 24 * 3600_000))

  return (
    <section className="patient-request" aria-labelledby="patient-request-h">
      <h3 className="section-h" id="patient-request-h">
        בקשה מהמטופל
      </h3>
      <p className="col-note">
        הפנייה תמתין לתשובת המטופל ותחזור לתור. היא לא תחזור לטיפול אוטומטי.
        {allowsApprove && ' שליחת בקשה מסירה לצמיתות את אפשרות אישור ההמשך.'}
      </p>
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
        label="עד מתי (אופציונלי)"
        type="datetime-local"
        dir="ltr"
        value={deadline}
        min={deadlineMin}
        max={deadlineMax}
        hint="אם ריק: 24 שעות מעכשיו, או מועד התור אם הוא מוקדם יותר. מועד שנבחר: עד 7 ימים ולא אחרי מועד התור."
        onChange={(event) => setDeadline(event.target.value)}
      />
      <TextField
        multiline
        label="סיבת הבקשה (פנימית)"
        value={reason}
        maxLength={2000}
        onChange={(event) => setReason(event.target.value)}
      />
      {fieldError && (
        <Alert variant="error" title="לא ניתן לשלוח">
          {fieldError}
        </Alert>
      )}
      {error && (
        <Alert variant="error" title="הבקשה לא נשלחה">
          {requestErrorLabel(error)} <span className="mono">{error}</span>
        </Alert>
      )}
      {contextChanged && (
        <Alert variant="error" title="ההקשר השתנה">
          <p>ההקשר השתנה מאז שנטען. רעננו אותו, קראו שוב את התוכן ושלחו את הבקשה מחדש אם היא עדיין נדרשת.</p>
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
      <Button variant="secondary" busy={busy} onClick={() => void send()}>
        שליחה למטופל
      </Button>
    </section>
  )
}
