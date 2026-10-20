import { useState, type FormEvent } from 'react'
import { ApiError } from '../api/http'
import { useAuth } from '../auth/context'

const GENERIC_FAILURE = 'Invalid username or password.'

export default function LoginPage() {
  const { login, restoreError } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    // Never log credentials.
    setError(null)
    setSubmitting(true)
    try {
      await login(username, password)
      // On success the provider flips to the authenticated view; clear fields.
      setPassword('')
    } catch (caught) {
      // Generic, safe message. Do not echo raw backend detail for auth errors.
      if (caught instanceof ApiError && caught.status === 401) {
        setError(GENERIC_FAILURE)
      } else if (caught instanceof ApiError) {
        setError('Sign-in could not be completed. Please try again.')
      } else {
        setError('Sign-in could not be completed. Please try again.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  const disabled = submitting || !username.trim() || !password

  return (
    <div className="login">
      <div className="login__brand" aria-hidden="true">
        PS
      </div>
      <h1 className="login__title">Professional Services AI OS</h1>
      <p className="login__subtitle">Sign in to continue.</p>

      {restoreError && (
        <p className="login__notice" role="alert">
          {restoreError}
        </p>
      )}

      <form
        className="login__form"
        onSubmit={onSubmit}
        aria-label="Sign in"
        noValidate
      >
        <label className="form__field">
          <span>Username</span>
          <input
            type="text"
            name="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={submitting}
            required
          />
        </label>

        <label className="form__field">
          <span>Password</span>
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting}
            required
          />
        </label>

        {error && (
          <p className="form__error" role="alert">
            {error}
          </p>
        )}

        <button className="button" type="submit" disabled={disabled}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>

      <p className="login__footer">
        Prototype · local access only. Contact your administrator if you do not
        have credentials.
      </p>
    </div>
  )
}
