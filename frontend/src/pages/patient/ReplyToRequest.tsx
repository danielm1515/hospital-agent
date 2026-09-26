import { useRef, useState } from 'react'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import type { ConversationEntry, DocumentType, PatientView } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { TextField } from '../../components/TextField'
import {
  describeDocumentType,
  errorMessage,
  FILE_TOO_LARGE_MESSAGE,
  formatDateTime,
  isAcceptedDocumentFile,
  MAX_UPLOAD_BYTES,
  UNSUPPORTED_FILE_MESSAGE,
  uploadResultMessage,
} from './helpers'
import type { UploadNotice } from './helpers'

/**
 * Sub-project 15 (design §7.5, `docs/api.md` §8): the patient's answer to a staff request -
 * text for a question, the requested document (PDF, JPEG or PNG, sub-project 17 task 2) for a
 * document. The patient sees the message and the deadline, never why it was asked or what
 * escalation is behind it (§12.3).
 */

/** `POST .../reply` accepts 1-2000 characters of trimmed text (`docs/api.md` §8). */
const MAX_REPLY_LENGTH = 2000

/** The two codes that mean the request itself is stale - answered, timed out, or the wrong
 * kind - so the view under the form is out of date and must be replaced, not just annotated
 * with an error. */
function isStaleReply(caught: unknown): boolean {
  return caught instanceof ApiError && (caught.detail === 'not_waiting_for_reply' || caught.detail === 'reply_kind_mismatch')
}

/**
 * Re-fetches the case after a stale reply attempt, so the screen reflects what actually
 * happened (already answered, timed out, or answered a different way) instead of leaving a
 * form for a request that no longer exists. A failed refresh is swallowed - the notice the
 * caller already showed is the only thing the patient needs to see.
 */
async function refreshAfterStaleReply(caseId: string, onChanged: (next: PatientView) => void): Promise<void> {
  try {
    onChanged(await api.getRequest(caseId))
  } catch {
    /* the caller's notice already told the patient something went wrong */
  }
}

export function ReplyToRequest({
  view,
  onChanged,
  onNotice,
}: {
  view: PatientView
  onChanged: (next: PatientView) => void
  /** Lifted to `RequestDetail`, like `MissingDocuments`' `onUploadNotice`: a reply that
   * moves the case out of `needs_reply` unmounts this whole section, so a notice that lived
   * only in local state here would vanish with it (the same reason `FileUploadForm` lifts
   * its own upload notice). */
  onNotice: (notice: UploadNotice | null) => void
}) {
  const request = view.reply_request
  if (!request) {
    // Fail closed (§14), the same pattern `MissingDocuments` uses for an unrecognised
    // template id: `needs_reply` with no request to show (a stale poll racing a decision)
    // still gives the patient somewhere to go, instead of a blank section.
    return <p className="muted">הפנייה ממתינה לתשובה. פנו למוקד המטופלים להמשך טיפול.</p>
  }
  return (
    <section className="reply-request" aria-labelledby="reply-request-h">
      <h2 className="section-h" id="reply-request-h">
        בקשה מהצוות
      </h2>
      {request.message && <p className="message-text">{request.message}</p>}
      <p className="muted">נא להשיב עד {formatDateTime(request.deadline)}.</p>
      {request.kind === 'question' ? (
        <TextReply caseId={view.case_id} onChanged={onChanged} onNotice={onNotice} />
      ) : (
        <FileReply caseId={view.case_id} documentType={request.document_type} onChanged={onChanged} onNotice={onNotice} />
      )}
    </section>
  )
}

