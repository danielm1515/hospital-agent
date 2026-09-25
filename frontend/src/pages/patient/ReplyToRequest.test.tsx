import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import type { PatientView } from '../../api/types'
import { Conversation, ReplyToRequest } from './ReplyToRequest'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  replyToRequest: vi.fn(),
  replyWithFile: vi.fn(),
  getRequest: vi.fn(),
}))

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

const QUESTION: PatientView = {
  ...BASE,
  reply_request: { kind: 'question', message: 'האם התכוונת למועד התור?', document_type: null, deadline: '2026-09-26T08:05:00Z' },
} as PatientView

const DOCUMENT: PatientView = {
  ...BASE,
  // Task 10 ruling: `deadline` is never null - the server always sets one.
  reply_request: { kind: 'document', message: 'נא להעלות את המסמך: בדיקת שתן.', document_type: 'URINALYSIS', deadline: '2026-09-26T08:05:00Z' },
} as PatientView

describe('ReplyToRequest', () => {
  it('shows the question and sends a text reply', async () => {
    vi.mocked(api.replyToRequest).mockResolvedValue({ ...QUESTION, status: 'in_review', reply_request: null })
    const onChanged = vi.fn()
    render(<ReplyToRequest view={QUESTION} onChanged={onChanged} />)
    expect(screen.getByText('האם התכוונת למועד התור?')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('התשובה שלך'), 'כן')
    await userEvent.click(screen.getByRole('button', { name: 'שליחת התשובה' }))
    expect(api.replyToRequest).toHaveBeenCalledWith('C-1', 'כן')
    expect(onChanged).toHaveBeenCalled()
  })

  it('refuses a blank reply without calling the API', async () => {
    render(<ReplyToRequest view={QUESTION} onChanged={vi.fn()} />)
    await userEvent.type(screen.getByLabelText('התשובה שלך'), '   ')
    await userEvent.click(screen.getByRole('button', { name: 'שליחת התשובה' }))
    expect(api.replyToRequest).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('יש לכתוב תשובה לפני השליחה.')
  })

  it('refreshes the view when the case is no longer waiting for a reply', async () => {
    vi.mocked(api.replyToRequest).mockRejectedValue(new ApiError(409, 'not_waiting_for_reply'))
    vi.mocked(api.getRequest).mockResolvedValue({ ...QUESTION, status: 'in_review', reply_request: null })
    const onChanged = vi.fn()
    render(<ReplyToRequest view={QUESTION} onChanged={onChanged} />)
    await userEvent.type(screen.getByLabelText('התשובה שלך'), 'כן')
    await userEvent.click(screen.getByRole('button', { name: 'שליחת התשובה' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('הפנייה כבר אינה ממתינה לתשובה')
    expect(api.getRequest).toHaveBeenCalledWith('C-1')
    expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ status: 'in_review' }))
  })

  it('offers a PDF picker for a document request and explains a wrong type', async () => {
    vi.mocked(api.replyWithFile).mockResolvedValue({
      upload: { code: 'wrong_document_type', document_type: 'CBC' },
      request: DOCUMENT,
    })
    render(<ReplyToRequest view={DOCUMENT} onChanged={vi.fn()} />)
    await userEvent.upload(
      screen.getByLabelText('בחירת קובץ PDF'),
      new File(['%PDF'], 'cbc.pdf', { type: 'application/pdf' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'העלאת המסמך' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('המסמך שהועלה אינו המסמך שהתבקש')
  })

  it('refuses a non-PDF file without calling the API', async () => {
    render(<ReplyToRequest view={DOCUMENT} onChanged={vi.fn()} />)
    await userEvent.upload(
      screen.getByLabelText('בחירת קובץ PDF'),
      new File(['hello'], 'notes.txt', { type: 'text/plain' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'העלאת המסמך' }))
    expect(api.replyWithFile).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('יש לבחור קובץ PDF.')
  })

  it('replaces the view when the document is accepted', async () => {
    const accepted = { ...DOCUMENT, status: 'in_review', reply_request: null } as PatientView
    vi.mocked(api.replyWithFile).mockResolvedValue({
      upload: { code: 'accepted', document_type: 'URINALYSIS' },
      request: accepted,
    })
    const onChanged = vi.fn()
    render(<ReplyToRequest view={DOCUMENT} onChanged={onChanged} />)
    await userEvent.upload(
      screen.getByLabelText('בחירת קובץ PDF'),
      new File(['%PDF'], 'urine.pdf', { type: 'application/pdf' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'העלאת המסמך' }))
    expect(onChanged).toHaveBeenCalledWith(accepted)
  })
})

describe('Conversation', () => {
  it('lists staff messages and replies in order', () => {
    render(
      <Conversation
        entries={[
          { sender: 'staff', text: 'שאלה מהצוות', at: '2026-09-25T08:00:00Z' },
          { sender: 'patient', text: 'תשובה שלי', at: '2026-09-25T09:00:00Z' },
        ]}
      />,
    )
    const items = screen.getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('צוות')
    expect(items[0]).toHaveTextContent('שאלה מהצוות')
    // Ruling: the patient's own label in the conversation is "אני", not "את/ה".
    expect(items[1]).toHaveTextContent('אני')
  })

  it('renders nothing when there is no conversation yet', () => {
    const { container } = render(<Conversation entries={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
