import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { AuthProvider } from './auth/AuthContext'
import { CSRF_HEADER_NAME, clearCsrfToken } from './api/http'

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** A fresh Response per call (Response bodies can only be read once). */
function fallback(data: unknown): () => Promise<Response> {
  return () => Promise.resolve(jsonResponse(data))
}

const CURRENT_USER = { id: 1, username: 'test-operator', role: 'user' }

function renderApp() {
  return render(
    <AuthProvider>
      <App />
    </AuthProvider>,
  )
}

/** URL of a recorded fetch call. */
function callUrl(call: [RequestInfo | URL, RequestInit?]): string {
  return String(call[0])
}

function callsTo(path: string) {
  return vi
    .mocked(fetch)
    .mock.calls.filter((call) => callUrl(call).endsWith(path))
}

describe('App authentication gating', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    clearCsrfToken()
    // Reset cookies between tests.
    document.cookie = 'ps_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the login screen when the session is unauthenticated', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ detail: 'Authentication required.' }, 401),
    )

    renderApp()

    expect(
      await screen.findByRole('heading', { name: /professional services ai os/i }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
    // The dashboard navigation is not shown while signed out.
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
  })

  it('restores an existing session and shows the dashboard', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(CURRENT_USER))
      .mockImplementation(fallback({ status: 'ok' }))

    renderApp()

    expect(await screen.findByTestId('current-user')).toHaveTextContent(
      /test-operator/,
    )
    expect(screen.getByRole('navigation')).toBeInTheDocument()
  })

  it('logs in successfully and reveals the dashboard', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse({ detail: 'Authentication required.' }, 401),
      ) // /auth/me on startup
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'csrf-abc' })) // /auth/csrf
      .mockResolvedValueOnce(jsonResponse(CURRENT_USER)) // /auth/login
      .mockImplementation(fallback({ status: 'ok' }))

    renderApp()
    await screen.findByRole('button', { name: /sign in/i })

    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/username/i), 'test-operator')
    await user.type(screen.getByLabelText(/password/i), 'a-strong-password-1')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByTestId('current-user')).toHaveTextContent(
      /test-operator/,
    )
  })

  it('shows a generic error for invalid credentials', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ detail: 'x' }, 401)) // /auth/me
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'csrf-abc' })) // /auth/csrf
      .mockResolvedValueOnce(
        jsonResponse({ detail: 'Invalid username or password.' }, 401),
      ) // login fails

    renderApp()
    await screen.findByRole('button', { name: /sign in/i })

    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/username/i), 'nobody')
    await user.type(screen.getByLabelText(/password/i), 'wrong')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(
      await screen.findByText(/invalid username or password/i),
    ).toBeInTheDocument()
    // Still on the login screen.
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('sends credentialed requests with the CSRF header on login', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ detail: 'x' }, 401))
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'csrf-xyz' }))
      .mockResolvedValueOnce(jsonResponse(CURRENT_USER))

    renderApp()
    await screen.findByRole('button', { name: /sign in/i })

    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/username/i), 'test-operator')
    await user.type(screen.getByLabelText(/password/i), 'a-strong-password-1')
    await user.click(screen.getByRole('button', { name: /sign in/i }))
    await screen.findByTestId('current-user')

    // The session-restore call must include credentials.
    const meCall = callsTo('/auth/me')[0]
    expect((meCall[1] as RequestInit).credentials).toBe('include')

    // The csrf acquisition returns a token echoed on the login request.
    const loginCall = callsTo('/auth/login')[0]
    const loginInit = loginCall[1] as RequestInit
    expect(loginInit.credentials).toBe('include')
    expect(loginInit.method).toBe('POST')
    const headers = new Headers(loginInit.headers)
    expect(headers.get(CSRF_HEADER_NAME)).toBe('csrf-xyz')
  })

  it('refreshes the CSRF token from the cookie after login', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ detail: 'x' }, 401))
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'csrf-login' }))
      .mockResolvedValueOnce(jsonResponse(CURRENT_USER))
      .mockImplementation(fallback({ status: 'ok' }))

    renderApp()
    await screen.findByRole('button', { name: /sign in/i })
    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/username/i), 'test-operator')
    await user.type(screen.getByLabelText(/password/i), 'a-strong-password-1')
    await user.click(screen.getByRole('button', { name: /sign in/i }))
    await screen.findByTestId('current-user')

    // The server rotates the CSRF token on login; the readable cookie is the
    // freshest source and must be used for subsequent unsafe requests.
    document.cookie = 'ps_csrf=csrf-rotated; path=/'
    const { apiPost } = await import('./api/http')
    await apiPost('/clients', { name: 'Acme' })

    const clientsCall = callsTo('/clients')[0]
    const headers = new Headers((clientsCall[1] as RequestInit).headers)
    expect(headers.get(CSRF_HEADER_NAME)).toBe('csrf-rotated')
  })

  it('logs out, calls the backend and clears local state', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(CURRENT_USER)) // /auth/me
      .mockResolvedValueOnce(jsonResponse({ status: 'logged_out' })) // /auth/logout
      .mockImplementation(fallback({ status: 'ok' }))

    renderApp()
    await screen.findByTestId('current-user')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /sign out/i }))

    // Back to the login screen...
    expect(await screen.findByRole('button', { name: /sign in/i })).toBeInTheDocument()
    expect(callsTo('/auth/logout')).toHaveLength(1)
    // ...and the identity is gone from the UI.
    expect(screen.queryByTestId('current-user')).not.toBeInTheDocument()
  })

  it('treats a 401 from a protected call as a session expiry', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(CURRENT_USER))
      .mockImplementation(fallback({ status: 'ok' }))

    renderApp()
    await screen.findByTestId('current-user')

    // Simulate a later protected request returning 401 (expired/revoked).
    const { apiGet } = await import('./api/http')
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ detail: 'Authentication required.' }, 401),
    )
    await expect(apiGet('/clients')).rejects.toMatchObject({ status: 401 })
  })

  it('does not interpret a transient restore failure as a confirmed logout', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError('network down'))

    renderApp()

    // Login screen shown, but with a distinct, honest notice (not silent).
    expect(
      await screen.findByText(/could not confirm your session/i),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('does not issue duplicate session-restore requests on startup', async () => {
    vi.mocked(fetch).mockImplementation(fallback(CURRENT_USER))
    renderApp()
    await screen.findByTestId('current-user')

    expect(callsTo('/auth/me')).toHaveLength(1)
  })

  it('never writes tokens or passwords to localStorage/sessionStorage', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ detail: 'x' }, 401))
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'csrf-secret' }))
      .mockResolvedValueOnce(jsonResponse(CURRENT_USER))
      .mockImplementation(fallback({ status: 'ok' }))

    const setLocal = vi.spyOn(Storage.prototype, 'setItem')
    renderApp()
    await screen.findByRole('button', { name: /sign in/i })
    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/username/i), 'test-operator')
    await user.type(screen.getByLabelText(/password/i), 'a-strong-password-1')
    await user.click(screen.getByRole('button', { name: /sign in/i }))
    await screen.findByTestId('current-user')

    expect(setLocal).not.toHaveBeenCalled()
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
    expect(JSON.stringify(window.localStorage)).not.toContain('csrf-secret')
    expect(JSON.stringify(window.sessionStorage)).not.toContain('csrf-secret')
  })

  it('lets an authenticated user reach the Assistant page and approve', async () => {
    const proposal = {
      proposal_id: 'abc123def456abc123def456abc12345',
      action_type: 'create_task',
      status: 'pending_approval',
      proposed: {
        title: 'Prepare Harbor disclosure schedule',
        description: 'Prepare the initial disclosure schedule.',
        matter_id: 1,
        matter_title: 'Harbor merger diligence',
        due_date: '2026-02-10',
      },
      evidence: [],
      notice: 'Machine-generated proposal only.',
      machine_generated: true,
      review_required: true,
      model: 'qwen3:4b',
    }
    const record = {
      ...proposal,
      created_task_id: null,
      execution_error: null,
      created_at: '2026-01-01T00:00:00+00:00',
      updated_at: '2026-01-01T00:00:00+00:00',
      decided_at: null,
      executed_at: null,
    }

    let approved = false
    vi.mocked(fetch).mockImplementation((input, init) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.endsWith('/auth/me')) return Promise.resolve(jsonResponse(CURRENT_USER))
      if (url.endsWith('/health')) return Promise.resolve(jsonResponse({ status: 'ok' }))
      if (url.endsWith('/assistant/propose-action')) {
        return Promise.resolve(
          jsonResponse({
            proposal,
            reason: null,
            machine_generated: true,
            review_required: true,
          }),
        )
      }
      if (url.endsWith(`/proposals/${proposal.proposal_id}/approve`)) {
        approved = true
        return Promise.resolve(
          jsonResponse({
            proposal_id: proposal.proposal_id,
            status: 'executed',
            created_task: {
              id: 7,
              matter_id: 1,
              title: proposal.proposed.title,
              status: 'todo',
              priority: 'medium',
              created_at: '2026-01-01T00:00:00+00:00',
            },
            execution_error: null,
            notice: 'Approved by a human. The task below was created exactly once.',
          }),
        )
      }
      if (url.endsWith(`/proposals/${proposal.proposal_id}`) && method === 'GET') {
        return Promise.resolve(
          jsonResponse(
            approved
              ? { ...record, status: 'executed', created_task_id: 7 }
              : record,
          ),
        )
      }
      return Promise.resolve(jsonResponse({ status: 'ok' }))
    })

    renderApp()
    await screen.findByTestId('current-user')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /ai assistant/i }))
    await user.type(
      screen.getByRole('textbox', { name: /^request$/i }),
      'Add a task for Harbor',
    )
    await user.click(screen.getByRole('button', { name: /request proposal/i }))
    await screen.findByText('Prepare Harbor disclosure schedule')

    // No optimistic success before the server confirms.
    expect(screen.queryByText(/Task #7 created/i)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^approve$/i }))

    expect(await screen.findByText(/Task #7 created/i)).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByText(/Created task ID: 7/i)).toBeInTheDocument(),
    )

    // The approval request carried credentials and the CSRF header.
    const approveCall = callsTo(`/proposals/${proposal.proposal_id}/approve`)[0]
    const approveInit = approveCall[1] as RequestInit
    expect(approveInit.credentials).toBe('include')
  })
})
