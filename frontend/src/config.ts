export const LOCAL_API_BASE_URL = 'http://127.0.0.1:8000'

type ResolveOptions = { production?: boolean }

/**
 * Resolve the backend base URL from VITE_API_BASE_URL.
 *
 * Local development falls back to the loopback backend. Production builds must
 * configure the variable explicitly: silently calling 127.0.0.1 from a hosted
 * frontend can never work, so fail loudly instead of shipping a broken app.
 */
export function resolveApiBaseUrl(
  configured: string | undefined,
  { production = import.meta.env.PROD }: ResolveOptions = {},
): string {
  const trimmed = configured?.trim().replace(/\/+$/, '')
  if (trimmed) return trimmed
  if (production) {
    throw new Error(
      'VITE_API_BASE_URL is required for production builds. Set it to the ' +
        'backend base URL (for example /api when the API is proxied on the ' +
        'same origin).',
    )
  }
  return LOCAL_API_BASE_URL
}

/** Base URL of the backend API, configurable via VITE_API_BASE_URL. */
export const apiBaseUrl: string = resolveApiBaseUrl(
  import.meta.env.VITE_API_BASE_URL,
)
