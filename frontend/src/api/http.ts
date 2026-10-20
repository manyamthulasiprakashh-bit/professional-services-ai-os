/** Shared shape of API errors raised by the http helper. */
export class ApiError extends Error {
  readonly status?: number

  constructor(message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * CSRF double-submit token contract (backend Step 6.2C-2).
 *
 * The backend sets a JS-readable `ps_csrf` cookie and requires its value in the
 * `X-CSRF-Token` header on unsafe requests (POST/PUT/PATCH/DELETE). The session
 * cookie itself is HttpOnly and never touched by JavaScript. The token is kept
 * in memory; because the server rotates it on login, we refresh it from the
 * readable cookie before each unsafe request rather than caching a stale value.
 * It is never written to localStorage/sessionStorage.
 */
export const CSRF_COOKIE_NAME = 'ps_csrf'
export const CSRF_HEADER_NAME = 'X-CSRF-Token'

let csrfToken: string | null = null

/** Read the current CSRF token from the readable cookie (browser only). */
function readCsrfCookie(): string | null {
  if (typeof document === 'undefined') return null
  const prefix = `${CSRF_COOKIE_NAME}=`
  const match = document.cookie
    .split('; ')
    .find((part) => part.startsWith(prefix))
  if (!match) return null
  return decodeURIComponent(match.slice(prefix.length))
}

/** Remember a CSRF token in memory (used after login/csrf acquisition). */
export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

/** Clear the in-memory CSRF token (used on logout). */
export function clearCsrfToken(): void {
  csrfToken = null
}

function currentCsrfToken(): string | null {
  // Prefer the freshest value the server currently exposes; fall back to the
  // in-memory token when the cookie is not yet present (e.g. in tests).
  return readCsrfCookie() ?? csrfToken
}

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

/** Fetch a JSON object from the backend, throwing ApiError on any failure. */
export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'GET' }, signal)
}

export async function apiPost<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return request<T>(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    signal,
  )
}

export async function apiPostMultipart<T>(
  path: string,
  form: FormData,
  signal?: AbortSignal,
): Promise<T> {
  return request<T>(path, { method: 'POST', body: form }, signal)
}

async function request<T>(
  path: string,
  init: RequestInit,
  signal?: AbortSignal,
): Promise<T> {
  const base = (
    import.meta.env.VITE_API_BASE_URL?.trim() || 'http://127.0.0.1:8000'
  ).replace(/\/+$/, '')

  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (UNSAFE_METHODS.has(method)) {
    const token = currentCsrfToken()
    if (token) headers.set(CSRF_HEADER_NAME, token)
  }

  let response: Response
  try {
    response = await fetch(`${base}${path}`, {
      ...init,
      headers,
      // Send/receive the HttpOnly session cookie and the CSRF cookie.
      credentials: 'include',
      signal,
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError(`Could not reach the backend at ${base}. Is it running?`)
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status} ${response.statusText}`
    try {
      const payload = (await response.json()) as { detail?: unknown }
      if (typeof payload.detail === 'string') detail = payload.detail
    } catch {
      // keep the HTTP status text
    }
    throw new ApiError(detail, response.status)
  }

  return (await response.json()) as T
}
