import type { HealthResponse } from '../types/health'

/** Error raised when the health check cannot be completed successfully. */
export class HealthRequestError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'HealthRequestError'
  }
}

function stripTrailingSlash(url: string): string {
  return url.replace(/\/+$/, '')
}

function isHealthResponse(data: unknown): data is HealthResponse {
  if (typeof data !== 'object' || data === null) return false
  const record = data as Record<string, unknown>
  return (
    typeof record.status === 'string' &&
    typeof record.application === 'string' &&
    typeof record.version === 'string'
  )
}

/**
 * Fetch GET /health from the backend.
 *
 * Throws HealthRequestError on network failure, abort/timeout,
 * non-2xx responses, or payloads that do not match the API contract.
 */
export async function fetchHealth(
  baseUrl: string,
  timeoutMs = 8000,
  externalSignal?: AbortSignal,
): Promise<HealthResponse> {
  const url = `${stripTrailingSlash(baseUrl)}/health`
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(new Error('timeout')), timeoutMs)

  const onExternalAbort = () => controller.abort(externalSignal?.reason)
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort(externalSignal.reason)
    else externalSignal.addEventListener('abort', onExternalAbort)
  }

  try {
    let response: Response
    try {
      response = await fetch(url, { signal: controller.signal })
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === 'AbortError') {
        throw new HealthRequestError(
          externalSignal?.aborted
            ? 'Request cancelled.'
            : `Health check timed out after ${timeoutMs} ms.`,
        )
      }
      throw new HealthRequestError(
        `Could not reach the backend at ${url}. Is it running? (${String(cause)})`,
      )
    }

    if (!response.ok) {
      throw new HealthRequestError(
        `Health check failed: HTTP ${response.status} ${response.statusText}`,
      )
    }

    let payload: unknown
    try {
      payload = await response.json()
    } catch {
      throw new HealthRequestError('Health check returned invalid JSON.')
    }

    if (!isHealthResponse(payload)) {
      throw new HealthRequestError('Health check returned an unexpected payload shape.')
    }

    return payload
  } finally {
    clearTimeout(timer)
    externalSignal?.removeEventListener('abort', onExternalAbort)
  }
}
