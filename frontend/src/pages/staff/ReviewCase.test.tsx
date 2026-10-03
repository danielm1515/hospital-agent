import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import type { AppointmentList, MessageTemplate, ReviewContext, ReviewItem } from '../../api/types'
import { authValue, STAFF_USER, TestAuthProvider } from '../../test/helpers'
import { ReviewCase } from './ReviewCase'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  getContext: vi.fn(),
  getReviewItem: vi.fn(),
  decide: vi.fn(),
  tombstone: vi.fn(),
  getMessageTemplates: vi.fn(),
  listCaseAppointments: vi.fn(),
  getStaffInstruction: vi.fn(),
}))

function appointmentList(overrides: Partial<AppointmentList> = {}): AppointmentList {
  return {
    from: '2026-09-26T00:00:00Z',
    to: '2026-10-26T00:00:00Z',
    appointments: [],
    truncated: false,
    ...overrides,
  }
}

const TEMPLATES: MessageTemplate[] = [
  { template_id: 'clarify_general', purpose: 'question', text: 'לא הצלחנו להבין', param: null, options: {} },
  {
    template_id: 'clarify_did_you_mean',
    purpose: 'question',
    text: 'האם התכוונת ל{topic}?',
    param: 'topic',
    options: { preparation: 'הוראות ההכנה לתור' },
  },
  {
    template_id: 'document_request',
    purpose: 'document',
    text: 'נא להעלות את המסמך: {document}.',
    param: 'document',
    options: { URINALYSIS: 'בדיקת שתן' },
  },
  { template_id: 'close_handled', purpose: 'closing', text: 'טופלה', param: null, options: {} },
]

const CASE_ID = 'CASE-6FFF40DFB8DA'

const MEDICAL_ITEM: ReviewItem = {
  case_id: CASE_ID,
  patient_id: 'P-10041',
  escalation_kind: 'MedicalQuestion',
  escalated_from_state: 'Classifying',
  reasons: ['medical_answer_attempt'],
  allowed_decisions: ['resolve', 'reject'],
  required_fields: [],
  entered_at: '2026-09-19T22:12:39.693277Z',
  human_engaged: false,
  returned_by: null,
}

const Z3_ITEM: ReviewItem = {
  ...MEDICAL_ITEM,
  escalation_kind: 'Z3Counterexample',
  escalated_from_state: 'AssessingReadiness',
  reasons: ['hours_until:20'],
  allowed_decisions: ['approve', 'resolve', 'reject'],
  required_fields: ['patient_deadline'],
}

const IDENTITY_ITEM: ReviewItem = {
  ...MEDICAL_ITEM,
  escalation_kind: 'PatientVerificationFailed',
  escalated_from_state: 'Received',
  reasons: [],
  allowed_decisions: ['approve', 'resolve', 'reject'],
  required_fields: ['verified_identity_ref'],
}

function context(overrides: Partial<ReviewContext> = {}): ReviewContext {
  return {
    case_id: CASE_ID,
    patient_id: 'P-10041',
    state: 'AwaitingHumanReview',
    escalation_kind: 'MedicalQuestion',
    escalated_from_state: 'Classifying',
    reasons: ['medical_answer_attempt'],
    data: [
      {
        entry_id: 'DATA-0080b88ca384',
        kind: 'request_text',
        content: 'האם להפסיק את מדלל הדם?',
        content_hash: '39d813fac47b85aba91577cb8ff34c584f018d92cf083d724a3a6c366a193bf3',
        created_at: '2026-09-19T22:12:39.681696Z',
      },
    ],
    trace: [
      {
        audit_id: 36,
        record_type: 'Transition',
        event: 'REQUEST_SUBMITTED',
        state_before: null,
        state_after: 'Received',
        action: null,
        policy_result: null,
        policy_reasons: [],
        recorded_at: '2026-09-19T22:12:39.678380Z',
        guards: {},
        outcome: null,
        attempt_number: null,
        retry_cycle: null,
        execution_id: null,
        approval_id: null,
      },
    ],
    shown_context_ref: 'ctx-ba3e0652b0ea',
    appointment_id: null,
    answered_appointment_id: null,
    department: null,
    exam_type_label: null,
    instruction_source_id: null,
    instruction_version: null,
    ...overrides,
  }
}

