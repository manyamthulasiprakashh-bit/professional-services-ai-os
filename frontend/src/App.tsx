import { useState } from 'react'
import './App.css'
import AssistantPage from './components/AssistantPage'
import ClientsPage from './components/ClientsPage'
import DocumentsPage from './components/DocumentsPage'
import Header from './components/Header'
import LoginPage from './components/LoginPage'
import OverviewSection from './components/OverviewSection'
import Sidebar, { type Page } from './components/Sidebar'
import TasksPage from './components/TasksPage'
import { useAuth } from './auth/context'

export default function App() {
  const { status } = useAuth()
  const [page, setPage] = useState<Page>('overview')

  if (status === 'loading') {
    return (
      <div className="auth-gate" role="status">
        <span className="status__spinner" aria-hidden="true" /> Checking your
        session…
      </div>
    )
  }

  if (status === 'unauthenticated') {
    return <LoginPage />
  }

  return (
    <div className="layout">
      <Sidebar currentPage={page} onNavigate={setPage} />
      <main className="layout__main">
        <Header />
        {page === 'overview' && <OverviewSection />}
        {page === 'clients' && <ClientsPage />}
        {page === 'tasks' && <TasksPage />}
        {page === 'documents' && <DocumentsPage />}
        {page === 'assistant' && <AssistantPage />}
        <footer className="layout__footer">
          Independent prototype. Not affiliated with any external company.
        </footer>
      </main>
    </div>
  )
}
