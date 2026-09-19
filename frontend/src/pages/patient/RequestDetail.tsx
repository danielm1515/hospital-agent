import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import * as api from '../../api/client'
import { DOCUMENT_FORMATS } from '../../api/types'
import type { DocumentFormat, PatientStatus, PatientView } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { StatusPill } from '../../components/StatusPill'
import { TextField } from '../../components/TextField'
import { MY_REQUESTS } from './paths'
import { documentLabel, errorMessage, formatDateTime, isMoving, usePolling } from './helpers'

/**
 * One request (design §4). Everything on the screen comes from the patient view
 * of `docs/api.md` §4: a status timeline, and per status the missing-document
 * request (D24), the delivered message, the review notice or the closing line.
 * An escalation kind, a policy reason or an audit row is never shown (§12.3).
 */

/** D24: the one template the request for a document may be rendered from. */
const MISSING_DOCUMENT_TEMPLATE = 'missing-document-v1'

/** `POST .../documents` accepts 1-20000 characters of text (the demo has no binary upload). */
const MAX_CONTENT = 20000

export function RequestDetail() {
  const { caseId = '' } = useParams()
  const [view, setView] = useState<PatientView | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const next = await api.getRequest(caseId)
      setView(next)
      setError(null)
    } catch (caught) {
      setError(errorMessage(caught))
    }
  }, [caseId])

  useEffect(() => {
    void load()
  }, [load])

  usePolling(view !== null && isMoving(view.status), () => void load())

  return (
    <section className="card">
      <div className="page-head">
        <h1 className="page-h">פרטי פנייה</h1>
        <Link className="link" to={MY_REQUESTS}>
          חזרה לפניות שלי
        </Link>
      </div>

      {error && (
        <Alert variant="error" title="לא הצלחנו לטעון את הפנייה">
          {error}
        </Alert>
      )}

      {view === null ? (
        !error && (
          <p className="page-loading" role="status">
            טוען…
          </p>
        )
      ) : (
        <>
          <div className="req-head">
            <StatusPill status={view.status} />
            <span className="code" dir="ltr">
              {view.case_id}
            </span>
            <time className="req-date" dateTime={view.created_at}>
              {formatDateTime(view.created_at)}
            </time>
          </div>

          <Timeline status={view.status} />

          <section className="req-body">
            <h2 className="section-h">הפנייה שלכם</h2>
            <p className="req-full">{view.request_text ?? 'תוכן הפנייה נמחק מהמערכת.'}</p>
          </section>

          <StatusContent view={view} onChanged={setView} />
        </>
      )}
    </section>
  )
}

// ---- Timeline --------------------------------------------------------------

type StepState = 'done' | 'current' | 'todo'

const FINAL_LABELS: Partial<Record<PatientStatus, string>> = {
  needs_document: 'ממתינה למסמך',
  in_review: 'אצל צוות',
  completed: 'הושלמה',
  closed: 'נסגרה',
}

function timelineFor(status: PatientStatus): Array<{ label: string; state: StepState }> {
  const final = FINAL_LABELS[status]
  return [
    { label: 'התקבלה', state: status === 'received' ? 'current' : 'done' },
    {
      label: 'בטיפול',
      state: status === 'in_progress' ? 'current' : status === 'received' ? 'todo' : 'done',
    },
    { label: final ?? 'סיום', state: final ? 'current' : 'todo' },
  ]
}

function Timeline({ status }: { status: PatientStatus }) {
  return (
    <ol className="timeline" aria-label="מצב הפנייה">
      {timelineFor(status).map((step) => (
        <li
          key={step.label}
          className={`tl-step ${step.state}`}
          aria-current={step.state === 'current' ? 'step' : undefined}
        >
          <span className="tl-dot" aria-hidden="true" />
          {step.label}
        </li>
      ))}
    </ol>
  )
}

// ---- Content per status ----------------------------------------------------

function StatusContent({ view, onChanged }: { view: PatientView; onChanged: (next: PatientView) => void }) {
  switch (view.status) {
    case 'needs_document':
      return <MissingDocuments view={view} onChanged={onChanged} />
    case 'completed':
      return (
        <Alert variant="ok" title="ההודעה שנשלחה אליך">
          <span className="message-text">{view.message}</span>
        </Alert>
      )
    case 'in_review':
      return <Alert variant="info">הפנייה הועברה לבדיקת צוות. מידע רפואי אינו נמסר באופן אוטומטי.</Alert>
    case 'closed':
      return <p className="muted">הפנייה נסגרה.</p>
    default:
      return (
        <p className="muted" role="status">
          הפנייה בטיפול. המסך מתעדכן מעצמו.
        </p>
      )
  }
}

