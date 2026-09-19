import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '../../components/AppShell'

/**
 * The patient area's routes. Placeholder screens; plan Task 2 replaces them with
 * MyRequests, NewRequest and RequestDetail.
 */
export function PatientRoutes() {
  return (
    <AppShell nav={[{ to: '/patient', label: 'הפניות שלי', end: true }]}>
      <Routes>
        <Route index element={<PatientPlaceholder title="הפניות שלי" />} />
        <Route path="new" element={<PatientPlaceholder title="פנייה חדשה" />} />
        <Route path="requests/:caseId" element={<PatientPlaceholder title="פרטי פנייה" />} />
        <Route path="*" element={<Navigate to="/patient" replace />} />
      </Routes>
    </AppShell>
  )
}

function PatientPlaceholder({ title }: { title: string }) {
  return (
    <section className="card">
      <h1 className="page-h">{title}</h1>
      <p className="lede">המסך ייבנה בשלב הבא של הפיתוח.</p>
    </section>
  )
}
