import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import type { PatientView } from '../../api/types'
import { RequestDetail } from './RequestDetail'
import { patientView } from './fixtures'
import { formatDateTime } from './helpers'
import { authValue, PATIENT_USER, TestAuthProvider } from '../../test/helpers'

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>()
  return {
    ...actual,
    getRequest: vi.fn(),
    uploadDocument: vi.fn(),
    uploadDocumentFile: vi.fn(),
    replyToRequest: vi.fn(),
    replyWithFile: vi.fn(),
  }
})

const getRequest = vi.mocked(api.getRequest)
const uploadDocument = vi.mocked(api.uploadDocument)
const uploadDocumentFile = vi.mocked(api.uploadDocumentFile)
const replyToRequest = vi.mocked(api.replyToRequest)
const replyWithFile = vi.mocked(api.replyWithFile)

function renderDetail(caseId = 'CASE-1') {
  return render(
    <MemoryRouter initialEntries={[`/patient/requests/${caseId}`]}>
      <TestAuthProvider value={authValue({ user: PATIENT_USER })}>
        <Routes>
          <Route path="/patient" element={<h1>הפניות שלי</h1>} />
          <Route path="/patient/requests/:caseId" element={<RequestDetail />} />
        </Routes>
      </TestAuthProvider>
    </MemoryRouter>,
  )
}

const needsDocument = (overrides: Partial<PatientView> = {}) =>
  patientView({
    case_id: 'CASE-1',
    status: 'needs_document',
    missing_document_ids: ['blood_test', 'referral'],
    missing_document_request_template_id: 'missing-document-v1',
    ...overrides,
  })

beforeEach(() => {
  getRequest.mockReset()
  uploadDocument.mockReset()
  uploadDocumentFile.mockReset()
  replyToRequest.mockReset()
  replyWithFile.mockReset()
})

