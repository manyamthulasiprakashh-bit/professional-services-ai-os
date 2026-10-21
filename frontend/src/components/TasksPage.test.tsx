import { StrictMode } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TasksPage from './TasksPage'

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('TasksPage', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('does not show a backend error when cleanup cancels the initial load', async () => {
    // Simulate Safari: a cancelled fetch rejects with TypeError, not AbortError.
    let sawCancellation = false
    vi.mocked(fetch).mockImplementation((_input, init) => {
      const signal = (init as RequestInit | undefined)?.signal
      return new Promise<Response>((resolve, reject) => {
        const onAbort = () => {
          sawCancellation = true
          reject(new TypeError('Load failed'))
        }
        if (signal?.aborted) {
          onAbort()
          return
        }
        signal?.addEventListener('abort', onAbort, { once: true })
        queueMicrotask(() => {
          if (signal?.aborted) return
          signal?.removeEventListener('abort', onAbort)
          resolve(jsonResponse([]))
        })
      })
    })

    render(
      <StrictMode>
        <TasksPage />
      </StrictMode>,
    )

    expect(await screen.findByText(/No tasks yet/i)).toBeInTheDocument()
    expect(sawCancellation).toBe(true)
    expect(screen.queryByText(/Could not load tasks/i)).not.toBeInTheDocument()
    expect(
      screen.queryByText(/Could not reach the backend/i),
    ).not.toBeInTheDocument()
  })

  it('shows a connection error for a genuine request failure that is not cancelled', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('NetworkError'))

    render(<TasksPage />)

    expect(await screen.findByText(/Could not load tasks/i)).toBeInTheDocument()
    expect(screen.getByText(/Could not reach the backend/i)).toBeInTheDocument()
  })

  it('recovers when Retry issues a fresh pair of requests', async () => {
    vi.mocked(fetch)
      .mockRejectedValueOnce(new TypeError('NetworkError')) // GET /tasks
      .mockRejectedValueOnce(new TypeError('NetworkError')) // GET /matters
      .mockImplementation(() => Promise.resolve(jsonResponse([]))) // retry pair

    render(<TasksPage />)
    expect(await screen.findByText(/Could not load tasks/i)).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /retry/i }))

    expect(await screen.findByText(/No tasks yet/i)).toBeInTheDocument()
    expect(screen.queryByText(/Could not load tasks/i)).not.toBeInTheDocument()
  })
})
