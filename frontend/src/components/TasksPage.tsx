import { useEffect, useState } from 'react'
import { ApiError } from '../api/http'
import { fetchMatters } from '../api/matters'
import { fetchTasks } from '../api/tasks'
import type { Matter, Task, TaskPriority, TaskStatus } from '../types/business'

type ListState =
  | { kind: 'loading' }
  | { kind: 'loaded'; tasks: Task[]; matters: Matter[] }
  | { kind: 'error'; message: string }

const STATUS_LABELS: Record<TaskStatus, string> = {
  todo: 'To do',
  in_progress: 'In progress',
  done: 'Done',
}

const PRIORITY_LABELS: Record<TaskPriority, string> = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
}

export default function TasksPage() {
  const [list, setList] = useState<ListState>({ kind: 'loading' })
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setList({ kind: 'loading' })
    Promise.all([fetchTasks(controller.signal), fetchMatters(controller.signal)])
      .then(([tasks, matters]) => setList({ kind: 'loaded', tasks, matters }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setList({
          kind: 'error',
          message:
            error instanceof ApiError
              ? error.message
              : 'Unexpected error while loading tasks.',
        })
      })
    return () => controller.abort()
  }, [reloadKey])

  const matterTitle = (matters: Matter[], matterId: number) =>
    matters.find((m) => m.id === matterId)?.title ?? `Matter #${matterId}`

  return (
    <section className="page" aria-labelledby="tasks-heading">
      <h2 id="tasks-heading">Tasks</h2>

      {list.kind === 'loading' && (
        <p className="page__message" role="status">
          Loading tasks…
        </p>
      )}

      {list.kind === 'error' && (
        <div className="page__error" role="alert">
          <strong>Could not load tasks</strong>
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

      {list.kind === 'loaded' && list.tasks.length === 0 && (
        <p className="page__message" role="status">
          No tasks yet. Tasks appear here once they are created against a matter.
        </p>
      )}

      {list.kind === 'loaded' && list.tasks.length > 0 && (
        <table className="table">
          <caption className="visually-hidden">Tasks</caption>
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">Title</th>
              <th scope="col">Matter</th>
              <th scope="col">Status</th>
              <th scope="col">Priority</th>
            </tr>
          </thead>
          <tbody>
            {list.tasks.map((task) => (
              <tr key={task.id}>
                <td>{task.id}</td>
                <td>{task.title}</td>
                <td>{matterTitle(list.matters, task.matter_id)}</td>
                <td>
                  <span className={`pill pill--${task.status}`}>
                    {STATUS_LABELS[task.status]}
                  </span>
                </td>
                <td>
                  <span className={`pill pill--priority-${task.priority}`}>
                    {PRIORITY_LABELS[task.priority]}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
