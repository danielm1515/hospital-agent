import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '../../components/AppShell'

/**
 * The staff area's routes. Placeholder screens; plan Task 3 replaces them with
 * ReviewQueue, ReviewCase and CaseMonitor.
 */
export function StaffRoutes() {
  return (
    <AppShell
      nav={[
        { to: '/staff', label: 'תור הסלמות', end: true },
        { to: '/staff/monitor', label: 'כל הפניות' },
      ]}
    >
      <Routes>
        <Route index element={<StaffPlaceholder title="תור הסלמות" />} />
        <Route path="cases/:caseId" element={<StaffPlaceholder title="פנייה בתור" />} />
        <Route path="monitor" element={<StaffPlaceholder title="כל הפניות" />} />
        <Route path="*" element={<Navigate to="/staff" replace />} />
      </Routes>
    </AppShell>
  )
}

function StaffPlaceholder({ title }: { title: string }) {
  return (
    <section className="card">
      <h1 className="page-h">{title}</h1>
      <p className="lede">המסך ייבנה בשלב הבא של הפיתוח.</p>
    </section>
  )
}
