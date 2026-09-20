import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { ApiError } from '../../api/client'
import type { PatientView } from '../../api/types'
import { RequestDetail } from './RequestDetail'
import { patientView } from './fixtures'
import { authValue, PATIENT_USER, TestAuthProvider } from '../../test/helpers'

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>()
  return { ...actual, getRequest: vi.fn(), uploadDocument: vi.fn() }
})

const getRequest = vi.mocked(api.getRequest)
const uploadDocument = vi.mocked(api.uploadDocument)

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

  it('closed shows the closing line', async () => {
    getRequest.mockResolvedValue(patientView({ status: 'closed' }))
    renderDetail()
    expect(await screen.findByText('הפנייה נסגרה.')).toBeInTheDocument()
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
