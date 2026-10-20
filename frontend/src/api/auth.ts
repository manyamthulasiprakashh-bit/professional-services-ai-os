import { apiGet, apiPost, clearCsrfToken, setCsrfToken } from './http'
import type { CurrentUser } from '../types/auth'

/**
 * Authentication API (backend Step 6.2C-2 contract).
 *
 * - `GET  /auth/csrf`  → `{ csrf_token }` and a readable `ps_csrf` cookie,
 *   used to protect the login request itself.
 * - `POST /auth/login` → `CurrentUser`; sets the HttpOnly session cookie plus a
 *   rotated `ps_csrf` cookie. Requires the CSRF header.
 * - `POST /auth/logout` → `{ status: "logged_out" }`; revokes the session.
 * - `GET  /auth/me`    → `CurrentUser` for the verified session.
 *
 * Raw session tokens are only ever held by the browser as HttpOnly cookies and
 * are never read by this module.
 */

/** Acquire a CSRF token (JSON body) and remember it in memory. */
export async function fetchCsrfToken(signal?: AbortSignal): Promise<string> {
  const body = await apiGet<{ csrf_token: string }>('/auth/csrf', signal)
  setCsrfToken(body.csrf_token)
  return body.csrf_token
}

/** Log in with credentials and return the safe current-user record. */
export async function login(
  username: string,
  password: string,
): Promise<CurrentUser> {
  // Ensure a CSRF token exists for the login request itself (single fetch).
  await fetchCsrfToken()
  return apiPost<CurrentUser>('/auth/login', { username, password })
}

/** Revoke the current session server-side and clear local CSRF state. */
export async function logout(): Promise<void> {
  try {
    await apiPost<{ status: string }>('/auth/logout', {})
  } finally {
    clearCsrfToken()
  }
}

/** Look up the current user; throws ApiError(401) when unauthenticated. */
export function fetchCurrentUser(signal?: AbortSignal): Promise<CurrentUser> {
  return apiGet<CurrentUser>('/auth/me', signal)
}
