import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import type { PatientView } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Conversation, ReplyToRequest } from './ReplyToRequest'
import type { UploadNotice } from './helpers'
import { formatDateTime } from './helpers'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  replyToRequest: vi.fn(),
  replyWithFile: vi.fn(),
  getRequest: vi.fn(),
}))

/**
 * Stands in for `RequestDetail`'s own `uploadNotice` state and `<Alert>` (the notice
 * `ReplyToRequest` is handed is lifted there, exactly like `MissingDocuments`' - see the
 * "notice vanishes" fix in Task 12's review) so these tests can see what a real caller
 * would actually show the patient, not just that a callback fired.
 */
function Harness({ view, onChanged }: { view: PatientView; onChanged: (next: PatientView) => void }) {
  const [notice, setNotice] = useState<UploadNotice | null>(null)
  return (
    <>
      {notice && <Alert variant={notice.variant}>{notice.text}</Alert>}
      <ReplyToRequest view={view} onChanged={onChanged} onNotice={setNotice} />
    </>
  )
}

const BASE = {
  case_id: 'C-1',
  status: 'needs_reply',
  created_at: '2026-09-25T08:00:00Z',
  updated_at: '2026-09-25T08:05:00Z',
  request_text: 'שאלה',
  missing_document_ids: [],
  missing_document_request_template_id: null,
  message: null,
  history: [],
  document_upload: 'file',
  conversation: [],
} satisfies Partial<PatientView>

const DEADLINE = '2026-09-26T08:05:00Z'

const QUESTION: PatientView = {
  ...BASE,
  reply_request: { kind: 'question', message: 'האם התכוונת למועד התור?', document_type: null, deadline: DEADLINE },
} as PatientView

const DOCUMENT: PatientView = {
  ...BASE,
  // Task 10 ruling: `deadline` is never null - the server always sets one.
  reply_request: { kind: 'document', message: 'נא להעלות את המסמך: בדיקת שתן.', document_type: 'URINALYSIS', deadline: DEADLINE },
} as PatientView

function pdfFile(name = 'results.pdf', sizeBytes = 1024) {
  const file = new File(['%PDF-1.4 ...'], name, { type: 'application/pdf' })
  Object.defineProperty(file, 'size', { value: sizeBytes })
  return file
}

