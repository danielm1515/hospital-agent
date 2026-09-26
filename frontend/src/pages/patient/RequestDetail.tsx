import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import * as api from '../../api/client'
import { DOCUMENT_FORMATS } from '../../api/types'
import type { DocumentFormat, PatientStatus, PatientView, StatusChange } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { Loading } from '../../components/Loading'
import { StatusPill } from '../../components/StatusPill'
import { TextField } from '../../components/TextField'
import { Conversation, ReplyToRequest } from './ReplyToRequest'
import { MY_REQUESTS } from './paths'
import {
  documentLabel,
  elapsedBetween,
  errorMessage,
  FILE_TOO_LARGE_MESSAGE,
  formatClock,
  formatDate,
  formatDateTime,
  formatIsraelHourMinute,
  isAcceptedDocumentFile,
  isFinal,
  isMoving,
  MAX_UPLOAD_BYTES,
  REQUEST_POLL_MS,
  REQUEST_POLL_WINDOW_MS,
  sameDay,
  statusText,
  UNSUPPORTED_FILE_MESSAGE,
  uploadResultMessage,
  usePolling,
} from './helpers'
import type { UploadNotice } from './helpers'

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
  // Lifted above `StatusContent`: `accepted` moves the case out of `needs_document`
  // (`docs/api.md` §4), so a notice that lived only inside the upload form would
  // unmount together with it the moment the view is replaced.
  const [uploadNotice, setUploadNotice] = useState<UploadNotice | null>(null)
  // Sub-project 18 (addition A): the 60 s polling window of a waiting status. `windowKey`
  // restarts its timeout without a second interval - the interval itself only follows
  // whether polling is on (`usePolling`).
  const [windowOpen, setWindowOpen] = useState(false)
  const [windowKey, setWindowKey] = useState(0)
  const [loadedAt, setLoadedAt] = useState<Date | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  // Every answer is numbered as it is asked for; one older than the view already shown (a
  // slow poll overtaken by the patient's own upload, say) is dropped, and nothing lands
  // after the screen is gone.
  const asked = useRef(0)
  const shown = useRef(0)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  // The status last put on screen, kept beside `shown` so `show` knows - in the very update
  // that puts a new status on screen - whether the window must open (fix round 1, item 4):
  // opening it from an effect a render later let "עודכן לאחרונה" flash for one frame.
  const shownStatus = useRef<PatientStatus | null>(null)

  /** Opens (or restarts) the 60 s window for a non-final status; closes it for a final one. */
  const openWindow = useCallback((next: PatientStatus) => {
    if (isFinal(next)) {
      setWindowOpen(false)
      return
    }
    setWindowOpen(true)
    setWindowKey((key) => key + 1)
  }, [])

  /**
   * Puts an answer on screen unless a newer one is already there (or the screen is gone).
   * Entering the screen and every status change open the window in the same update;
   * `restart` does so for an unchanged status too (the patient's own action).
   */
  const show = useCallback(
    (next: PatientView, ticket: number, restart = false) => {
      if (!alive.current || ticket < shown.current) return false
      shown.current = ticket
      const changed = next.status !== shownStatus.current
      shownStatus.current = next.status
      setView(next)
      setError(null)
      setLoadedAt(new Date())
      if (changed || restart) openWindow(next.status)
      return true
    },
    [openWindow],
  )

  const load = useCallback(async () => {
    const ticket = ++asked.current
    try {
      show(await api.getRequest(caseId), ticket)
    } catch (caught) {
      if (!alive.current || ticket < shown.current) return
      setError(errorMessage(caught))
    }
  }, [caseId, show])

  /** "רענון" and the tab shown again: restart the window of the status on screen, if non-final. */
  const restartWindow = useCallback(() => {
    const current = shownStatus.current
    if (current === null || isFinal(current)) return
    openWindow(current)
  }, [openWindow])

  useEffect(() => {
    // Through a microtask, and cancelled by the cleanup: StrictMode's throw-away first mount
    // never loads, so the screen asks exactly once on entry (fix round 1, item 8).
    let cancelled = false
    void Promise.resolve().then(() => {
      if (!cancelled) void load()
    })
    return () => {
      cancelled = true
    }
  }, [load])

  useEffect(() => {
    setUploadNotice(null)
  }, [caseId])

  useEffect(() => {
    if (!windowOpen) return
    const timer = setTimeout(() => setWindowOpen(false), REQUEST_POLL_WINDOW_MS)
    return () => clearTimeout(timer)
  }, [windowOpen, windowKey])

  // The tab shown again: read the case now and restart the window (non-final only).
  useEffect(() => {
    function onVisibility() {
      if (document.visibilityState !== 'visible') return
      const current = shownStatus.current
      if (current === null || isFinal(current)) return
      restartWindow()
      void load()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [load, restartWindow])

  const status = view?.status ?? null
  const polling = status !== null && !isFinal(status) && (isMoving(status) || windowOpen)
  usePolling(polling, () => void load(), REQUEST_POLL_MS)
  const pollingStopped = status !== null && !isFinal(status) && !polling

  /** The patient's own successful action (upload, reply): show its answer, restart the window. */
  const applyChange = useCallback((next: PatientView) => void show(next, ++asked.current, true), [show])

  async function refresh() {
    setRefreshing(true)
    restartWindow()
    await load()
    if (alive.current) setRefreshing(false)
  }

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
        !error && <Loading />
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

          <Timeline history={view.history} />

          <section className="req-body">
            <h2 className="section-h">הפנייה שלכם</h2>
            <p className="req-full">{view.request_text ?? 'תוכן הפנייה נמחק מהמערכת.'}</p>
          </section>

          {/* Sub-project 15: the conversation so far, above whatever the case is waiting on
              now (the reply form, or nothing) - it is history, read before the present. */}
          <Conversation entries={view.conversation} />

          {uploadNotice && <Alert variant={uploadNotice.variant}>{uploadNotice.text}</Alert>}

          <StatusContent view={view} onChanged={applyChange} onUploadNotice={setUploadNotice} />

          {pollingStopped && (
            <div className="req-refresh">
              <p className="hint">{`עודכן לאחרונה ב־${loadedAt ? formatIsraelHourMinute(loadedAt) : ''}`}</p>
              <Button variant="quiet" busy={refreshing} onClick={() => void refresh()}>
                רענון
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  )
}

// ---- Timeline --------------------------------------------------------------

function Timeline({ history }: { history: StatusChange[] }) {
  if (history.length === 0) return null
  return (
    <section className="tl-block" aria-label="מהלך הפנייה">
      <h2 className="section-h">מהלך הפנייה</h2>
      <ol className="timeline">
        {history.map((step, index) => {
          const previous = index > 0 ? history[index - 1] : null
          const isCurrent = index === history.length - 1
          const gap = previous ? elapsedBetween(previous.at, step.at) : null
          const showDate = previous === null || !sameDay(previous.at, step.at)
          const text = statusText(step.status)
          return (
            <li
              key={`${step.status}-${step.at}`}
              className={`tl-step ${isCurrent ? 'current' : 'done'}`}
              aria-current={isCurrent ? 'step' : undefined}
            >
              <span className="tl-dot" aria-hidden="true" />
              <div className="tl-text">
                <p className="tl-title">{text.title}</p>
                <p className="tl-note">{text.note}</p>
              </div>
              <div className="tl-when">
                <time className="tl-time" dateTime={step.at}>
                  {formatClock(step.at)}
                </time>
                {showDate && <span className="tl-date">{formatDate(step.at)}</span>}
                {gap && <span className="tl-gap">{gap}</span>}
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

// ---- Content per status ----------------------------------------------------

function StatusContent({
  view,
  onChanged,
  onUploadNotice,
}: {
  view: PatientView
  onChanged: (next: PatientView) => void
  onUploadNotice: (notice: UploadNotice | null) => void
}) {
  switch (view.status) {
    case 'needs_document':
      return <MissingDocuments view={view} onChanged={onChanged} onUploadNotice={onUploadNotice} />
    case 'needs_reply':
      // Sub-project 15 (`docs/api.md` §8): a staff member asked a question or for a
      // document instead of (or before) deciding the case. `onUploadNotice` is shared with
      // `MissingDocuments` below - the two statuses are mutually exclusive, so one slot is
      // enough, and lifting it here is what keeps a notice alive across the status change
      // a successful reply causes (see `ReplyToRequest`).
      return <ReplyToRequest view={view} onChanged={onChanged} onNotice={onUploadNotice} />
    case 'completed':
      return (
        <>
          <Alert variant="ok" title="ההודעה שנשלחה אליך">
            <span className="message-text">{view.message}</span>
          </Alert>
          {view.instructions && <Instructions title={view.instructions.title} text={view.instructions.text} />}
        </>
      )
    case 'in_review':
      return <Alert variant="info">הפנייה הועברה לבדיקת צוות. מידע רפואי אינו נמסר באופן אוטומטי.</Alert>
    case 'closed':
      // `closed` means a person handled the case and no message was delivered through the
      // system (`docs/api.md` §4). The reviewer's own reason is an audit record, not an
      // answer to the patient: content reaches a patient only through a ContentApproval
      // (§12.5). Since sub-project 15, a reviewer may attach a closing message to the
      // decision - shown here exactly like a delivered one - and the screen falls back to
      // the generic line only when there isn't one.
      return view.message ? (
        <Alert variant="info" title="הודעה מהצוות">
          <span className="message-text">{view.message}</span>
        </Alert>
      ) : (
        <Alert variant="info" title="הפנייה נסגרה על ידי איש צוות">
          תשובה רפואית אינה נמסרת דרך המערכת. איש הצוות שטיפל בפנייה יחזור אליכם ישירות; אם לא שמעתם
          מאיתנו, אפשר לפנות למוקד המטופלים.
        </Alert>
      )
    default:
      return (
        <p className="muted" role="status">
          הפנייה בטיפול. המסך מתעדכן מעצמו.
        </p>
      )
  }
}

/**
 * Sub-project 18 (design D11): the preparation instruction the case loaded, under the
 * delivered message - exactly what was approved and shown (`docs/api.md` §4), as a readable
 * section: its own title, and the text with its line breaks kept. Never its source id or
 * version: a code is staff-only.
 */
function Instructions({ title, text }: { title: string; text: string }) {
  return (
    <section className="req-instructions" aria-labelledby="req-instructions-h">
      <h2 className="section-h" id="req-instructions-h">
        הוראות ההכנה
      </h2>
      <h3 className="req-instructions-title">{title}</h3>
      <p className="message-text">{text}</p>
    </section>
  )
}

/**
 * D24: the request for a missing document is rendered here, from the template id
 * the server sent - never sent to the patient by the system. An unknown template
 * id shows nothing but a neutral line (fail closed, §14).
 */
function MissingDocuments({
  view,
  onChanged,
  onUploadNotice,
}: {
  view: PatientView
  onChanged: (next: PatientView) => void
  onUploadNotice: (notice: UploadNotice | null) => void
}) {
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

      {view.document_upload === 'file' ? (
        <FileUploadForm caseId={view.case_id} onUploaded={onChanged} onUploadNotice={onUploadNotice} />
      ) : (
        view.missing_document_ids.map((documentId) => (
          <UploadForm key={documentId} caseId={view.case_id} documentId={documentId} onUploaded={onChanged} />
        ))
      )}
    </>
  )
}

// ---- File upload (sub-project 13) ------------------------------------------

/**
 * The sub-project 13 file picker (design §5.3; sub-project 17 task 2: PDF, JPEG or PNG, not
 * PDF only): one file, checked in the browser for an accepted name/type and the 10 MB limit
 * before it is sent, then `uploadDocumentFile`. The response always replaces the view
 * (`request`), and `upload.code` says what happened to the file itself.
 */
function FileUploadForm({
  caseId,
  onUploaded,
  onUploadNotice,
}: {
  caseId: string
  onUploaded: (next: PatientView) => void
  onUploadNotice: (notice: UploadNotice | null) => void
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
    if (!file || !isAcceptedDocumentFile(file)) {
      setError(UNSUPPORTED_FILE_MESSAGE)
      return
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(FILE_TOO_LARGE_MESSAGE)
      return
    }
    setError(null)
    onUploadNotice(null)
    setBusy(true)
    try {
      const response = await api.uploadDocumentFile(caseId, file)
      // The notice is shown by the parent (`RequestDetail`), not this form: `accepted`
      // moves the case out of `needs_document` (`docs/api.md` §4), which unmounts this
      // form the moment `onUploaded` replaces the view.
      onUploaded(response.request)
      onUploadNotice(uploadResultMessage(response.upload))
      setFileName(null)
      if (inputRef.current) inputRef.current.value = ''
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="upload" aria-label="העלאת מסמך (PDF או תמונה)">
      <h2 className="section-h">העלאת מסמך (PDF או תמונה)</h2>

      <form className="form" onSubmit={submit}>
        <div className="field">
          <label className="label" htmlFor="file-upload">
            בחירת קובץ
          </label>
          <input
            id="file-upload"
            ref={inputRef}
            className="file-input"
            type="file"
            accept="application/pdf,.pdf,image/jpeg,.jpg,.jpeg,image/png,.png"
            onChange={chooseFile}
          />
          <p className="hint">
            {fileName
              ? `נבחר הקובץ ${fileName}.`
              : 'המערכת מזהה את סוג המסמך אוטומטית. אפשר להעלות קובץ PDF או תמונה (JPG/PNG) אחד, עד 10MB.'}
          </p>
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
  const [notice, setNotice] = useState<string | null>(null)
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
    setNotice(null)
    setBusy(true)
    try {
      const next = await api.uploadDocument(caseId, { document_id: documentId, format, content: trimmed })
      onUploaded(next)
      // `docs/api.md` §4: a `200` here is not necessarily success. The only reliable
      // signal is whether `status` actually left `needs_document` - a document that
      // failed validation is a committed self-loop (status unchanged, state advanced),
      // and a document the case was not waiting for at all (D25) changes nothing. Either
      // way the content must be kept so the patient can look at it and try again.
      if (next.status !== 'needs_document') {
        setContent('')
        setFileName(null)
      } else {
        setNotice('המסמך נשלח. הפנייה עדיין ממתינה למסמך — בדקו את הקובץ ונסו שוב.')
      }
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
        {notice && <Alert variant="info">{notice}</Alert>}

        <div className="actions">
          <Button type="submit" variant="primary" busy={busy}>
            שליחת המסמך
          </Button>
        </div>
      </form>
    </section>
  )
}
