import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import type { SystemStatus } from '../api/types'
import { SystemStatusBanner } from './SystemStatusBanner'

vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  getSystemStatus: vi.fn(),
}))

function status(overrides: Partial<SystemStatus> = {}): SystemStatus {
  return {
    orchestrator: 'running',
    llm: { last_ok_at: null, last_error: null, last_error_at: null },
    ...overrides,
  }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('SystemStatusBanner', () => {
  it('renders nothing while running with no error', async () => {
    vi.mocked(api.getSystemStatus).mockResolvedValue(status())
    render(<SystemStatusBanner />)

    await waitFor(() => expect(api.getSystemStatus).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('renders nothing while running with an error that is older than the last success', async () => {
    vi.mocked(api.getSystemStatus).mockResolvedValue(
      status({
        llm: {
          last_ok_at: '2026-09-26T10:05:00.000+00:00',
          last_error: 'api:RateLimitError:insufficient_quota',
          last_error_at: '2026-09-26T10:00:00.000+00:00',
        },
      }),
    )
    render(<SystemStatusBanner />)

    await waitFor(() => expect(api.getSystemStatus).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the quota label and the code when the LLM last failed on insufficient_quota', async () => {
    vi.mocked(api.getSystemStatus).mockResolvedValue(
      status({
        llm: {
          last_ok_at: '2026-09-26T09:00:00.000+00:00',
          last_error: 'api:RateLimitError:insufficient_quota',
          last_error_at: '2026-09-26T10:00:00.000+00:00',
        },
      }),
    )
    render(<SystemStatusBanner />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('נגמר הקרדיט בחשבון OpenAI - הפניות מוסלמות לצוות')
    expect(alert).toHaveTextContent('api:RateLimitError:insufficient_quota')
  })

  it('shows the quota label for credit_balance_exhausted too', async () => {
    vi.mocked(api.getSystemStatus).mockResolvedValue(
      status({
        llm: {
          last_ok_at: null,
          last_error: 'api:RateLimitError:credit_balance_exhausted',
          last_error_at: '2026-09-26T10:00:00.000+00:00',
        },
      }),
    )
    render(<SystemStatusBanner />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('נגמר הקרדיט בחשבון OpenAI - הפניות מוסלמות לצוות')
  })

  it('shows the invalid-key label for an AuthenticationError', async () => {
    vi.mocked(api.getSystemStatus).mockResolvedValue(
      status({
        llm: { last_ok_at: null, last_error: 'api:AuthenticationError', last_error_at: '2026-09-26T10:00:00.000+00:00' },
      }),
    )
    render(<SystemStatusBanner />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('מפתח OpenAI אינו תקין')
    expect(alert).toHaveTextContent('api:AuthenticationError')
  })

  it('shows the disabled-key label when the orchestrator never started for lack of a key', async () => {
    vi.mocked(api.getSystemStatus).mockResolvedValue(
      status({ orchestrator: 'disabled: OPENAI_API_KEY is not set' }),
    )
    render(<SystemStatusBanner />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('מפתח OpenAI לא הוגדר - הסוכן האוטומטי כבוי')
    expect(alert).toHaveTextContent('disabled: OPENAI_API_KEY is not set')
  })

  it('falls back to a generic label for any other orchestrator-disabled reason', async () => {
    vi.mocked(api.getSystemStatus).mockResolvedValue(
      status({ orchestrator: 'disabled: APPOINTMENT_API_KEY is not set' }),
    )
    render(<SystemStatusBanner />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('הסוכן האוטומטי אינו זמין')
    expect(alert).toHaveTextContent('disabled: APPOINTMENT_API_KEY is not set')
  })

  it('shows the generic label with no invented code when orchestrator is null', async () => {
    // fix round 1, M4: a null orchestrator (an injected test server, never a real one) gets
    // the generic label and no code - never a made-up "orchestrator_unavailable".
    vi.mocked(api.getSystemStatus).mockResolvedValue(status({ orchestrator: null }))
    render(<SystemStatusBanner />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('הסוכן האוטומטי אינו זמין')
    expect(alert.querySelector('.mono')).not.toBeInTheDocument()
  })

  it('re-polls every 60 seconds', async () => {
    vi.useFakeTimers()
    vi.mocked(api.getSystemStatus).mockResolvedValue(status())
    render(<SystemStatusBanner />)

    await act(async () => {
      await Promise.resolve() // let the mount effect's first load() settle
    })
    expect(api.getSystemStatus).toHaveBeenCalledTimes(1)
    await act(() => vi.advanceTimersByTimeAsync(60000))
    expect(api.getSystemStatus).toHaveBeenCalledTimes(2)
  })

  it('stops polling once unmounted', async () => {
    vi.useFakeTimers()
    vi.mocked(api.getSystemStatus).mockResolvedValue(status())
    const { unmount } = render(<SystemStatusBanner />)

    await act(async () => {
      await Promise.resolve() // let the mount effect's first load() settle
    })
    expect(api.getSystemStatus).toHaveBeenCalledTimes(1)

    unmount()
    await act(() => vi.advanceTimersByTimeAsync(60000 * 3))
    expect(api.getSystemStatus).toHaveBeenCalledTimes(1) // no fetch after unmount, ever
  })

  it('warns about the document-service when its health check fails, beside the code', async () => {
    vi.mocked(api.getSystemStatus).mockResolvedValue(
      status({
        documents: { configured: true, health: 'unreachable', last_ok_at: null, last_error: null, last_error_at: null },
      }),
    )
    render(<SystemStatusBanner />)
    expect(await screen.findByText('שירות המסמכים אינו זמין - מטופלים אינם יכולים להעלות מסמכים')).toBeInTheDocument()
    expect(screen.getByText('unreachable')).toHaveClass('mono')
  })

  it("warns about the document classifier when the service is up but the last upload failed on it", async () => {
    vi.mocked(api.getSystemStatus).mockResolvedValue(
      status({
        documents: {
          configured: true,
          health: 'ok',
          last_ok_at: '2026-10-01T08:00:00Z',
          last_error: 'classifier_unavailable',
          last_error_at: '2026-10-01T09:00:00Z',
        },
      }),
    )
    render(<SystemStatusBanner />)
    expect(await screen.findByText('סיווג המסמכים אינו זמין (ספק ה-LLM) - העלאות נכשלות')).toBeInTheDocument()
  })

  it('says nothing about documents once a later upload was answered, or when the service is not configured', async () => {
    vi.mocked(api.getSystemStatus).mockResolvedValue(
      status({
        documents: {
          configured: true,
          health: 'ok',
          last_ok_at: '2026-10-01T10:00:00Z',
          last_error: 'no_answer',
          last_error_at: '2026-10-01T09:00:00Z',
        },
      }),
    )
    const { unmount } = render(<SystemStatusBanner />)
    await waitFor(() => expect(api.getSystemStatus).toHaveBeenCalled())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    unmount()

    vi.mocked(api.getSystemStatus).mockResolvedValue(
      status({ documents: { configured: false, health: null, last_ok_at: null, last_error: null, last_error_at: null } }),
    )
    render(<SystemStatusBanner />)
    await waitFor(() => expect(api.getSystemStatus).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