describe('RequestDetail: needs_document (D24)', () => {
  it('renders the missing-document-v1 request and every missing id', async () => {
    getRequest.mockResolvedValue(needsDocument())
    renderDetail()

    expect(await screen.findByText('כדי להשלים את ההכנה לתור חסרים המסמכים הבאים:')).toBeInTheDocument()
    const alert = screen.getByRole('status')
    expect(within(alert).getByText('blood_test')).toBeInTheDocument()
    expect(within(alert).getByText('referral')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'העלאת מסמך blood_test' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'העלאת מסמך referral' })).toBeInTheDocument()
  })

  it('uploads pasted content as {document_id, format, content} and shows the case as it now stands', async () => {
    const user = userEvent.setup()
    getRequest.mockResolvedValue(needsDocument())
    uploadDocument.mockResolvedValue(patientView({ case_id: 'CASE-1', status: 'in_progress' }))
    const { container } = renderDetail()

    const form = await screen.findByRole('region', { name: 'העלאת מסמך blood_test' })
    await user.type(within(form).getByLabelText('תוכן המסמך'), '  תוצאות בדיקת דם  ')
    await user.selectOptions(within(form).getByLabelText('סוג הקובץ'), 'jpg')
    await user.click(within(form).getByRole('button', { name: 'שליחת המסמך' }))

    expect(uploadDocument).toHaveBeenCalledWith('CASE-1', {
      document_id: 'blood_test',
      format: 'jpg',
      content: 'תוצאות בדיקת דם',
    })
    await waitFor(() =>
      expect(screen.queryByText('כדי להשלים את ההכנה לתור חסרים המסמכים הבאים:')).not.toBeInTheDocument(),
    )
    expect(container.querySelector('.req-head .pill')).toHaveTextContent('בטיפול')
  })

  it('reads a chosen file as text and sends it', async () => {
    const user = userEvent.setup()
    getRequest.mockResolvedValue(needsDocument({ missing_document_ids: ['blood_test'] }))
    uploadDocument.mockResolvedValue(needsDocument({ missing_document_ids: ['blood_test'] }))
    renderDetail()

    const form = await screen.findByRole('region', { name: 'העלאת מסמך blood_test' })
    const file = new File(['תוצאות תקינות'], 'results.png', { type: 'image/png' })
    await user.upload(within(form).getByLabelText('בחירת קובץ'), file)

    await waitFor(() => expect(within(form).getByLabelText('תוכן המסמך')).toHaveValue('תוצאות תקינות'))
    expect(within(form).getByLabelText('סוג הקובץ')).toHaveValue('png')

    await user.click(within(form).getByRole('button', { name: 'שליחת המסמך' }))
    expect(uploadDocument).toHaveBeenCalledWith('CASE-1', {
      document_id: 'blood_test',
      format: 'png',
      content: 'תוצאות תקינות',
    })
  })

  it('keeps the pasted content and shows a neutral notice when the document fails validation (committed self-loop, D24/D25)', async () => {
    const user = userEvent.setup()
    getRequest.mockResolvedValue(needsDocument({ missing_document_ids: ['blood_test'] }))
    // §4: a document the case was still waiting for but that fails validation is a
    // committed self-loop - `state_version`/`updated_at` move but `status` stays
    // `needs_document`.
    uploadDocument.mockResolvedValue(
      needsDocument({ missing_document_ids: ['blood_test'], updated_at: '2026-09-20T12:00:00Z' }),
    )
    renderDetail()

    const form = await screen.findByRole('region', { name: 'העלאת מסמך blood_test' })
    await user.type(within(form).getByLabelText('תוכן המסמך'), 'תוצאות בדיקת דם')
    await user.click(within(form).getByRole('button', { name: 'שליחת המסמך' }))

    expect(await within(form).findByText('המסמך נשלח. הפנייה עדיין ממתינה למסמך — בדקו את הקובץ ונסו שוב.')).toBeInTheDocument()
    expect(within(form).getByLabelText('תוכן המסמך')).toHaveValue('תוצאות בדיקת דם')
    // The request stays visible - this was not a confirmation of success.
    expect(screen.getByText('כדי להשלים את ההכנה לתור חסרים המסמכים הבאים:')).toBeInTheDocument()
  })

  it('keeps the pasted content and shows a neutral notice for a document the case was not waiting for (D25)', async () => {
    const user = userEvent.setup()
    const unchanged = needsDocument({ missing_document_ids: ['blood_test'] })
    getRequest.mockResolvedValue(unchanged)
    // §4: a document the case was not waiting for at all changes nothing, not even `updated_at`.
    uploadDocument.mockResolvedValue(unchanged)
    renderDetail()

    const form = await screen.findByRole('region', { name: 'העלאת מסמך blood_test' })
    await user.type(within(form).getByLabelText('תוכן המסמך'), 'תוצאות בדיקת דם')
    await user.click(within(form).getByRole('button', { name: 'שליחת המסמך' }))

    expect(await within(form).findByText('המסמך נשלח. הפנייה עדיין ממתינה למסמך — בדקו את הקובץ ונסו שוב.')).toBeInTheDocument()
    expect(within(form).getByLabelText('תוכן המסמך')).toHaveValue('תוצאות בדיקת דם')
  })

  it('does not send an empty document', async () => {
    const user = userEvent.setup()
    getRequest.mockResolvedValue(needsDocument({ missing_document_ids: ['blood_test'] }))
    renderDetail()

    const form = await screen.findByRole('region', { name: 'העלאת מסמך blood_test' })
    await user.click(within(form).getByRole('button', { name: 'שליחת המסמך' }))

    expect(uploadDocument).not.toHaveBeenCalled()
    expect(within(form).getByRole('alert')).toHaveTextContent('בחרו קובץ או הדביקו את תוכן המסמך.')
  })

  it('shows no request at all for an unknown template id (fails closed)', async () => {
    getRequest.mockResolvedValue(needsDocument({ missing_document_request_template_id: 'other-v9' }))
    renderDetail()

    expect(await screen.findByText(/הפנייה ממתינה למסמך/)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'העלאת מסמך blood_test' })).not.toBeInTheDocument()
  })
})

