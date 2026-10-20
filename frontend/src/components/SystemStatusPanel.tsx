import { useCallback, useEffect, useState } from 'react'
import { fetchHealth, HealthRequestError } from '../api/health'
import { apiBaseUrl } from '../config'
import type { HealthResponse } from '../types/health'

type StatusState =
  | { kind: 'loading' }
  | { kind: 'success'; data: HealthResponse }
  | { kind: 'error'; message: string }

export default function SystemStatusPanel() {
  const [state, setState] = useState<StatusState>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)

  const load = useCallback((signal?: AbortSignal) => {
    setState({ kind: 'loading' })
    fetchHealth(apiBaseUrl, 8000, signal)
      .then((data) => setState({ kind: 'success', data }))
      .catch((error: unknown) => {
        if (signal?.aborted) return
        const message =
          error instanceof HealthRequestError
            ? error.message
            : 'Unexpected error while checking backend status.'
        setState({ kind: 'error', message })
      })
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load, attempt])

  return (
    <section className="status" aria-labelledby="status-heading">
      <div className="status__header">
        <h3 id="status-heading">System status</h3>
        <span className="status__endpoint">{apiBaseUrl}/health</span>
      </div>

      {state.kind === 'loading' && (
        <p className="status__message" role="status" aria-live="polite">
          <span className="status__spinner" aria-hidden="true" />
          Checking backend…
        </p>
      )}

      {state.kind === 'error' && (
        <div className="status__message status__message--error" role="alert">
          <strong>Backend unreachable</strong>
          <p>{state.message}</p>
          <button type="button" className="button" onClick={() => setAttempt((a) => a + 1)}>
            Retry
          </button>
        </div>
      )}

      {state.kind === 'success' && (
        <dl className="status__details" role="status" aria-live="polite">
          <div>
            <dt>Application</dt>
            <dd data-testid="health-application">{state.data.application}</dd>
          </div>
          <div>
            <dt>Version</dt>
            <dd data-testid="health-version">{state.data.version}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>
              <span
                className={
                  state.data.status === 'healthy'
                    ? 'badge badge--done'
                    : 'badge badge--planned'
                }
                data-testid="health-status"
              >
                {state.data.status}
              </span>
            </dd>
          </div>
        </dl>
      )}
    </section>
  )
}
