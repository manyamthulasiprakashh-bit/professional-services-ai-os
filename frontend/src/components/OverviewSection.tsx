import SystemStatusPanel from './SystemStatusPanel'

const MILESTONES = [
  {
    title: 'Phase 1 · Backend foundation',
    body: 'FastAPI service with health and discovery endpoints, covered by automated tests.',
    state: 'Done',
  },
  {
    title: 'Phase 2 · Dashboard',
    body: 'This dashboard with a live backend health indicator.',
    state: 'Done',
  },
  {
    title: 'Later milestones',
    body: 'Documents, workflows and AI assistance are not built yet.',
    state: 'Planned',
  },
]

export default function OverviewSection() {
  return (
    <section className="overview" aria-labelledby="overview-heading">
      <div className="overview__heading-row">
        <h2 id="overview-heading">Overview</h2>
        <SystemStatusPanel />
      </div>

      <div className="overview__cards">
        {MILESTONES.map(({ title, body, state }) => (
          <article className="card" key={title}>
            <span
              className={
                state === 'Done' ? 'badge badge--done' : 'badge badge--planned'
              }
            >
              {state}
            </span>
            <h3>{title}</h3>
            <p>{body}</p>
          </article>
        ))}
      </div>
    </section>
  )
}