function renderCase(role: 'clinical_staff' | 'admin_staff' = 'clinical_staff', query = '') {
  return render(
    <MemoryRouter initialEntries={[`/staff/cases/${CASE_ID}${query}`]}>
      <TestAuthProvider value={authValue({ user: { ...STAFF_USER, role } })}>
        <Routes>
          <Route path="/staff" element={<h1>תור הסלמות</h1>} />
          <Route path="/staff/cases/:caseId" element={<ReviewCase />} />
        </Routes>
      </TestAuthProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(api.getContext).mockResolvedValue(context())
  vi.mocked(api.getReviewItem).mockResolvedValue(MEDICAL_ITEM)
  vi.mocked(api.decide).mockResolvedValue({ case_id: CASE_ID, state: 'Completed' })
  vi.mocked(api.tombstone).mockResolvedValue(undefined)
  vi.mocked(api.getMessageTemplates).mockResolvedValue([])
  vi.mocked(api.listCaseAppointments).mockResolvedValue(appointmentList())
})

describe('ReviewCase', () => {
  it('shows the shared loading status while the context is still loading', async () => {
    vi.mocked(api.getContext).mockReturnValue(new Promise(() => {})) // never resolves
    renderCase()
    const status = await screen.findByText('טוען…')
    expect(status).toHaveAttribute('role', 'status')
    expect(status.closest('.loader')).toBeInTheDocument()
  })

  it('does not flash "not awaiting a decision" while the review item is still loading (M2)', async () => {
    vi.mocked(api.getReviewItem).mockReturnValue(new Promise(() => {})) // never resolves
    renderCase()

    // The context itself loads fine and fast (its own mock resolves), so the page is past
    // the whole-page loader by now - only the decision panel's own item fetch is pending.
    await screen.findByRole('tab', { name: 'הכרעה' })
    expect(screen.queryByText('הפנייה אינה ממתינה להכרעה')).not.toBeInTheDocument()
    const panelStatus = screen.getByText('טוען…')
    expect(panelStatus.closest('.loader')).toHaveClass('loader-inline')
  })

  it('shows the appointments panel for the patient, loaded with the case id', async () => {
    renderCase('clinical_staff', '?tab=appointment')

    expect(await screen.findByRole('heading', { name: 'התורים של המטופל' })).toBeInTheDocument()
    // The panel calls load from its mount effect through a microtask, which can land after the
    // heading renders - wait for the call instead of asserting it immediately.
    await waitFor(() =>
      expect(api.listCaseAppointments).toHaveBeenCalledWith(CASE_ID, expect.any(Date), expect.any(Date)),
    )
  })

  it('shows the chosen appointment, its exam type and the instruction source (sub-project 18, D13)', async () => {
    vi.mocked(api.getContext).mockResolvedValue(
      context({
        appointment_id: 'APT-8392',
        answered_appointment_id: 'APT-8392',
        department: 'Cardiology',
        exam_type_label: 'מבחן מאמץ',
        instruction_source_id: 'INSTR-CARD-STRESS',
        instruction_version: '1',
      }),
    )
    renderCase('clinical_staff', '?tab=appointment')

    const group = (await screen.findByRole('heading', { name: 'תור והוראות הכנה' })).closest('.fact-group') as HTMLElement
    expect(within(group).getByText('מבחן מאמץ')).toBeInTheDocument()
    expect(within(group).getByText('Cardiology')).toHaveClass('code')
    expect(group).toHaveTextContent('קרדיולוגיה')
    expect(within(group).getByText('INSTR-CARD-STRESS')).toHaveClass('code')
    expect(group).toHaveTextContent('גרסה 1')
    expect(within(group).getAllByText('APT-8392')).toHaveLength(2)
  })

  it('reads the preparation text through the staff route (sub-project 18, D12)', async () => {
    vi.mocked(api.listCaseAppointments).mockResolvedValue(
      appointmentList({
        appointments: [
          {
            appointment_id: 'APT-8392',
            appointment_at: '2026-10-07T06:00:00Z',
            department: 'Cardiology',
            doctor_name: null,
            location: null,
            status: 'Scheduled',
            required_documents: [],
            exam_type: { code: 'CARD_STRESS', label: 'מבחן מאמץ' },
            instruction: { source_id: 'INSTR-CARD-STRESS', version: '1', title: 'לפני מבחן מאמץ' },
          },
        ],
      }),
    )
    vi.mocked(api.getStaffInstruction).mockResolvedValue({
      source_id: 'INSTR-CARD-STRESS',
      version: '1',
      title: 'לפני מבחן מאמץ',
      text: 'צום 3 שעות.',
    })
    renderCase('clinical_staff', '?tab=appointment')

    await userEvent.click(await screen.findByRole('button', { name: 'הצגת הוראות ההכנה' }))
    expect(await screen.findByText('צום 3 שעות.')).toBeInTheDocument()
    expect(api.getStaffInstruction).toHaveBeenCalledWith('INSTR-CARD-STRESS', '1')
  })

  it('does not reload the appointments list when the review context is refreshed', async () => {
    vi.mocked(api.decide).mockRejectedValue(new api.ApiError(409, 'context_changed'))
    renderCase('clinical_staff', '?tab=appointment')

    await screen.findByRole('heading', { name: 'התורים של המטופל' })
    await waitFor(() => expect(api.listCaseAppointments).toHaveBeenCalledTimes(1))
    await userEvent.click(screen.getByRole('tab', { name: 'הכרעה' }))

    await userEvent.type(await screen.findByLabelText(/סיבת ההכרעה/), 'סגירה.')
    await userEvent.click(screen.getByRole('button', { name: 'סגירת הפנייה' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('ההקשר השתנה')

    await userEvent.click(screen.getByRole('button', { name: 'רענון הקשר' }))
    await waitFor(() => expect(api.getContext).toHaveBeenCalledTimes(2))
    // Let any stray deferred load land before counting, so a reload would be caught here.
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(api.listCaseAppointments).toHaveBeenCalledTimes(1)
  })

  it('shows the Data Log content and the audit trace as returned', async () => {
    renderCase('clinical_staff', '?tab=data')

    const data = await screen.findByRole('tabpanel')
    expect(within(data).getByText('האם להפסיק את מדלל הדם?')).toBeInTheDocument()
    expect(within(data).getByText('הפנייה')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: /יומן ביקורת/ }))
    const audit = screen.getByRole('tabpanel')
    expect(within(audit).getByText('REQUEST_SUBMITTED')).toBeInTheDocument()
    expect(within(audit).getByText('Received')).toBeInTheDocument()
    expect(screen.getByText('medical_answer_attempt')).toBeInTheDocument()
  })

  it('numbers the audit rows and times them to the millisecond, with the gap between them', async () => {
    vi.mocked(api.getContext).mockResolvedValue(
      context({
        trace: [
          {
            audit_id: 36,
            record_type: 'Transition',
            event: 'REQUEST_SUBMITTED',
            state_before: null,
            state_after: 'Received',
            action: null,
            policy_result: null,
            policy_reasons: [],
            recorded_at: '2026-09-19T22:12:39.678380Z',
            guards: {},
            outcome: null,
            attempt_number: null,
            retry_cycle: null,
            execution_id: null,
            approval_id: null,
          },
          {
            audit_id: 37,
            record_type: 'Transition',
            event: 'REQUEST_VALIDATED',
            state_before: 'Received',
            state_after: 'Classifying',
            action: null,
            policy_result: null,
            policy_reasons: [],
            recorded_at: '2026-09-19T22:12:39.890380Z',
            guards: {},
            outcome: null,
            attempt_number: null,
            retry_cycle: null,
            execution_id: null,
            approval_id: null,
          },
        ],
      }),
    )
    const { container } = renderCase()
    await screen.findByText('REQUEST_VALIDATED')

    const items = [...container.querySelectorAll('.audit-timeline-item')]
    expect(items.map((item) => item.querySelector('.audit-timeline-index')?.textContent)).toEqual(['1.', '2.'])
    // Both rows land in the same minute, so the time has to carry seconds and milliseconds
    // (the hour is the reader's local one, whatever the machine's time zone is).
    expect(items[0]).toHaveTextContent(/\d{2}:12:39[.,]678/)
    expect(items[1]).toHaveTextContent(/\d{2}:12:39[.,]890/)
    expect(items[1]).toHaveTextContent('+0.212 שנ׳')
    expect(items[0].querySelector('.audit-timeline-gap')).toBeNull()
    expect(screen.getByText(/2 רשומות/)).toBeInTheDocument()
  })

  it('says in words which state a row came from and which it went to', async () => {
    renderCase()
    await screen.findByText('REQUEST_SUBMITTED')

    // An arrow between two Latin names is ambiguous on an RTL line; the words are not.
    const states = document.querySelector('.audit-timeline-states')
    expect(states?.textContent).toBe('נפתח במצב Received')
    expect(document.querySelector('.audit-timeline-arrow')).toBeNull()
  })

  it('renders buttons only for allowed_decisions', async () => {
    renderCase()

    expect(await screen.findByRole('button', { name: 'סגירת הפנייה' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'דחייה' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'אישור והמשך' })).not.toBeInTheDocument()
  })

  it('mounts the clinical answer block for a MedicalQuestion escalation', async () => {
    renderCase()

    expect(await screen.findByLabelText('התשובה למטופל')).toBeInTheDocument()
  })

  it('does not mount the clinical answer block for a non-MedicalQuestion escalation', async () => {
    vi.mocked(api.getReviewItem).mockResolvedValue(Z3_ITEM)
    renderCase()

    await screen.findByRole('button', { name: 'אישור והמשך' })
    expect(screen.queryByLabelText('התשובה למטופל')).not.toBeInTheDocument()
    expect(screen.queryByText('תשובה למטופל')).not.toBeInTheDocument()
  })

  it('locks the clinical answer block for a reviewer who is not clinical_staff', async () => {
    renderCase('admin_staff')

    expect(await screen.findByText('אישור תוכן רפואי הוא של צוות קליני בלבד (§12.4). אפשר לסגור או לדחות את הפנייה.')).toBeInTheDocument()
    expect(screen.queryByLabelText('התשובה למטופל')).not.toBeInTheDocument()
  })

  it('renders the required field of a Z3Counterexample approval', async () => {
    vi.mocked(api.getReviewItem).mockResolvedValue(Z3_ITEM)
    renderCase()

    expect(await screen.findByRole('button', { name: 'אישור והמשך' })).toBeInTheDocument()
    expect(screen.getByLabelText('מועד יעד חדש למטופל')).toBeInTheDocument()
    expect(screen.queryByLabelText('אסמכתת זיהוי')).not.toBeInTheDocument()
  })

  it('sends the decision with the reason and the shown context ref', async () => {
    renderCase()

    await userEvent.type(await screen.findByLabelText(/סיבת ההכרעה/), 'הופנתה למרפאה.')
    await userEvent.click(screen.getByRole('button', { name: 'סגירת הפנייה' }))

    expect(api.decide).toHaveBeenCalledWith(CASE_ID, {
      decision: 'resolve',
      reason: 'הופנתה למרפאה.',
      shown_context_ref: 'ctx-ba3e0652b0ea',
    })
    expect(await screen.findByRole('heading', { name: 'תור הסלמות' })).toBeInTheDocument()
  })

  it('sends verified_identity_ref with an approval that requires it', async () => {
    vi.mocked(api.getReviewItem).mockResolvedValue(IDENTITY_ITEM)
    vi.mocked(api.decide).mockResolvedValue({ case_id: CASE_ID, state: 'Classifying' })
    renderCase()

    await userEvent.type(await screen.findByLabelText(/סיבת ההכרעה/), 'זוהתה בדלפק.')
    await userEvent.type(screen.getByLabelText('אסמכתת זיהוי'), 'ID-DESK-17')
    await userEvent.click(screen.getByRole('button', { name: 'אישור והמשך' }))

    expect(api.decide).toHaveBeenCalledWith(CASE_ID, {
      decision: 'approve',
      reason: 'זוהתה בדלפק.',
      shown_context_ref: 'ctx-ba3e0652b0ea',
      verified_identity_ref: 'ID-DESK-17',
    })
  })

  it('refuses to send without a reason', async () => {
    renderCase()

    await userEvent.click(await screen.findByRole('button', { name: 'דחייה' }))

    expect(api.decide).not.toHaveBeenCalled()
    expect(screen.getByText('נדרשת סיבה להכרעה.')).toBeInTheDocument()
  })

  it('offers to refresh the context after 409 context_changed', async () => {
    vi.mocked(api.decide).mockRejectedValue(new api.ApiError(409, 'context_changed'))
    renderCase()

    await userEvent.type(await screen.findByLabelText(/סיבת ההכרעה/), 'סגירה.')
    await userEvent.click(screen.getByRole('button', { name: 'סגירת הפנייה' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('ההקשר השתנה')
    expect(api.getContext).toHaveBeenCalledTimes(1)

    await userEvent.click(screen.getByRole('button', { name: 'רענון הקשר' }))

    expect(api.getContext).toHaveBeenCalledTimes(2)
  })

  it('keeps the decision form mounted while a successful refresh is still pending (fix round 3)', async () => {
    vi.mocked(api.decide).mockRejectedValue(new api.ApiError(409, 'context_changed'))
    renderCase()

    await userEvent.type(await screen.findByLabelText(/סיבת ההכרעה/), 'סגירה.')
    await userEvent.click(screen.getByRole('button', { name: 'סגירת הפנייה' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('ההקשר השתנה')

    let resolveContext: (value: ReviewContext) => void = () => {}
    vi.mocked(api.getContext).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveContext = resolve
      }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'רענון הקשר' }))

    // The refresh has not resolved yet - the form (and its buttons) must stay mounted, not
    // be replaced by a loader.
    expect(screen.getByRole('button', { name: 'סגירת הפנייה' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'דחייה' })).toBeInTheDocument()
    expect(screen.getByLabelText(/סיבת ההכרעה/)).toBeInTheDocument()
    expect(screen.queryByText('טוען…')).not.toBeInTheDocument()

    resolveContext(context())
    await waitFor(() => expect(screen.queryByText('ההקשר השתנה')).not.toBeInTheDocument())
  })

  it('keeps the decision form and the refresh button usable when the refresh itself fails (I1, fix round 2)', async () => {
    vi.mocked(api.decide).mockRejectedValue(new api.ApiError(409, 'context_changed'))
    renderCase()

    await userEvent.type(await screen.findByLabelText(/סיבת ההכרעה/), 'סגירה.')
    await userEvent.click(screen.getByRole('button', { name: 'סגירת הפנייה' }))
    expect(await screen.findByText('ההקשר השתנה')).toBeInTheDocument()

    vi.mocked(api.getContext).mockRejectedValueOnce(new api.ApiError(503, 'appointments_unavailable'))
    await userEvent.click(screen.getByRole('button', { name: 'רענון הקשר' }))

    // Not stuck on a loader forever, and not unmounted: "ההקשר השתנה" (and its "רענון
    // הקשר" button - the refresh that just ran did not resolve it) is still up, right
    // alongside the new "the refresh itself failed" alert; the decision buttons never left.
    await waitFor(() => expect(api.getContext).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('רענון ההקשר נכשל')).toBeInTheDocument()
    expect(screen.getByText('appointments_unavailable')).toBeInTheDocument()
    expect(screen.getByText('ההקשר השתנה')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'רענון הקשר' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'סגירת הפנייה' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'דחייה' })).toBeInTheDocument()
    expect(screen.queryByText('טוען…')).not.toBeInTheDocument()

    // A second refresh, this time succeeding, resolves it through the same button.
    await userEvent.click(screen.getByRole('button', { name: 'רענון הקשר' }))

    await waitFor(() => expect(api.getContext).toHaveBeenCalledTimes(3))
    expect(screen.queryByText('ההקשר השתנה')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'סגירת הפנייה' })).toBeInTheDocument()
  })

  it('shows the detail of any other 409', async () => {
    vi.mocked(api.decide).mockRejectedValue(new api.ApiError(409, 'not_in_review'))
    renderCase()

    await userEvent.type(await screen.findByLabelText(/סיבת ההכרעה/), 'סגירה.')
    await userEvent.click(screen.getByRole('button', { name: 'סגירת הפנייה' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('not_in_review')
    expect(screen.queryByRole('button', { name: 'רענון הקשר' })).not.toBeInTheDocument()
  })

  it('shows the Hebrew label beside a decision error code', async () => {
    vi.mocked(api.decide).mockRejectedValue(new api.ApiError(409, 'human_engaged'))
    renderCase()

    await userEvent.type(await screen.findByLabelText(/סיבת ההכרעה/), 'סגירה.')
    await userEvent.click(screen.getByRole('button', { name: 'סגירת הפנייה' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('human_engaged')
    expect(alert).toHaveTextContent('לא ניתן לאשר המשך: כבר נשלחה בקשה למטופל בפנייה זו')
  })

  it('asks for confirmation before it tombstones a Data Log entry', async () => {
    renderCase('clinical_staff', '?tab=data')

    await userEvent.click(await screen.findByRole('button', { name: 'מחיקה לפי בקשת מטופל' }))
    expect(api.tombstone).not.toHaveBeenCalled()

    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'מחיקה' }))

    expect(api.tombstone).toHaveBeenCalledWith(CASE_ID, 'DATA-0080b88ca384')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('reloads the context after a tombstone, for a fresh shown_context_ref', async () => {
    renderCase('clinical_staff', '?tab=data')

    await userEvent.click(await screen.findByRole('button', { name: 'מחיקה לפי בקשת מטופל' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'מחיקה' }))

    expect(api.getContext).toHaveBeenCalledTimes(2)
  })

  it('cancels the deletion without calling the API', async () => {
    renderCase('clinical_staff', '?tab=data')

    await userEvent.click(await screen.findByRole('button', { name: 'מחיקה לפי בקשת מטופל' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'ביטול' }))

    expect(api.tombstone).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('says so when the case is not waiting for a decision', async () => {
    vi.mocked(api.getReviewItem).mockRejectedValue(new api.ApiError(404, 'not_in_review'))
    vi.mocked(api.getContext).mockResolvedValue(context({ state: 'Completed', escalation_kind: null, reasons: [] }))
    renderCase()

    expect(await screen.findByText('הפנייה אינה ממתינה להכרעה')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'סגירת הפנייה' })).not.toBeInTheDocument()
  })

  it('shows a first-load error, not "not awaiting a decision" or a refresh title, when getReviewItem itself fails (fix round 3)', async () => {
    vi.mocked(api.getReviewItem).mockRejectedValue(new api.ApiError(500, 'boom'))
    renderCase()

    expect(await screen.findByText('טעינת הפנייה נכשלה')).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()
    expect(screen.queryByText('הפנייה אינה ממתינה להכרעה')).not.toBeInTheDocument()
    expect(screen.queryByText('רענון ההקשר נכשל')).not.toBeInTheDocument()
  })

  it('shows the patient-request panel and no approve button when human_engaged, even on a resumable kind', async () => {
    // RetryExhausted normally allows `approve` (it just opens a new retry cycle) - the
    // absent button here has to come from `allowed_decisions`, not from the escalation kind.
    vi.mocked(api.getReviewItem).mockResolvedValue({
      ...MEDICAL_ITEM,
      escalation_kind: 'RetryExhausted',
      escalated_from_state: 'RetrievingData',
      human_engaged: true,
      allowed_decisions: ['resolve', 'reject'],
    })
    vi.mocked(api.getMessageTemplates).mockResolvedValue(TEMPLATES)
    renderCase()

    expect(await screen.findByRole('heading', { name: 'בקשה מהמטופל' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'אישור והמשך' })).not.toBeInTheDocument()
  })

  it('sends a closing template with the resolve decision', async () => {
    vi.mocked(api.getReviewItem).mockResolvedValue(Z3_ITEM)
    vi.mocked(api.getMessageTemplates).mockResolvedValue(TEMPLATES)
    renderCase()

    await userEvent.type(await screen.findByLabelText(/סיבת ההכרעה/), 'טופלה.')
    const closingGroup = screen.getByRole('group', { name: 'הודעת סיום למטופל' })
    await userEvent.selectOptions(within(closingGroup).getByLabelText('הודעה'), 'close_handled')
    await userEvent.click(screen.getByRole('button', { name: 'סגירת הפנייה' }))

    expect(api.decide).toHaveBeenCalledWith(
      CASE_ID,
      expect.objectContaining({
        decision: 'resolve',
        message: { template_id: 'close_handled' },
      }),
    )
  })

  it('never sends a message when approving, even if a closing message was chosen', async () => {
    // Z3_ITEM allows approve and requires patient_deadline; a closing message picked while
    // it was on screen must not leak into an approve body (a closing message is resolve/reject only).
    vi.mocked(api.getReviewItem).mockResolvedValue(Z3_ITEM)
    vi.mocked(api.getMessageTemplates).mockResolvedValue(TEMPLATES)
    renderCase()

    await userEvent.type(await screen.findByLabelText(/סיבת ההכרעה/), 'אישור.')
    fireEvent.change(screen.getByLabelText('מועד יעד חדש למטופל'), { target: { value: '2026-10-01T10:00' } })
    const closingGroup = screen.getByRole('group', { name: 'הודעת סיום למטופל' })
    await userEvent.selectOptions(within(closingGroup).getByLabelText('הודעה'), 'close_handled')
    await userEvent.click(screen.getByRole('button', { name: 'אישור והמשך' }))

    expect(api.decide).toHaveBeenCalled()
    const [, body] = vi.mocked(api.decide).mock.calls[0]
    expect(body).not.toHaveProperty('message')
  })
})

describe('ReviewCase tabs', () => {
  it('splits the case into four tabs and opens on the decision when one is awaited', async () => {
    renderCase()
    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual(['הכרעה', 'תוכן הפנייה1', 'תור ומסמכים', 'יומן ביקורת1'])
    expect(screen.getByRole('tab', { name: 'הכרעה' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByRole('button', { name: 'סגירת הפנייה' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'מחיקה לפי בקשת מטופל' })).not.toBeInTheDocument()
  })

  it('shows the request text on the decision tab, with a link to the full content', async () => {
    renderCase()
    const panel = await screen.findByRole('tabpanel')
    expect(within(panel).getByText('האם להפסיק את מדלל הדם?')).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: 'לתוכן המלא' }))
    expect(screen.getByRole('tab', { name: /תוכן הפנייה/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'מחיקה לפי בקשת מטופל' })).toBeVisible()
  })

  it('opens on the content when no decision is awaited', async () => {
    vi.mocked(api.getReviewItem).mockRejectedValue(new api.ApiError(404, 'not_in_review'))
    renderCase()
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /תוכן הפנייה/ })).toHaveAttribute('aria-selected', 'true'),
    )
  })

  it('opens the tab named in the address', async () => {
    renderCase('clinical_staff', '?tab=audit')
    expect(await screen.findByRole('tab', { name: /יומן ביקורת/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName(/יומן ביקורת/)
  })

  it('ignores an unknown tab in the address', async () => {
    renderCase('clinical_staff', '?tab=nope')
    expect(await screen.findByRole('tab', { name: 'הכרעה' })).toHaveAttribute('aria-selected', 'true')
  })

  it('keeps a half-written decision reason across a tab switch', async () => {
    renderCase()
    await userEvent.type(await screen.findByLabelText('סיבת ההכרעה (פנימית)'), 'טיוטה')
    await userEvent.click(screen.getByRole('tab', { name: /יומן ביקורת/ }))
    await userEvent.click(screen.getByRole('tab', { name: 'הכרעה' }))
    expect(screen.getByLabelText('סיבת ההכרעה (פנימית)')).toHaveValue('טיוטה')
  })
})
