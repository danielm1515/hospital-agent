/**
 * Staff-fixes design Task 1, decision 3: is the LLM (still) called at all, shown to staff
 * as a banner while the Agent Orchestrator does not run or the last LLM call failed.
 *
 * `GET /api/staff/system-status` is polled - there is no push for this (same choice as the
 * review queue, ReviewQueue.tsx). Staff-only: never rendered on the patient area.
 */
import { useEffect, useState } from 'react'
import * as api from '../api/client'
import type { SystemStatus } from '../api/types'
import { Alert } from './Alert'

export const SYSTEM_STATUS_POLL_MS = 60000

/** A Hebrew label beside the code, never instead of it (CLAUDE.md) - an unknown code falls
 * back to a generic "not available" sentence rather than showing nothing. */
const STATUS_LABELS: Record<string, string> = {
  insufficient_quota: 'נגמר הקרדיט בחשבון OpenAI - הפניות מוסלמות לצוות',
  credit_balance_exhausted: 'נגמר הקרדיט בחשבון OpenAI - הפניות מוסלמות לצוות',
  AuthenticationError: 'מפתח OpenAI אינו תקין',
  'disabled: OPENAI_API_KEY is not set': 'מפתח OpenAI לא הוגדר - הסוכן האוטומטי כבוי',
}
const GENERIC_LABEL = 'הסוכן האוטומטי אינו זמין'

/** `code` is either the orchestrator status or an `llm.last_error` value (e.g.
 * `api:RateLimitError:insufficient_quota`, `api:AuthenticationError`,
 * `api:AuthenticationError:invalid_api_key`) - so a known key is matched either as the whole
 * code or as one `:`-separated segment of it, not by exact equality alone. */
export function systemStatusLabel(code: string): string {
  const segments = code.split(':')
  const known = Object.keys(STATUS_LABELS).find((key) => code === key || segments.includes(key))
  return known ? STATUS_LABELS[known] : GENERIC_LABEL
}

function isNewer(a: string, b: string): boolean {
  return new Date(a).getTime() > new Date(b).getTime()
}

/** Whether the banner should show at all, and the code it should show beside the label. */
function trouble(status: SystemStatus): string | null {
  if (status.orchestrator !== 'running') return status.orchestrator ?? 'orchestrator_unavailable'
  const { last_error, last_ok_at, last_error_at } = status.llm
  if (last_error && last_error_at && (!last_ok_at || isNewer(last_error_at, last_ok_at))) return last_error
  return null
}

export function SystemStatusBanner() {
  const [status, setStatus] = useState<SystemStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const result = await api.getSystemStatus()
        if (!cancelled) setStatus(result)
      } catch {
        // A status check that itself fails to load is not shown as a second alarm - the
        // existing banner (if any) is left as it was until the next poll succeeds.
      }
    }
    void load()
    const timer = setInterval(() => void load(), SYSTEM_STATUS_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  if (!status) return null
  const code = trouble(status)
  if (code === null) return null

  return (
    <Alert variant="error" title={systemStatusLabel(code)}>
      <span className="mono">{code}</span>
    </Alert>
  )
}
