/** Test-only builders for the patient view of `docs/api.md` §4. */
import type { PatientStatus, PatientView, StatusChange } from '../../api/types'

/** A history that ends at `status`, the way the server's always does. */
export function historyFor(status: PatientStatus): StatusChange[] {
  const steps: StatusChange[] = [
    { status: 'received', at: '2026-09-19T22:12:47.693947Z' },
    { status: 'in_progress', at: '2026-09-19T22:12:47.812004Z' },
  ]
  if (status === 'received') return steps.slice(0, 1)
  if (status === 'in_progress') return steps
  return [...steps, { status, at: '2026-09-19T22:12:47.898132Z' }]
}

export function patientView(overrides: Partial<PatientView> = {}): PatientView {
  const status = overrides.status ?? 'in_progress'
  return {
    case_id: 'CASE-23FE645294B7',
    status,
    created_at: '2026-09-19T22:12:47.693947Z',
    updated_at: '2026-09-19T22:12:47.898132Z',
    request_text: 'מתי התור שלי ואילו מסמכים צריך להביא?',
    missing_document_ids: [],
    missing_document_request_template_id: null,
    message: null,
    history: historyFor(status),
    document_upload: 'text',
    ...overrides,
  }
}
