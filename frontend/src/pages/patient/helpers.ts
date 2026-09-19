/**
 * Small helpers shared by the patient screens (design §4).
 *
 * Nothing here decides anything: the status always comes from the API response
 * (`docs/api.md` §4), and these functions only shape it for the screen.
 */
import { useEffect, useRef } from 'react'
import { ApiError } from '../../api/client'
import type { PatientStatus } from '../../api/types'

/** The patient screens refresh every 3 s while a case is still moving (design §4). */
export const POLL_MS = 3000

/** The card text is cut at 120 characters on the list screen. */
export const SUMMARY_LENGTH = 120

/** The two statuses the agent advances on its own; the rest wait for a person. */
export function isMoving(status: PatientStatus): boolean {
  return status === 'received' || status === 'in_progress'
}

export function truncate(text: string, max = SUMMARY_LENGTH): string {
  return text.length <= max ? text : `${text.slice(0, max)}…`
}

const DATE_TIME = new Intl.DateTimeFormat('he-IL', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

/** An ISO timestamp as Hebrew date and time; an unparsable one is shown as it came. */
export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : DATE_TIME.format(date)
}

/**
 * The demo's document ids, for a readable line next to the id itself. An id the
 * demo does not know is shown as the id alone - the UI never invents a name.
 */
const DOCUMENT_LABELS: Record<string, string> = {
  referral: 'הפניה',
  blood_test: 'בדיקת דם',
  imaging: 'הדמיה',
  consent_form: 'טופס הסכמה',
}

export function documentLabel(documentId: string): string | null {
  return DOCUMENT_LABELS[documentId] ?? null
}

/**
 * A failed call, as one Hebrew sentence. The patient never sees an internal
 * code, an escalation kind or a policy reason (§12.3, `docs/api.md` §4).
 */
export function errorMessage(caught: unknown): string {
  if (!(caught instanceof ApiError)) return 'אירעה תקלה. נסו שוב בעוד רגע.'
  if (caught.status === 0) return 'אין חיבור לשרת. בדקו את החיבור ונסו שוב.'
  if (caught.status === 404) return 'הפנייה לא נמצאה.'
  if (caught.status === 422 || caught.detail === 'validation_error') return 'הפרטים שהוזנו אינם תקינים.'
  return 'אירעה תקלה. נסו שוב בעוד רגע.'
}

/** Runs `tick` every `POLL_MS` while `active`, always with the latest closure. */
export function usePolling(active: boolean, tick: () => void): void {
  const latest = useRef(tick)
  useEffect(() => {
    latest.current = tick
  })
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => latest.current(), POLL_MS)
    return () => clearInterval(timer)
  }, [active])
}
