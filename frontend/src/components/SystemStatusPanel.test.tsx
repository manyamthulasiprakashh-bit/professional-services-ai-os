import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SystemStatusPanel from './SystemStatusPanel'
import { apiBaseUrl } from '../config'

const HEALTH_PAYLOAD = {
  status: 'healthy',
  application: 'Professional Services AI OS',
  version: '0.1.0',
}

describe('SystemStatusPanel', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the backend application, version and status after a successful fetch', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify(HEALTH_PAYLOAD), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    render(<SystemStatusPanel />)

    expect(await screen.findByTestId('health-application')).toHaveTextContent(
      'Professional Services AI OS',
    )
    expect(screen.getByTestId('health-version')).toHaveTextContent('0.1.0')
    expect(screen.getByTestId('health-status')).toHaveTextContent('healthy')
    expect(fetch).toHaveBeenCalledWith(
      `${apiBaseUrl}/health`,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('shows an error state when the backend returns a non-2xx response', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response('boom', { status: 503, statusText: 'Service Unavailable' }),
    )

    render(<SystemStatusPanel />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/HTTP 503/)
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('shows an error state when the backend is unreachable', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'))

    render(<SystemStatusPanel />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /Could not reach the backend/,
    )
  })
})
