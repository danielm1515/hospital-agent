/**
 * Route path constants, in one leaf module.
 *
 * This file must import nothing from `./App` or from any component module:
 * `App.tsx` imports `PatientRoutes`/`StaffRoutes`, which (via page modules such as
 * `pages/patient/paths.ts` and the login pages) need these constants, so keeping
 * them here — with no imports of its own — breaks that import cycle.
 */
export const PATIENT_HOME = '/patient'
export const STAFF_HOME = '/staff'
export const PATIENT_LOGIN = '/login'
export const STAFF_LOGIN = '/staff/login'
