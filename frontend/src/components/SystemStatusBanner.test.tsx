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
})
