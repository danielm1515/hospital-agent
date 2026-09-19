import type { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './auth/AuthContext'
import { isStaffRole } from './api/types'
import { PatientLogin } from './pages/patient/PatientLogin'
import { PatientRoutes } from './pages/patient/PatientRoutes'
import { StaffLogin } from './pages/staff/StaffLogin'
import { StaffRoutes } from './pages/staff/StaffRoutes'

export const PATIENT_HOME = '/patient'
export const STAFF_HOME = '/staff'
export const PATIENT_LOGIN = '/login'
export const STAFF_LOGIN = '/staff/login'

type Area = 'patient' | 'staff'

/** Sends a signed-in user to their area, everyone else to the patient login. */
function HomeRedirect() {
  const { user, status } = useAuth()
  if (status === 'loading') return <Loading />
  if (!user) return <Navigate to={PATIENT_LOGIN} replace />
  return <Navigate to={isStaffRole(user.role) ? STAFF_HOME : PATIENT_HOME} replace />
}

export function Loading() {
  return (
    <p className="page-loading" role="status">
      טוען…
    </p>
  )
}

/** Route guard: no token means the area's login page; a wrong role means the user's own area. */
export function RequireRole({ area, children }: { area: Area; children: ReactNode }) {
  const { user, status } = useAuth()
  const location = useLocation()
  if (status === 'loading') return <Loading />
  if (!user) {
    const to = area === 'staff' ? STAFF_LOGIN : PATIENT_LOGIN
    return <Navigate to={to} replace state={{ from: location.pathname }} />
  }
  const userArea: Area = isStaffRole(user.role) ? 'staff' : 'patient'
  if (userArea !== area) return <Navigate to={userArea === 'staff' ? STAFF_HOME : PATIENT_HOME} replace />
  return <>{children}</>
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<HomeRedirect />} />
      <Route path={PATIENT_LOGIN} element={<PatientLogin />} />
      <Route path={STAFF_LOGIN} element={<StaffLogin />} />
      <Route
        path="/patient/*"
        element={
          <RequireRole area="patient">
            <PatientRoutes />
          </RequireRole>
        }
      />
      <Route
        path="/staff/*"
        element={
          <RequireRole area="staff">
            <StaffRoutes />
          </RequireRole>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
