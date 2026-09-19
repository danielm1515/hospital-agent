/** The patient area's routes, in one place (wired in `PatientRoutes`). */
import { PATIENT_HOME } from '../../App'

export const MY_REQUESTS = PATIENT_HOME
export const NEW_REQUEST = `${PATIENT_HOME}/new`

export function requestPath(caseId: string): string {
  return `${PATIENT_HOME}/requests/${encodeURIComponent(caseId)}`
}
