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

/** Row 98: the document-service, beside its code (`health` or the last upload's error). */
const DOCUMENT_LABELS: Record<string, string> = {
  unreachable: 'שירות המסמכים אינו זמין - מטופלים אינם יכולים להעלות מסמכים',
  degraded: 'שירות המסמכים פועל חלקית - העלאות עלולות להיכשל',
  classifier_unavailable: 'סיווג המסמכים אינו זמין (ספק ה-LLM) - העלאות נכשלות',
  no_answer: 'שירות המסמכים לא ענה בהעלאה האחרונה',
}
const DOCUMENT_GENERIC = 'שירות המסמכים אינו זמין'

export function documentStatusLabel(code: string): string {
  return DOCUMENT_LABELS[code] ?? DOCUMENT_GENERIC
}

function isNewer(a: string, b: string): boolean {
  return new Date(a).getTime() > new Date(b).getTime()
}

/** Whether the banner should show at all, and the code (if any) to show beside the label.
 * `code: null` happens only for a `null` `orchestrator` (an injected test server, never a real
 * one - fix round 1, M4): there is no invented code for that, just the generic label. */
interface Trouble {
  code: string | null
}

function trouble(status: SystemStatus): Trouble | null {
  if (status.orchestrator === null) return { code: null }
  if (status.orchestrator !== 'running') return { code: status.orchestrator }
  const { last_error, last_ok_at, last_error_at } = status.llm
  if (last_error && last_error_at && (!last_ok_at || isNewer(last_error_at, last_ok_at))) return { code: last_error }
  return null
}

/**
 * Row 98: the document-service's trouble code, or null. The live health check comes first - it
 * sees an outage before any patient tries; with the service up, the last upload's error still
 * counts while it is newer than the last answered upload (e.g. the classifier's LLM provider).
 */
function documentsTrouble(status: SystemStatus): string | null {
  const documents = status.documents
  if (!documents?.configured) return null
  if (documents.health && documents.health !== 'ok') return documents.health
  const { last_error, last_error_at, last_ok_at } = documents
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
  const found = trouble(status)
  const documents = documentsTrouble(status)
  if (found === null && documents === null) return null

  return (
    <>
      {found && (
        <Alert variant="error" title={found.code === null ? GENERIC_LABEL : systemStatusLabel(found.code)}>
          {found.code === null ? undefined : <span className="mono">{found.code}</span>}
        </Alert>
      )}
      {documents && (
        <Alert variant="error" title={documentStatusLabel(documents)}>
          <span className="mono">{documents}</span>
        </Alert>
      )}
    </>
  )
}