describe('RequestDetail: needs_document, file upload (sub-project 13, docs/api.md §4)', () => {
  const needsDocumentFile = (overrides: Partial<PatientView> = {}) =>
    needsDocument({ document_upload: 'file', ...overrides })

  function pdfFile(name = 'results.pdf', sizeBytes = 1024) {
    const file = new File(['%PDF-1.4 ...'], name, { type: 'application/pdf' })
    Object.defineProperty(file, 'size', { value: sizeBytes })
    return file
  }

  function jpgFile(name = 'photo.jpg', sizeBytes = 1024) {
    const file = new File(['\xff\xd8\xff...'], name, { type: 'image/jpeg' })
    Object.defineProperty(file, 'size', { value: sizeBytes })
    return file
  }

  it('renders one document input and no text box or format select', async () => {
    getRequest.mockResolvedValue(needsDocumentFile())
    renderDetail()

    const section = await screen.findByRole('region', { name: 'העלאת מסמך (PDF או תמונה)' })
    expect(within(section).getByLabelText('בחירת קובץ')).toHaveAttribute(
      'accept', 'application/pdf,.pdf,image/jpeg,.jpg,.jpeg,image/png,.png')
    expect(within(section).queryByLabelText('תוכן המסמך')).not.toBeInTheDocument()
    expect(within(section).queryByLabelText('סוג הקובץ')).not.toBeInTheDocument()
    // Only one upload section - not one per missing document.
    expect(screen.getAllByRole('region', { name: /העלאת מסמך/ })).toHaveLength(1)
  })

  it('accepts a JPG file and sends it', async () => {
    const user = userEvent.setup()
    getRequest.mockResolvedValue(needsDocumentFile())
    uploadDocumentFile.mockResolvedValue({
      upload: { code: 'accepted', document_type: 'CBC' },
      request: patientView({ case_id: 'CASE-1', status: 'in_progress' }),
    })
    renderDetail()

    const section = await screen.findByRole('region', { name: 'העלאת מסמך (PDF או תמונה)' })
    await user.upload(within(section).getByLabelText('בחירת קובץ'), jpgFile())
    await user.click(within(section).getByRole('button', { name: 'שליחת המסמך' }))

    expect(uploadDocumentFile).toHaveBeenCalledWith('CASE-1', expect.any(File))
  })

  it('refuses an unsupported file without calling the API', async () => {
    const user = userEvent.setup()
    getRequest.mockResolvedValue(needsDocumentFile())
    renderDetail()

    const section = await screen.findByRole('region', { name: 'העלאת מסמך (PDF או תמונה)' })
    const file = new File(['hello'], 'results.txt', { type: 'text/plain' })
    await user.upload(within(section).getByLabelText('בחירת קובץ'), file)
    await user.click(within(section).getByRole('button', { name: 'שליחת המסמך' }))

    expect(uploadDocumentFile).not.toHaveBeenCalled()
    expect(within(section).getByRole('alert')).toHaveTextContent('אפשר להעלות רק PDF או תמונה (JPG/PNG).')
  })

  it('refuses a file over 10 MB without calling the API', async () => {
    const user = userEvent.setup()
    getRequest.mockResolvedValue(needsDocumentFile())
    renderDetail()

    const section = await screen.findByRole('region', { name: 'העלאת מסמך (PDF או תמונה)' })
    await user.upload(within(section).getByLabelText('בחירת קובץ'), pdfFile('big.pdf', 10 * 1024 * 1024 + 1))
    await user.click(within(section).getByRole('button', { name: 'שליחת המסמך' }))

    expect(uploadDocumentFile).not.toHaveBeenCalled()
    expect(within(section).getByRole('alert')).toHaveTextContent('הקובץ גדול מ־10MB. העלו קובץ קטן יותר.')
  })

  it.each([
    ['accepted', 'CBC', 'ok', 'המסמך ספירת דם מלאה התקבל. הפנייה ממשיכה בטיפול.'],
    ['not_required', 'ECG', 'info', 'המסמך תרשים פעילות חשמלית של הלב תקין, אבל אינו נדרש לתור הזה.'],
    ['already_received', 'URINALYSIS', 'info', 'המסמך בדיקת שתן כבר התקבל קודם.'],
    ['not_medical', null, 'error', 'הקובץ אינו מסמך רפואי, ולכן לא נקלט.'],
    ['unreadable', null, 'error', 'לא הצלחנו לקרוא את המסמך. העלו קובץ PDF או תמונה ברורה של המסמך.'],
    ['expired', 'COAGULATION_TESTS', 'error', 'המסמך בדיקות קרישה ישן מדי לפי כללי התוקף. יש להעלות מסמך עדכני.'],
    ['not_yours', null, 'error', 'המסמך אינו שייך לך, ולכן לא נקלט.'],
    ['unrecognised_type', null, 'error', 'לא זיהינו את סוג המסמך. ודאו שהעליתם את המסמך שהתבקש.'],
    ['unreadable_scan', null, 'error',
      'לא הצלחנו לקרוא את הסריקה. צלמו את המסמך באור טוב ובחדות, או העלו את קובץ ה־PDF המקורי.'],
    ['bad_date', null, 'error', 'תאריך המסמך עתידי. ודאו שהעליתם את המסמך הנכון.'],
    ['no_date', null, 'error', 'לא מצאנו תאריך על המסמך. העלו מסמך שמופיע עליו תאריך הבדיקה.'],
    ['unsupported_format', null, 'error', 'סוג הקובץ אינו נתמך. העלו PDF או תמונה (JPG/PNG).'],
    ['too_large', null, 'error', 'הקובץ גדול מ־10MB. העלו קובץ קטן יותר.'],
  ] as const)('shows the sentence for upload.code %s', async (code, documentType, variant, sentence) => {
    const user = userEvent.setup()
    getRequest.mockResolvedValue(needsDocumentFile())
    const nextView =
      code === 'accepted' ? patientView({ case_id: 'CASE-1', status: 'in_progress' }) : needsDocumentFile()
    uploadDocumentFile.mockResolvedValue({ upload: { code, document_type: documentType }, request: nextView })
    renderDetail()

    const section = await screen.findByRole('region', { name: 'העלאת מסמך (PDF או תמונה)' })
    await user.upload(within(section).getByLabelText('בחירת קובץ'), pdfFile())
    await user.click(within(section).getByRole('button', { name: 'שליחת המסמך' }))

    expect(uploadDocumentFile).toHaveBeenCalledWith('CASE-1', expect.any(File))
    expect(await screen.findByText(sentence)).toBeInTheDocument()
    expect(screen.getByText(sentence).closest(`.alert.${variant}`)).not.toBeNull()
    // The patient never sees the raw code, whatever the sentence (§12.3).
    expect(screen.queryByText(code, { exact: false })).not.toBeInTheDocument()
  })

  it('shows the neutral fail-closed sentence for a code this version does not know, and never the code itself', async () => {
    const user = userEvent.setup()
    getRequest.mockResolvedValue(needsDocumentFile())
    uploadDocumentFile.mockResolvedValue({
      upload: { code: 'something_new' as never, document_type: null },
      request: needsDocumentFile(),
    })
    renderDetail()

    const section = await screen.findByRole('region', { name: 'העלאת מסמך (PDF או תמונה)' })
    await user.upload(within(section).getByLabelText('בחירת קובץ'), pdfFile())
    await user.click(within(section).getByRole('button', { name: 'שליחת המסמך' }))

    expect(await screen.findByText('המסמך לא נקלט. נסו שוב או פנו למוקד.')).toBeInTheDocument()
    expect(screen.queryByText('something_new', { exact: false })).not.toBeInTheDocument()
  })

  it('replaces the view with response.request on every code, including accepted', async () => {
    const user = userEvent.setup()
    getRequest.mockResolvedValue(needsDocumentFile())
    uploadDocumentFile.mockResolvedValue({
      upload: { code: 'accepted', document_type: 'CBC' },
      request: patientView({ case_id: 'CASE-1', status: 'in_progress', document_upload: 'file' }),
    })
    const { container } = renderDetail()

    const section = await screen.findByRole('region', { name: 'העלאת מסמך (PDF או תמונה)' })
    await user.upload(within(section).getByLabelText('בחירת קובץ'), pdfFile())
    await user.click(within(section).getByRole('button', { name: 'שליחת המסמך' }))

    await waitFor(() => expect(container.querySelector('.req-head .pill')).toHaveTextContent('בטיפול'))
    expect(screen.queryByText('כדי להשלים את ההכנה לתור חסרים המסמכים הבאים:')).not.toBeInTheDocument()
  })

  it('shows the Hebrew sentence for an API error and does not change the view', async () => {
    const user = userEvent.setup()
    getRequest.mockResolvedValue(needsDocumentFile())
    uploadDocumentFile.mockRejectedValue(new ApiError(409, 'not_waiting_for_document'))
    renderDetail()

    const section = await screen.findByRole('region', { name: 'העלאת מסמך (PDF או תמונה)' })
    await user.upload(within(section).getByLabelText('בחירת קובץ'), pdfFile())
    await user.click(within(section).getByRole('button', { name: 'שליחת המסמך' }))

    expect(await within(section).findByRole('alert')).toHaveTextContent('הפנייה כבר אינה ממתינה למסמך')
  })
})

