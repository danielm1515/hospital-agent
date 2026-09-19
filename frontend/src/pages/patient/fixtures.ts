/** Test-only builders for the patient view of `docs/api.md` §4. */
import type { PatientView } from '../../api/types'

export function patientView(overrides: Partial<PatientView> = {}): PatientView {
  return {
    case_id: 'CASE-23FE645294B7',
    status: 'in_progress',
    created_at: '2026-09-19T22:12:47.693947Z',
    updated_at: '2026-09-19T22:12:47.898132Z',
    request_text: 'מתי התור שלי ואילו מסמכים צריך להביא?',
    missing_document_ids: [],
    missing_document_request_template_id: null,
    message: null,
    ...overrides,
  }
}
