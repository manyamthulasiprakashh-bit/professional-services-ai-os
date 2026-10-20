import { useCallback, useEffect, useState } from 'react'
import { createClient, fetchClients } from '../api/clients'
import { ApiError } from '../api/http'
import type { Client } from '../types/business'

type ListState =
  | { kind: 'loading' }
  | { kind: 'loaded'; clients: Client[] }
  | { kind: 'error'; message: string }

export default function ClientsPage() {
  const [list, setList] = useState<ListState>({ kind: 'loading' })
  const [reloadKey, setReloadKey] = useState(0)

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [formSuccess, setFormSuccess] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setList({ kind: 'loading' })
    fetchClients(controller.signal)
      .then((clients) => setList({ kind: 'loaded', clients }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setList({
          kind: 'error',
          message:
            error instanceof ApiError
              ? error.message
              : 'Unexpected error while loading clients.',
        })
      })
    return () => controller.abort()
  }, [reloadKey])

  const onSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault()
      setFormError(null)
      setFormSuccess(null)
      setSubmitting(true)
      try {
        const created = await createClient({
          name: name.trim(),
          email: email.trim() || undefined,
        })
        setFormSuccess(`Client "${created.name}" created.`)
        setName('')
        setEmail('')
        setReloadKey((key) => key + 1)
      } catch (error) {
        setFormError(
          error instanceof ApiError
            ? error.message
            : 'Unexpected error while creating the client.',
        )
      } finally {
        setSubmitting(false)
      }
    },
    [name, email],
  )

  return (
    <section className="page" aria-labelledby="clients-heading">
      <h2 id="clients-heading">Clients</h2>

      <form className="card form" onSubmit={onSubmit} aria-label="Create client">
        <h3>Create client</h3>
        <label className="form__field">
          <span>Name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Acme Legal LLP"
            required
            maxLength={120}
          />
        </label>
        <label className="form__field">
          <span>Email (optional)</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="ops@example.test"
            maxLength={254}
          />
        </label>
        {formError && (
          <p className="form__error" role="alert">
            {formError}
          </p>
        )}
        {formSuccess && (
          <p className="form__success" role="status">
            {formSuccess}
          </p>
        )}
        <button className="button" type="submit" disabled={submitting || !name.trim()}>
          {submitting ? 'Creating…' : 'Create client'}
        </button>
      </form>

      {list.kind === 'loading' && (
        <p className="page__message" role="status">
          Loading clients…
        </p>
      )}

      {list.kind === 'error' && (
        <div className="page__error" role="alert">
          <strong>Could not load clients</strong>
          <p>{list.message}</p>
          <button
            type="button"
            className="button"
            onClick={() => setReloadKey((key) => key + 1)}
          >
            Retry
          </button>
        </div>
      )}

      {list.kind === 'loaded' && list.clients.length === 0 && (
        <p className="page__message" role="status">
          No clients yet. Create one with the form above.
        </p>
      )}

      {list.kind === 'loaded' && list.clients.length > 0 && (
        <table className="table">
          <caption className="visually-hidden">Clients</caption>
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">Name</th>
              <th scope="col">Email</th>
              <th scope="col">Created</th>
            </tr>
          </thead>
          <tbody>
            {list.clients.map((client) => (
              <tr key={client.id}>
                <td>{client.id}</td>
                <td>{client.name}</td>
                <td>{client.email ?? '—'}</td>
                <td>{new Date(client.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
