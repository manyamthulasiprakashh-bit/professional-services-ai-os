import { apiGet } from './http'
import type { Matter } from '../types/business'

export function fetchMatters(signal?: AbortSignal): Promise<Matter[]> {
  return apiGet<Matter[]>('/matters', signal)
}
