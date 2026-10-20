import { apiGet } from './http'
import type { Task } from '../types/business'

export function fetchTasks(signal?: AbortSignal): Promise<Task[]> {
  return apiGet<Task[]>('/tasks', signal)
}