/**
 * D24: the request for a missing document is rendered here, from the template id
 * the server sent - never sent to the patient by the system. An unknown template
 * id shows nothing but a neutral line (fail closed, §14).
 */
function MissingDocuments({ view, onChanged }: { view: PatientView; onChanged: (next: PatientView) => void }) {
  if (view.missing_document_request_template_id !== MISSING_DOCUMENT_TEMPLATE) {
    return <p className="muted">הפנייה ממתינה למסמך. פנו למוקד המטופלים להמשך טיפול.</p>
  }
  return (
    <>
      <Alert variant="warn" title="כדי להשלים את ההכנה לתור חסרים המסמכים הבאים:">
        <ul className="doc-list">
          {view.missing_document_ids.map((documentId) => (
            <li key={documentId}>
              <span className="code" dir="ltr">
                {documentId}
              </span>
              {documentLabel(documentId) && <span className="doc-name">{documentLabel(documentId)}</span>}
            </li>
          ))}
        </ul>
      </Alert>

      {view.missing_document_ids.map((documentId) => (
        <UploadForm key={documentId} caseId={view.case_id} documentId={documentId} onUploaded={onChanged} />
      ))}
    </>
  )
}

/**
 * The chosen file as text. Browsers have `File.text()`; jsdom (and older
 * browsers) only have `FileReader`, so both paths are kept.
 */
function readFileText(file: File): Promise<string> {
  if (typeof file.text === 'function') return file.text()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '')
    reader.onerror = () => reject(reader.error ?? new Error('read_failed'))
    reader.readAsText(file)
  })
}

const EXTENSION_FORMATS: Record<string, DocumentFormat> = {
  pdf: 'pdf',
  jpg: 'jpg',
  jpeg: 'jpg',
  png: 'png',
}

/**
 * One missing document: a file, whose text the browser reads (design decision 3),
 * or pasted content, plus the format. The answer is the case as it now stands -
 * the server may have dropped the document (D25), so the view is simply replaced.
 */
function UploadForm({
  caseId,
  documentId,
  onUploaded,
}: {
  caseId: string
  documentId: string
  onUploaded: (next: PatientView) => void
}) {
  const [content, setContent] = useState('')
  const [format, setFormat] = useState<DocumentFormat>('pdf')
  const [fileName, setFileName] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const label = documentLabel(documentId)
  const formatId = `fmt-${documentId}`
  const fileId = `file-${documentId}`

  async function chooseFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    setFileName(file.name)
    const extension = file.name.split('.').pop()?.toLowerCase() ?? ''
    const guessed = EXTENSION_FORMATS[extension]
    if (guessed) setFormat(guessed)
    try {
      setContent(await readFileText(file))
      setError(null)
    } catch {
      setError('לא הצלחנו לקרוא את הקובץ. אפשר להדביק את תוכנו בשדה שמתחת.')
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = content.trim()
    if (!trimmed) {
      setError('בחרו קובץ או הדביקו את תוכן המסמך.')
      return
    }
    if (trimmed.length > MAX_CONTENT) {
      setError('תוכן המסמך ארוך מדי. אפשר לשלוח עד 20,000 תווים.')
      return
    }
    setError(null)
    setBusy(true)
    try {
      const next = await api.uploadDocument(caseId, { document_id: documentId, format, content: trimmed })
      onUploaded(next)
      setContent('')
      setFileName(null)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="upload" aria-label={`העלאת מסמך ${documentId}`}>
      <h2 className="section-h">
        העלאת מסמך: <span className="code" dir="ltr">{documentId}</span>
        {label && <span className="doc-name">{label}</span>}
      </h2>

      <form className="form" onSubmit={submit}>
        <div className="field">
          <label className="label" htmlFor={fileId}>
            בחירת קובץ
          </label>
          <input
            id={fileId}
            className="file-input"
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,.txt"
            onChange={chooseFile}
          />
          <p className="hint">
            {fileName ? `נבחר הקובץ ${fileName}.` : 'הדמו שולח את תוכן הקובץ כטקסט. אפשר גם להדביק אותו למטה.'}
          </p>
        </div>

        <TextField
          multiline
          label="תוכן המסמך"
          value={content}
          maxLength={MAX_CONTENT}
          counter
          rows={4}
          onChange={(event) => setContent(event.target.value)}
        />

        <div className="field">
          <label className="label" htmlFor={formatId}>
            סוג הקובץ
          </label>
          <div className="control">
            <select
              id={formatId}
              className="input select"
              value={format}
              onChange={(event) => setFormat(event.target.value as DocumentFormat)}
            >
              {DOCUMENT_FORMATS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
        </div>

        {error && <Alert variant="error">{error}</Alert>}

        <div className="actions">
          <Button type="submit" variant="primary" busy={busy}>
            שליחת המסמך
          </Button>
        </div>
      </form>
    </section>
  )
}
