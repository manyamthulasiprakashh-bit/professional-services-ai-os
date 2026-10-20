export type Page = 'overview' | 'clients' | 'tasks' | 'documents' | 'assistant'

interface SidebarProps {
  currentPage: Page
  onNavigate: (page: Page) => void
}

const NAV_ITEMS: { page: Page; label: string }[] = [
  { page: 'overview', label: 'Overview' },
  { page: 'clients', label: 'Clients' },
  { page: 'tasks', label: 'Tasks' },
  { page: 'documents', label: 'Documents' },
  { page: 'assistant', label: 'AI Assistant' },
]

const SOON_ITEMS: { label: string }[] = [
  { label: 'Workflows' },
]

export default function Sidebar({ currentPage, onNavigate }: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="Primary navigation">
      <div className="sidebar__brand">
        <span className="sidebar__mark" aria-hidden="true">
          PS
        </span>
        <span className="sidebar__brand-text">
          Professional Services
          <br />
          AI OS
        </span>
      </div>

      <nav>
        <ul className="sidebar__nav">
          {NAV_ITEMS.map(({ page, label }) => (
            <li key={page}>
              <button
                type="button"
                className={
                  currentPage === page
                    ? 'sidebar__link sidebar__link--current'
                    : 'sidebar__link'
                }
                aria-current={currentPage === page ? 'page' : undefined}
                onClick={() => onNavigate(page)}
              >
                {label}
              </button>
            </li>
          ))}
          {SOON_ITEMS.map(({ label }) => (
            <li key={label}>
              <span
                className="sidebar__link sidebar__link--disabled"
                aria-disabled="true"
                title="Not available in this milestone"
              >
                {label}
                <span className="sidebar__soon">Soon</span>
              </span>
            </li>
          ))}
        </ul>
      </nav>

      <p className="sidebar__footer">Prototype · Milestone 3</p>
    </aside>
  )
}
