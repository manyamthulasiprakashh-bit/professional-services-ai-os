import { createContext, useContext } from 'react'
import type { CurrentUser } from '../types/auth'

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

export interface AuthContextValue {
  status: AuthStatus
  user: CurrentUser | null
  /**
   * Set when session restoration failed for a reason other than a confirmed
   * 401 (e.g. the backend was unreachable). Distinguished so a transient
   * failure is never presented as a confirmed logout.
   */
  restoreError: string | null
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  /** Force a re-check of the session (used after a 401 from a protected call). */
  refresh: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