function TextReply({
  caseId,
  onChanged,
  onNotice,
}: {
  caseId: string
  onChanged: (next: PatientView) => void
  onNotice: (notice: UploadNotice | null) => void
}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [fieldError, setFieldError] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = text.trim()
    if (!trimmed) {
      setFieldError('יש לכתוב תשובה לפני השליחה.')
      return
    }
    setFieldError(null)
    onNotice(null)
    setBusy(true)
    try {
      onChanged(await api.replyToRequest(caseId, trimmed))
    } catch (caught) {
      if (isStaleReply(caught)) {
        onNotice({ variant: 'error', text: errorMessage(caught) })
        await refreshAfterStaleReply(caseId, onChanged)
      } else {
        setFieldError(errorMessage(caught))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="form" onSubmit={(event) => void submit(event)}>
      <TextField
        multiline
        label="התשובה שלך"
        value={text}
        maxLength={MAX_REPLY_LENGTH}
        counter
        error={fieldError}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="actions">
        <Button type="submit" variant="primary" busy={busy}>
          שליחת התשובה
        </Button>
      </div>
    </form>
  )
}

function FileReply({
  caseId,
  documentType,
  onChanged,
  onNotice,
}: {
  caseId: string
  documentType: DocumentType | null
  onChanged: (next: PatientView) => void
  onNotice: (notice: UploadNotice | null) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function chooseFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    setFileName(file ? file.name : null)
    setError(null)
    // A previous attempt's lifted notice (e.g. `wrong_document_type`) is about the file
    // that was sent, not this new one - it must not linger once the patient moves on.
    onNotice(null)
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const file = inputRef.current?.files?.[0]
    if (!file || !isAcceptedDocumentFile(file)) {
      setError(UNSUPPORTED_FILE_MESSAGE)
      return
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(FILE_TOO_LARGE_MESSAGE)
      return
    }
    setError(null)
    onNotice(null)
    setBusy(true)
    try {
      const { upload, request } = await api.replyWithFile(caseId, file)
      // Like `FileUploadForm`: the view is always replaced with `request` and the outcome
      // sentence always shown, on every code, not only `accepted` - for every other code
      // `request` is exactly what it was before (`docs/api.md` §8), so this is a no-op
      // re-render when nothing moved, and the real thing when it did.
      onChanged(request)
      onNotice(uploadResultMessage(upload))
      setFileName(null)
      if (inputRef.current) inputRef.current.value = ''
    } catch (caught) {
      if (isStaleReply(caught)) {
        onNotice({ variant: 'error', text: errorMessage(caught) })
        await refreshAfterStaleReply(caseId, onChanged)
      } else {
        setError(errorMessage(caught))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="upload" aria-label="העלאת מסמך (PDF או תמונה)">
      <h3 className="section-h">העלאת מסמך (PDF או תמונה)</h3>
      {documentType && <p className="muted">המסמך המבוקש: {describeDocumentType(documentType)}</p>}

      <form className="form" onSubmit={(event) => void submit(event)}>
        <div className="field">
          <label className="label" htmlFor="reply-file-upload">
            בחירת קובץ
          </label>
          <input
            id="reply-file-upload"
            ref={inputRef}
            className="file-input"
            type="file"
            accept="application/pdf,.pdf,image/jpeg,.jpg,.jpeg,image/png,.png"
            disabled={busy}
            onChange={chooseFile}
          />
          <p className="hint">
            {fileName ? `נבחר הקובץ ${fileName}.` : 'אפשר להעלות קובץ PDF או תמונה (JPG/PNG) אחד, עד 10MB.'}
          </p>
        </div>

        {error && <Alert variant="error">{error}</Alert>}

        <div className="actions">
          <Button type="submit" variant="primary" busy={busy}>
            העלאת המסמך
          </Button>
        </div>
      </form>
    </section>
  )
}

/** The staff messages the patient may see and their own replies, oldest first (§7.5). */
export function Conversation({ entries }: { entries: ConversationEntry[] }) {
  if (entries.length === 0) return null
  return (
    <section className="conversation" aria-labelledby="conversation-h">
      <h2 className="section-h" id="conversation-h">
        ההתכתבות עם הצוות
      </h2>
      <ol className="conversation-list">
        {entries.map((entry, index) => (
          <li key={index} className={`conversation-item from-${entry.sender}`}>
            {/* Fail closed: only an exact `'patient'` sender is ever labelled as the
                patient's own words - anything else, including a value this version does
                not recognise, is shown as the staff's (§12.3 - never invent who said what). */}
            <span className="conversation-who">{entry.sender === 'patient' ? 'אני' : 'צוות בית החולים'}</span>
            <span className="message-text">{entry.text}</span>
            <time className="muted" dateTime={entry.at}>
              {formatDateTime(entry.at)}
            </time>
          </li>
        ))}
      </ol>
    </section>
  )
}
