import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  fetchCurrentUser,
  login as apiLogin,
  logout as apiLogout,
} from '../api/auth'
import { ApiError, clearCsrfToken } from '../api/http'
import type { CurrentUser } from '../types/auth'
import { AuthContext, type AuthStatus } from './context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [restoreError, setRestoreError] = useState<string | null>(null)

  // Single-flight guard: only one restore request may be in flight at a time,
  // so StrictMode double-invocation and rapid mounts cannot race.
  const inFlight = useRef<Promise<void> | null>(null)

  const applyUnauthenticated = useCallback(() => {
    setUser(null)
    setStatus('unauthenticated')
  }, [])

  const restore = useCallback(async () => {
    if (inFlight.current) return inFlight.current
    const run = (async () => {
      setRestoreError(null)
      try {
        const current = await fetchCurrentUser()
        // Never trust frontend-supplied identity: this is the server's answer.
        setUser(current)
        setStatus('authenticated')
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          // Confirmed: the session is absent/expired/revoked.
          clearCsrfToken()
          applyUnauthenticated()
          return
        }
        // Transient/unknown failure: not a confirmed logout. Surface it.
        setRestoreError(
          'Could not confirm your session with the server. You can sign in again.',
        )
        applyUnauthenticated()
      } finally {
        inFlight.current = null
      }
    })()
    inFlight.current = run
    return run
  }, [applyUnauthenticated])

  useEffect(() => {
    void restore()
  }, [restore])

  const login = useCallback(async (username: string, password: string) => {
    const current = await apiLogin(username, password)
    setUser(current)
    setStatus('authenticated')
    setRestoreError(null)
  }, [])

  const logout = useCallback(async () => {
    try {
      await apiLogout()
    } finally {
      // Always clear local identity and CSRF state, even if the call failed.
      clearCsrfToken()
      setUser(null)
      setStatus('unauthenticated')
    }
  }, [])

  const refresh = useCallback(async () => {
    inFlight.current = null
    await restore()
  }, [restore])

  return (
    <AuthContext.Provider
      value={{ status, user, restoreError, login, logout, refresh }}
    >
      {children}
    </AuthContext.Provider>
  )
}
