import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import { AppShell } from '../../components/AppShell'
import { SystemStatusBanner } from '../../components/SystemStatusBanner'
import { CaseMonitor } from './CaseMonitor'
import { Metrics } from './Metrics'
import { ReviewCase } from './ReviewCase'
import { ReviewQueue } from './ReviewQueue'

/**
 * The staff area (design §5): the review queue, one case in review, the Case Monitor and -
 * for admin_staff only (sub-project 14) - the system metrics. Hiding the link is a
 * convenience; the server's require_admin is the gate.
 */
export function StaffRoutes() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin_staff'
  return (
    <AppShell
      nav={[
        { to: '/staff', label: 'תור הסלמות', end: true },
        { to: '/staff/monitor', label: 'כל הפניות' },
        ...(isAdmin ? [{ to: '/staff/metrics', label: 'מדדי מערכת' }] : []),
      ]}
    >
      <SystemStatusBanner />
      <Routes>
        <Route index element={<ReviewQueue />} />
        <Route path="cases/:caseId" element={<ReviewCase />} />
        <Route path="monitor" element={<CaseMonitor />} />
        <Route path="metrics" element={isAdmin ? <Metrics /> : <Navigate to="/staff" replace />} />
        <Route path="*" element={<Navigate to="/staff" replace />} />
      </Routes>
    </AppShell>
  )
}
