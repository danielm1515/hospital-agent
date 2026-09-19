import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '../../components/AppShell'
import { CaseMonitor } from './CaseMonitor'
import { ReviewCase } from './ReviewCase'
import { ReviewQueue } from './ReviewQueue'

/** The staff area (design §5): the review queue, one case in review, and the Case Monitor. */
export function StaffRoutes() {
  return (
    <AppShell
      nav={[
        { to: '/staff', label: 'תור הסלמות', end: true },
        { to: '/staff/monitor', label: 'כל הפניות' },
      ]}
    >
      <Routes>
        <Route index element={<ReviewQueue />} />
        <Route path="cases/:caseId" element={<ReviewCase />} />
        <Route path="monitor" element={<CaseMonitor />} />
        <Route path="*" element={<Navigate to="/staff" replace />} />
      </Routes>
    </AppShell>
  )
}
