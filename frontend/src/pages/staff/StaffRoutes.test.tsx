import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api/client'
import { makeUser, renderWithAuth } from '../../test/helpers'
import { StaffRoutes } from './StaffRoutes'

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  getMetrics: vi.fn(),
  listReviews: vi.fn(),
  getSystemStatus: vi.fn(),
}))

const staffArea = (
  <Routes>
    <Route path="/staff/*" element={<StaffRoutes />} />
  </Routes>
)

beforeEach(() => {
  vi.mocked(api.listReviews).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(api.getMetrics).mockReturnValue(new Promise(() => {}))
  vi.mocked(api.getSystemStatus).mockReturnValue(new Promise(() => {}))
})

describe('StaffRoutes', () => {
  it('gives admin_staff the metrics link and page', async () => {
    renderWithAuth(staffArea, { user: makeUser('admin_staff'), route: '/staff/metrics' })
    expect(screen.getByRole('link', { name: 'מדדי מערכת' })).toHaveAttribute('href', '/staff/metrics')
    expect(await screen.findByRole('heading', { name: 'מדדי מערכת', level: 1 })).toBeInTheDocument()
    expect(api.getMetrics).toHaveBeenCalledTimes(1)
  })

  it('hides the link from clinical_staff and sends the address back to the queue', async () => {
    renderWithAuth(staffArea, { user: makeUser('clinical_staff'), route: '/staff/metrics' })
    expect(screen.queryByRole('link', { name: 'מדדי מערכת' })).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'תור הסלמות' })).toBeInTheDocument()
    expect(api.getMetrics).not.toHaveBeenCalled()
  })
})
