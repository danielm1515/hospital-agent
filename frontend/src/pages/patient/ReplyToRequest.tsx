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
  isPdfFile,
  MAX_UPLOAD_BYTES,
  NOT_PDF_MESSAGE,
  uploadResultMessage,
} from './helpers'

/**
 * Sub-project 15 (design §7.5, `docs/api.md` §8): the patient's answer to a staff request -
 * text for a question, the requested PDF for a document. The patient sees the message and
 * the deadline, never why it was asked or what escalation is behind it (§12.3).
 */

/** `POST .../reply` accepts 1-2000 characters of trimmed text (`docs/api.md` §8). */
const MAX_REPLY_LENGTH = 2000

export function ReplyToRequest({ view, onChanged }: { view: PatientView; onChanged: (next: PatientView) => void }) {
  const request = view.reply_request
  if (!request) return null
  return (
    <section className="reply-request" aria-labelledby="reply-request-h">
      <h2 className="section-h" id="reply-request-h">
        בקשה מהצוות
      </h2>
      {request.message && <p className="message-text">{request.message}</p>}
      <p className="muted">נא להשיב עד {formatDateTime(request.deadline)}.</p>
      {request.kind === 'question' ? (
        <TextReply caseId={view.case_id} onChanged={onChanged} />
      ) : (
        <FileReply caseId={view.case_id} documentType={request.document_type} onChanged={onChanged} />
      )}
    </section>
  )
}

function TextReply({ caseId, onChanged }: { caseId: string; onChanged: (next: PatientView) => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = text.trim()
    if (!trimmed) {
      setError('יש לכתוב תשובה לפני השליחה.')
      return
    }
    setError(null)
    setBusy(true)
    try {
      onChanged(await api.replyToRequest(caseId, trimmed))
    } catch (caught) {
      setError(errorMessage(caught))
      if (caught instanceof ApiError && caught.detail === 'not_waiting_for_reply') {
        // The case moved on (already answered, timed out) while the patient was typing -
        // refresh so the screen shows the state it is actually in, not a stale request.
        try {
          onChanged(await api.getRequest(caseId))
        } catch {
          /* the error above already told the patient something went wrong */
        }
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
        onChange={(event) => setText(event.target.value)}
      />
      {error && <Alert variant="error">{error}</Alert>}
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
}: {
  caseId: string
  documentType: DocumentType | null
  onChanged: (next: PatientView) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function chooseFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    setFileName(file ? file.name : null)
    setError(null)
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const file = inputRef.current?.files?.[0]
    if (!file || !isPdfFile(file)) {
      setError(NOT_PDF_MESSAGE)
      return
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(FILE_TOO_LARGE_MESSAGE)
      return
    }
    setError(null)
    setBusy(true)
    try {
      const { upload, request } = await api.replyWithFile(caseId, file)
      // Only `accepted` moves the case out of `needs_reply` (`docs/api.md` §8); every other
      // code leaves it exactly as it was, so there is nothing new to show but the sentence.
      if (upload.code === 'accepted') onChanged(request)
      else setError(uploadResultMessage(upload).text)
      setFileName(null)
      if (inputRef.current) inputRef.current.value = ''
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="upload" aria-label="העלאת מסמך PDF">
      <h2 className="section-h">העלאת מסמך PDF</h2>
      {documentType && <p className="muted">המסמך המבוקש: {describeDocumentType(documentType)}</p>}

      <form className="form" onSubmit={(event) => void submit(event)}>
        <div className="field">
          <label className="label" htmlFor="reply-file-upload">
            בחירת קובץ PDF
          </label>
          <input
            id="reply-file-upload"
            ref={inputRef}
            className="file-input"
            type="file"
            accept="application/pdf,.pdf"
            onChange={chooseFile}
          />
          <p className="hint">
            {fileName ? `נבחר הקובץ ${fileName}.` : 'אפשר להעלות קובץ PDF אחד, עד 10MB.'}
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
    <section className="conversation" aria-label="ההתכתבות עם הצוות">
      <ol className="conversation-list">
        {entries.map((entry, index) => (
          <li key={index} className={`conversation-item from-${entry.sender}`}>
            <span className="conversation-who">{entry.sender === 'staff' ? 'צוות בית החולים' : 'אני'}</span>
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
