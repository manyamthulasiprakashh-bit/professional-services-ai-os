export interface Client {
  id: number
  name: string
  email: string | null
  created_at: string
}

export interface ClientCreateInput {
  name: string
  email?: string
}

export type MatterStatus = 'open' | 'closed'

export interface Matter {
  id: number
  client_id: number
  title: string
  status: MatterStatus
  created_at: string
}

export type TaskStatus = 'todo' | 'in_progress' | 'done'
export type TaskPriority = 'low' | 'medium' | 'high'

export interface Task {
  id: number
  matter_id: number
  title: string
  status: TaskStatus
  priority: TaskPriority
  created_at: string
}

export interface TaskCreateInput {
  matter_id: number
  title: string
  status?: TaskStatus
  priority?: TaskPriority
}
