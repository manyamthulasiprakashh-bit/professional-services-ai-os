import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ClientsPage from './ClientsPage'

const EMPTY: unknown[] = []

const CREATED_CLIENT = {
  id: 1,
  name: 'Acme Legal LLP',
  email: 'ops@acme.test',
  created_at: '2026-01-01T00:00:00+00:00',
}

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('ClientsPage', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the empty state when the backend has no clients', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(EMPTY))

    render(<ClientsPage />)

    expect(
      await screen.findByText(/No clients yet/i),
    ).toBeInTheDocument()
  })

  it('creates a client and refreshes the list', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(EMPTY)) // initial GET
      .mockResolvedValueOnce(jsonResponse(CREATED_CLIENT, 201)) // POST
      .mockResolvedValueOnce(jsonResponse([CREATED_CLIENT])) // refresh GET

    render(<ClientsPage />)

    await screen.findByText(/No clients yet/i)

    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/^Name/i), 'Acme Legal LLP')
    await user.click(screen.getByRole('button', { name: /create client/i }))

    await waitFor(() =>
      expect(screen.getByText(/"Acme Legal LLP" created/i)).toBeInTheDocument(),
    )
    expect(screen.getByText('Acme Legal LLP')).toBeInTheDocument()

    const postCall = vi.mocked(fetch).mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === 'POST',
    )
    expect(postCall).toBeDefined()
    expect(String(postCall?.[0])).toContain('/clients')
  })

  it('surfaces backend validation errors from the create form', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(EMPTY))
      .mockResolvedValueOnce(
        jsonResponse({ detail: "A client named 'Acme Legal LLP' already exists." }, 409),
      )

    render(<ClientsPage />)

    await screen.findByText(/No clients yet/i)

    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/^Name/i), 'Acme Legal LLP')
    await user.click(screen.getByRole('button', { name: /create client/i }))

    expect(
      await screen.findByText(/already exists/i),
    ).toBeInTheDocument()
  })
})
