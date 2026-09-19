import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '../../components/AppShell'
import { MyRequests } from './MyRequests'
import { NewRequest } from './NewRequest'
import { RequestDetail } from './RequestDetail'

/** The patient area's routes (design §4). */
export function PatientRoutes() {
  return (
    <AppShell nav={[{ to: '/patient', label: 'הפניות שלי', end: true }]}>
      <Routes>
        <Route index element={<MyRequests />} />
        <Route path="new" element={<NewRequest />} />
        <Route path="requests/:caseId" element={<RequestDetail />} />
        <Route path="*" element={<Navigate to="/patient" replace />} />
      </Routes>
    </AppShell>
  )
}