describe('ReplyToRequest: text reply', () => {
  it('shows the question, the formatted deadline, and sends the trimmed text', async () => {
    const returned = { ...QUESTION, status: 'in_review', reply_request: null } as PatientView
    vi.mocked(api.replyToRequest).mockResolvedValue(returned)
    const onChanged = vi.fn()
    render(<Harness view={QUESTION} onChanged={onChanged} />)

    expect(screen.getByText('האם התכוונת למועד התור?')).toBeInTheDocument()
    expect(screen.getByText(/נא להשיב עד/)).toHaveTextContent(formatDateTime(DEADLINE))

    await userEvent.type(screen.getByLabelText('התשובה שלך'), '  כן  ')
    await userEvent.click(screen.getByRole('button', { name: 'שליחת התשובה' }))

    expect(api.replyToRequest).toHaveBeenCalledWith('C-1', 'כן')
    // Pins the exact object the caller gets, not just that it was called at all.
    expect(onChanged).toHaveBeenCalledWith(returned)
  })

  it('has a 2000-character maxlength on the textarea (`docs/api.md` §8)', () => {
    render(<Harness view={QUESTION} onChanged={vi.fn()} />)
    expect(screen.getByLabelText('התשובה שלך')).toHaveAttribute('maxlength', '2000')
  })

  it('refuses a blank reply without calling the API, as a field error', async () => {
    render(<Harness view={QUESTION} onChanged={vi.fn()} />)
    const textarea = screen.getByLabelText('התשובה שלך')
    await userEvent.type(textarea, '   ')
    await userEvent.click(screen.getByRole('button', { name: 'שליחת התשובה' }))
    expect(api.replyToRequest).not.toHaveBeenCalled()
    expect(screen.getByText('יש לכתוב תשובה לפני השליחה.')).toBeInTheDocument()
    expect(textarea).toHaveAttribute('aria-invalid', 'true')
  })

  it('refreshes the view and keeps the notice visible when the case is no longer waiting for a reply', async () => {
    vi.mocked(api.replyToRequest).mockRejectedValue(new ApiError(409, 'not_waiting_for_reply'))
    vi.mocked(api.getRequest).mockResolvedValue({ ...QUESTION, status: 'in_review', reply_request: null })
    const onChanged = vi.fn()
    render(<Harness view={QUESTION} onChanged={onChanged} />)

    await userEvent.type(screen.getByLabelText('התשובה שלך'), 'כן')
    await userEvent.click(screen.getByRole('button', { name: 'שליחת התשובה' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('הפנייה כבר אינה ממתינה לתשובה')
    expect(api.getRequest).toHaveBeenCalledWith('C-1')
    expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ status: 'in_review' }))
  })

  it('refreshes the view and keeps the notice visible on a reply_kind_mismatch too', async () => {
    vi.mocked(api.replyToRequest).mockRejectedValue(new ApiError(409, 'reply_kind_mismatch'))
    vi.mocked(api.getRequest).mockResolvedValue(DOCUMENT)
    const onChanged = vi.fn()
    render(<Harness view={QUESTION} onChanged={onChanged} />)

    await userEvent.type(screen.getByLabelText('התשובה שלך'), 'כן')
    await userEvent.click(screen.getByRole('button', { name: 'שליחת התשובה' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('לא ניתן להשיב בדרך זו')
    expect(onChanged).toHaveBeenCalledWith(DOCUMENT)
  })
})

describe('ReplyToRequest: file reply', () => {
  it('offers a PDF picker for a document request and explains a wrong type through the lifted notice', async () => {
    vi.mocked(api.replyWithFile).mockResolvedValue({
      upload: { code: 'wrong_document_type', document_type: 'CBC' },
      request: DOCUMENT,
    })
    const onChanged = vi.fn()
    render(<Harness view={DOCUMENT} onChanged={onChanged} />)
    await userEvent.upload(screen.getByLabelText('בחירת קובץ PDF'), pdfFile('cbc.pdf'))
    await userEvent.click(screen.getByRole('button', { name: 'העלאת המסמך' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('המסמך שהועלה אינו המסמך שהתבקש')
    // M2: the view is always replaced with the response, even when it did not change.
    expect(onChanged).toHaveBeenCalledWith(DOCUMENT)
  })

  it('refuses a non-PDF file without calling the API', async () => {
    render(<Harness view={DOCUMENT} onChanged={vi.fn()} />)
    await userEvent.upload(screen.getByLabelText('בחירת קובץ PDF'), new File(['hello'], 'notes.txt', { type: 'text/plain' }))
    await userEvent.click(screen.getByRole('button', { name: 'העלאת המסמך' }))
    expect(api.replyWithFile).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('יש לבחור קובץ PDF.')
  })

  it('refuses a PDF over 10 MB without calling the API', async () => {
    render(<Harness view={DOCUMENT} onChanged={vi.fn()} />)
    await userEvent.upload(screen.getByLabelText('בחירת קובץ PDF'), pdfFile('big.pdf', 11 * 1024 * 1024))
    await userEvent.click(screen.getByRole('button', { name: 'העלאת המסמך' }))
    expect(api.replyWithFile).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('הקובץ גדול מדי')
  })

  it('replaces the view and shows the accepted sentence through the lifted notice', async () => {
    const accepted = { ...DOCUMENT, status: 'in_review', reply_request: null } as PatientView
    vi.mocked(api.replyWithFile).mockResolvedValue({
      upload: { code: 'accepted', document_type: 'URINALYSIS' },
      request: accepted,
    })
    const onChanged = vi.fn()
    render(<Harness view={DOCUMENT} onChanged={onChanged} />)
    await userEvent.upload(screen.getByLabelText('בחירת קובץ PDF'), pdfFile('urine.pdf'))
    await userEvent.click(screen.getByRole('button', { name: 'העלאת המסמך' }))

    expect(onChanged).toHaveBeenCalledWith(accepted)
    expect(await screen.findByText(/התקבל/)).toBeInTheDocument()
  })

  it('refreshes the view and keeps the notice visible when the reply/file call finds the case stale', async () => {
    vi.mocked(api.replyWithFile).mockRejectedValue(new ApiError(409, 'not_waiting_for_reply'))
    vi.mocked(api.getRequest).mockResolvedValue({ ...DOCUMENT, status: 'in_review', reply_request: null })
    const onChanged = vi.fn()
    render(<Harness view={DOCUMENT} onChanged={onChanged} />)
    await userEvent.upload(screen.getByLabelText('בחירת קובץ PDF'), pdfFile())
    await userEvent.click(screen.getByRole('button', { name: 'העלאת המסמך' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('הפנייה כבר אינה ממתינה לתשובה')
    expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ status: 'in_review' }))
  })

  it('disables the file input while the upload is in flight', async () => {
    let resolveUpload: (value: Awaited<ReturnType<typeof api.replyWithFile>>) => void = () => {}
    vi.mocked(api.replyWithFile).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveUpload = resolve
        }),
    )
    render(<Harness view={DOCUMENT} onChanged={vi.fn()} />)
    await userEvent.upload(screen.getByLabelText('בחירת קובץ PDF'), pdfFile())
    await userEvent.click(screen.getByRole('button', { name: 'העלאת המסמך' }))

    expect(screen.getByLabelText('בחירת קובץ PDF')).toBeDisabled()
    resolveUpload({ upload: { code: 'accepted', document_type: 'URINALYSIS' }, request: DOCUMENT })
    await waitFor(() => expect(screen.getByLabelText('בחירת קובץ PDF')).not.toBeDisabled())
  })
})

describe('ReplyToRequest: fail-closed', () => {
  it('shows a neutral line instead of nothing when needs_reply has no reply_request', () => {
    const noRequest = { ...BASE, reply_request: null } as PatientView
    render(<Harness view={noRequest} onChanged={vi.fn()} />)
    expect(screen.getByText(/הפנייה ממתינה לתשובה/)).toBeInTheDocument()
  })
})

describe('Conversation', () => {
  it('has a visible heading and lists staff messages and replies in order', () => {
    render(
      <Conversation
        entries={[
          { sender: 'staff', text: 'שאלה מהצוות', at: '2026-09-25T08:00:00Z' },
          { sender: 'patient', text: 'תשובה שלי', at: '2026-09-25T09:00:00Z' },
        ]}
      />,
    )
    expect(screen.getByRole('heading', { name: 'ההתכתבות עם הצוות' })).toBeInTheDocument()
    const items = screen.getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('צוות בית החולים')
    expect(items[0]).toHaveTextContent('שאלה מהצוות')
    // Ruling: the patient's own label in the conversation is "אני", not "את/ה".
    expect(items[1]).toHaveTextContent('אני')
  })

  it('fails closed: only sender "patient" is ever labelled "אני"', () => {
    render(
      <Conversation
        entries={[{ sender: 'someone-else' as never, text: 'טקסט', at: '2026-09-25T08:00:00Z' }]}
      />,
    )
    expect(screen.getByRole('listitem')).toHaveTextContent('צוות בית החולים')
    expect(screen.getByRole('listitem')).not.toHaveTextContent('אני')
  })

  it('renders nothing when there is no conversation yet', () => {
    const { container } = render(<Conversation entries={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
