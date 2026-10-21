import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiGet, clearCsrfToken } from './http'

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

async function capture(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise
    throw new Error('expected the request to reject')
  } catch (error) {
    return error
  }
}

describe('http request error handling', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    clearCsrfToken()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('rethrows a recognized DOMException AbortError instead of reporting an outage', async () => {
    const abort = new DOMException('The operation was aborted.', 'AbortError')
    vi.mocked(fetch).mockRejectedValueOnce(abort)

    const error = await capture(apiGet('/clients'))

    expect(error).toBe(abort)
    expect(error).not.toBeInstanceOf(ApiError)
  })

  it('rethrows a non-abort error when the signal is already aborted (Safari cancellation)', async () => {
    const controller = new AbortController()
    controller.abort()
    // Safari can surface a cancelled fetch as a TypeError rather than AbortError.
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError('Load failed'))

    const error = await capture(apiGet('/clients', controller.signal))

    expect(error).toBeInstanceOf(TypeError)
    expect(error).not.toBeInstanceOf(ApiError)
    expect((error as Error).message).not.toMatch(/Could not reach the backend/)
  })

  it('rethrows an AbortError-named error that is not a DOMException instance', async () => {
    const named = new Error('cancelled')
    named.name = 'AbortError'
    vi.mocked(fetch).mockRejectedValueOnce(named)

    const error = await capture(apiGet('/clients'))

    expect(error).toBe(named)
    expect(error).not.toBeInstanceOf(ApiError)
  })

  it('classifies a genuine network failure as a backend-outage error when not aborted', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError('NetworkError'))

    const error = await capture(apiGet('/clients'))

    expect(error).toBeInstanceOf(ApiError)
    expect((error as Error).message).toMatch(/Could not reach the backend/)
  })

  it('surfaces HTTP error responses as ApiError with the server detail and status', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ detail: 'Authentication required.' }, 401),
    )

    const error = await capture(apiGet('/clients'))

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(401)
    expect((error as Error).message).toBe('Authentication required.')
  })
})
