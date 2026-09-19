import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import * as api from '../api/client'
import type { Me } from '../api/types'

/**
 * The signed-in identity. The token lives in the API client (`sessionStorage`);
 * this context only mirrors who it belongs to. A 401 from any call clears both.
 */
export type AuthStatus = 'loading' | 'ready'

export interface AuthValue {
  user: Me | null
  status: AuthStatus
  login: (userId: string, password: string) => Promise<Me>
  logout: () => void
}

export const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null)
  const [status, setStatus] = useState<AuthStatus>(() => (api.getToken() ? 'loading' : 'ready'))

  // A 401 anywhere ends the session; the route guards then send the user to a login page.
  useEffect(() => {
    api.setOnUnauthorized(() => setUser(null))
    return () => api.setOnUnauthorized(null)
  }, [])

  // Restore the session from a token left in sessionStorage.
  useEffect(() => {
    if (!api.getToken()) return
    let cancelled = false
    api
      .me()
      .then((me) => {
        if (!cancelled) setUser(me)
      })
      .catch(() => {
        if (!cancelled) setUser(null)
      })
      .finally(() => {
        if (!cancelled) setStatus('ready')
      })
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (userId: string, password: string) => {
    const result = await api.login(userId, password)
    const me: Me = { user_id: result.user_id, role: result.role, display_name: result.display_name }
    setUser(me)
    setStatus('ready')
    return me
  }, [])

  const logout = useCallback(() => {
    api.logout()
    setUser(null)
    setStatus('ready')
  }, [])

  const value = useMemo<AuthValue>(() => ({ user, status, login, logout }), [user, status, login, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside an AuthProvider')
  return value
}