describe('RequestDetail: the other statuses', () => {
  it('completed shows the delivered message', async () => {
    getRequest.mockResolvedValue(
      patientView({ status: 'completed', message: 'התור שלך ביום ראשון ב-09:00. יש להביא הפניה.' }),
    )
    const { container } = renderDetail()

    expect(await screen.findByText('ההודעה שנשלחה אליך')).toBeInTheDocument()
    expect(screen.getByText('התור שלך ביום ראשון ב-09:00. יש להביא הפניה.')).toBeInTheDocument()
    expect(container.querySelector('.req-head .pill')).toHaveTextContent('הושלמה')
  })

  it('in_review shows the notice and nothing internal', async () => {
    const withInternals = {
      ...patientView({ status: 'in_review' }),
      escalation_kind: 'MedicalQuestion',
      escalated_from_state: 'Classifying',
      reasons: ['medical_answer_attempt'],
    } as unknown as PatientView
    getRequest.mockResolvedValue(withInternals)
    renderDetail()

    expect(
      await screen.findByText('הפנייה הועברה לבדיקת צוות. מידע רפואי אינו נמסר באופן אוטומטי.'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/MedicalQuestion/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Classifying/)).not.toBeInTheDocument()
    expect(screen.queryByText(/medical_answer_attempt/)).not.toBeInTheDocument()
  })

  it('closed says a person handled it and where the answer comes from', async () => {
    getRequest.mockResolvedValue(patientView({ status: 'closed' }))
    renderDetail()
    expect(await screen.findByText('הפנייה נסגרה על ידי איש צוות')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('תשובה רפואית אינה נמסרת דרך המערכת')
    // §12.3: the reviewer's own reason is an audit record, never shown to the patient.
    expect(screen.queryByText(/סיבת ההכרעה/)).not.toBeInTheDocument()
  })

  it('closed with a closing message shows it instead of the generic line (sub-project 15)', async () => {
    getRequest.mockResolvedValue(
      patientView({ status: 'closed', message: 'פנייתך אינה בתחום שהמערכת מטפלת בו.' }),
    )
    renderDetail()
    expect(await screen.findByText('הודעה מהצוות')).toBeInTheDocument()
    expect(screen.getByText('פנייתך אינה בתחום שהמערכת מטפלת בו.')).toBeInTheDocument()
    expect(screen.queryByText('הפנייה נסגרה על ידי איש צוות')).not.toBeInTheDocument()
  })

  it('needs_reply renders the staff request, its deadline, and the reply form', async () => {
    getRequest.mockResolvedValue(
      patientView({
        status: 'needs_reply',
        reply_request: {
          kind: 'question',
          message: 'האם התכוונת למועד התור?',
          document_type: null,
          deadline: '2026-09-26T08:05:00Z',
        },
      }),
    )
    renderDetail()
    expect(await screen.findByText('בקשה מהצוות')).toBeInTheDocument()
    expect(screen.getByText('האם התכוונת למועד התור?')).toBeInTheDocument()
    expect(screen.getByLabelText('התשובה שלך')).toBeInTheDocument()
    // Pins the deadline actually reaching the screen - a guard removed by mistake would
    // still pass every other assertion here.
    expect(screen.getByText(/נא להשיב עד/)).toHaveTextContent(formatDateTime('2026-09-26T08:05:00Z'))
  })

  it('needs_reply with no reply_request shows a neutral fail-closed line instead of nothing', async () => {
    getRequest.mockResolvedValue(patientView({ status: 'needs_reply', reply_request: null }))
    renderDetail()
    expect(await screen.findByText(/הפנייה ממתינה לתשובה/)).toBeInTheDocument()
    expect(screen.queryByText('בקשה מהצוות')).not.toBeInTheDocument()
  })

  it('renders a conversation entry, above the reply form (sub-project 15)', async () => {
    getRequest.mockResolvedValue(
      patientView({
        status: 'needs_reply',
        reply_request: {
          kind: 'question',
          message: 'האם התכוונת למועד התור?',
          document_type: null,
          deadline: '2026-09-26T08:05:00Z',
        },
        conversation: [{ sender: 'staff', text: 'לא הצלחנו להבין את פנייתך.', at: '2026-09-25T09:00:00Z' }],
      }),
    )
    const { container } = renderDetail()
    expect(await screen.findByText('לא הצלחנו להבין את פנייתך.')).toBeInTheDocument()
    const conversation = container.querySelector('.conversation')
    const replyRequest = container.querySelector('.reply-request')
    expect(conversation).not.toBeNull()
    expect(replyRequest).not.toBeNull()
    // `compareDocumentPosition` - `conversation` comes before `reply-request` in source order.
    expect(conversation!.compareDocumentPosition(replyRequest!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('needs_reply: a stale text reply (not_waiting_for_reply) refreshes the view and keeps the notice visible', async () => {
    const user = userEvent.setup()
    getRequest.mockResolvedValueOnce(
      patientView({
        status: 'needs_reply',
        reply_request: {
          kind: 'question',
          message: 'האם התכוונת למועד התור?',
          document_type: null,
          deadline: '2026-09-26T08:05:00Z',
        },
      }),
    )
    replyToRequest.mockRejectedValue(new ApiError(409, 'not_waiting_for_reply'))
    getRequest.mockResolvedValueOnce(patientView({ status: 'in_review' }))
    const { container } = renderDetail()

    await user.type(await screen.findByLabelText('התשובה שלך'), 'כן')
    await user.click(screen.getByRole('button', { name: 'שליחת התשובה' }))

    await waitFor(() => expect(container.querySelector('.req-head .pill')).toHaveTextContent('אצל צוות'))
    // The reply form is gone (the case moved on), but the notice explaining why survives it.
    expect(screen.queryByLabelText('התשובה שלך')).not.toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('הפנייה כבר אינה ממתינה לתשובה')
  })

  it('marks the timeline step of the current status', async () => {
    getRequest.mockResolvedValue(patientView({ status: 'in_progress' }))
    const { container } = renderDetail()
    await screen.findByText('הפנייה שלכם')
    const current = container.querySelector('.tl-step.current')
    expect(current).toHaveTextContent('הפנייה בטיפול')
    expect(current).toHaveAttribute('aria-current', 'step')
  })

  it('the timeline is one step per status change, in order, each with its time', async () => {
    getRequest.mockResolvedValue(
      patientView({
        status: 'completed',
        message: 'התור שלך קבוע ל-23/09/2026 בשעה 08:30.',
        history: [
          { status: 'received', at: '2026-09-19T22:12:47Z' },
          { status: 'in_progress', at: '2026-09-19T22:12:49Z' },
          { status: 'needs_document', at: '2026-09-19T22:12:53Z' },
          { status: 'in_progress', at: '2026-09-19T22:20:53Z' },
          { status: 'completed', at: '2026-09-19T22:21:01Z' },
        ],
      }),
    )
    const { container } = renderDetail()
    await screen.findByText('הפנייה שלכם')

    const steps = [...container.querySelectorAll('.tl-step')]
    expect(steps.map((step) => step.querySelector('.tl-title')?.textContent)).toEqual([
      'הפנייה נקלטה',
      'הפנייה בטיפול',
      'ממתינה למסמך',
      'הפנייה בטיפול',
      'הפנייה הושלמה',
    ])
    // Every step says when it happened, to the second - the agent moves a case in seconds.
    expect(steps.every((step) => /\d{2}:\d{2}:\d{2}/.test(step.textContent ?? ''))).toBe(true)
    // And how long the step before it took.
    expect(steps[1]).toHaveTextContent('כעבור שתי שניות')
    expect(steps[3]).toHaveTextContent('כעבור 8 דקות')
    // The date is printed once, on the first step, since all five are the same day.
    expect(container.querySelectorAll('.tl-date')).toHaveLength(1)
  })

  it('shows a Hebrew message for a case that is not the patient’s', async () => {
    getRequest.mockRejectedValue(new ApiError(404, 'case_not_found'))
    renderDetail()
    expect(await screen.findByRole('alert')).toHaveTextContent('הפנייה לא נמצאה.')
    expect(screen.queryByText(/case_not_found/)).not.toBeInTheDocument()
  })
})

describe('RequestDetail polling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('refreshes every 3 s while the case is in progress, and stops when it is not', async () => {
    getRequest.mockResolvedValue(patientView({ status: 'in_progress' }))
    renderDetail()
    await act(async () => {})
    expect(getRequest).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(3000)
    })
    expect(getRequest).toHaveBeenCalledTimes(2)

    getRequest.mockResolvedValue(patientView({ status: 'in_review' }))
    await act(async () => {
      vi.advanceTimersByTime(3000)
    })
    expect(getRequest).toHaveBeenCalledTimes(3)

    await act(async () => {
      vi.advanceTimersByTime(9000)
    })
    expect(getRequest).toHaveBeenCalledTimes(3)
  })
})
