import { useState } from 'react'
import { useAuth } from '../auth/context'

export default function Header() {
  const { user, logout } = useAuth()
  const [loggingOut, setLoggingOut] = useState(false)

  const onLogout = async () => {
    setLoggingOut(true)
    try {
      await logout()
    } finally {
      setLoggingOut(false)
    }
  }

  return (
    <header className="header">
      <div className="header__top">
        <div>
          <h1 className="header__title">Professional Services AI OS</h1>
          <p className="header__subtitle">
            Business operations platform prototype — data, documents, workflows
            and AI assistance.
          </p>
        </div>
        {user && (
          <div className="header__session">
            <span className="header__user" data-testid="current-user">
              Signed in as <strong>{user.username}</strong>
            </span>
            <button
              type="button"
              className="button button--secondary button--small"
              onClick={onLogout}
              disabled={loggingOut}
            >
              {loggingOut ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        )}
      </div>
    </header>
  )
}
