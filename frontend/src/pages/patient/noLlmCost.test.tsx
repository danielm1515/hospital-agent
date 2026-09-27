/**
 * Sub-project 19 (docs/api.md §10, plan Global Constraints): the patient UI never shows an LLM
 * cost, a token count or a model. Three layers of proof: the patient's type has no such field,
 * no patient-side source reaches for one (or for the staff money helpers), and even a view the
 * server had leaked a cost into renders none of it.
 */
import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, expectTypeOf, it, vi } from 'vitest'
import * as api from '../../api/client'
import type { AppointmentList, PatientView } from '../../api/types'
import { authValue, PATIENT_USER, TestAuthProvider } from '../../test/helpers'
import { MyRequests } from './MyRequests'
import { RequestDetail } from './RequestDetail'
import { patientView } from './fixtures'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  listRequests: vi.fn(),
  getRequest: vi.fn(),
  listMyAppointments: vi.fn(),
  getPatientInstruction: vi.fn(),
  getLlmCosts: vi.fn(),
}))

// Paths are relative to the Vitest root, i.e. `frontend/`.
const SOURCES = ['src/pages/patient', 'src/components'].flatMap((dir) =>
  readdirSync(resolve(process.cwd(), dir))
    .filter((name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name))
    .map((name) => join(dir, name)),
)

/**
 * Whatever would put a cost on a patient screen. "עלות" only as a word of its own: the
 * patient screens say "להעלות" (to upload), which merely contains the letters.
 */
const FORBIDDEN = [
  /llm_cost|llm_usage|cost_usd|unpriced_calls|input_tokens|output_tokens/,
  /getLlmCosts|LlmCost|CaseLlmUsage|formatUsd|costText|CaseLlmFacts/,
  /metricsLabels|MetricsParts/,
  /(^|[^א-ת])עלות/,
  /מחיר לא ידוע/,
]

/** A leaked cost, as if the server had put the staff fields on the patient view. */
const LEAKED = {
  llm_cost_usd: '0.00213400',
  llm_cost_partial: true,
  llm_usage: { calls: 2, input_tokens: 3600, cached_input_tokens: 0, output_tokens: 52, cost_usd: '0.00078240' },
}

function leakedView(overrides: Partial<PatientView> = {}): PatientView {
  return { ...patientView({ case_id: 'CASE-1', ...overrides }), ...LEAKED } as PatientView
}

const NO_APPOINTMENTS: AppointmentList = {
  from: '2026-09-27T00:00:00Z',
  to: '2026-10-27T00:00:00Z',
  appointments: [],
  truncated: false,
}

function expectNoCostOnScreen() {
  const text = document.body.textContent ?? ''
  expect(text).not.toMatch(/\$\s?\d|<\$/)
  expect(text).not.toContain('0.0021')
  expect(text).not.toContain('0.00078240')
  expect(text).not.toMatch(/(^|[^א-ת])עלות/)
  expect(text).not.toContain('מחיר לא ידוע')
  expect(text).not.toMatch(/LLM|token/i)
}

beforeEach(() => {
  vi.mocked(api.listMyAppointments).mockResolvedValue(NO_APPOINTMENTS)
})

describe('the patient UI never shows an LLM cost (sub-project 19)', () => {
  it('has no cost, token or model field on the patient view type', () => {
    // A type-level assertion: Vitest does not check it at run time, `tsc -b` does - and
    // `npm run build` runs `tsc -b` over `src/` (tests included), so a cost-like key on
    // `PatientView` fails the build.
    type CostLike = Extract<keyof PatientView, `${string}cost${string}` | `${string}llm${string}` | `${string}token${string}`>
    expectTypeOf<CostLike>().toEqualTypeOf<never>()
  })

  it('reads no cost field and uses no staff money helper in any patient-side source', () => {
    expect(SOURCES.length).toBeGreaterThan(5)
    const offending = SOURCES.flatMap((path) => {
      const source = readFileSync(resolve(process.cwd(), path), 'utf8')
      return FORBIDDEN.filter((pattern) => pattern.test(source)).map((pattern) => `${path}: ${pattern}`)
    })
    expect(offending).toEqual([])
  })

  it('renders no cost on "הפניות שלי", even from a view that carries one', async () => {
    vi.mocked(api.listRequests).mockResolvedValue([
      leakedView({ status: 'completed', message: 'התור שלך קבוע ל-23/09/2026 בשעה 08:30.' }),
      leakedView({ case_id: 'CASE-2', status: 'in_progress' }),
    ])
    render(
      <MemoryRouter initialEntries={['/patient']}>
        <TestAuthProvider value={authValue({ user: PATIENT_USER })}>
          <Routes>
            <Route path="/patient" element={<MyRequests />} />
          </Routes>
        </TestAuthProvider>
      </MemoryRouter>,
    )

    await screen.findAllByRole('link', { name: /CASE-1|מתי התור שלי/ })
    expectNoCostOnScreen()
    expect(api.getLlmCosts).not.toHaveBeenCalled()
  })

  it('renders no cost on a request screen, even from a view that carries one', async () => {
    vi.mocked(api.getRequest).mockResolvedValue(
      leakedView({ status: 'completed', message: 'התור שלך קבוע ל-23/09/2026 בשעה 08:30.' }),
    )
    render(
      <MemoryRouter initialEntries={['/patient/requests/CASE-1']}>
        <TestAuthProvider value={authValue({ user: PATIENT_USER })}>
          <Routes>
            <Route path="/patient/requests/:caseId" element={<RequestDetail />} />
          </Routes>
        </TestAuthProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('התור שלך קבוע ל-23/09/2026 בשעה 08:30.')).toBeInTheDocument()
    expectNoCostOnScreen()
    expect(api.getLlmCosts).not.toHaveBeenCalled()
  })
})
