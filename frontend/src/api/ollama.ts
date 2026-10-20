import { apiGet } from './http'
import type { OllamaHealth } from '../types/documents'

export function fetchOllamaHealth(signal?: AbortSignal): Promise<OllamaHealth> {
  return apiGet<OllamaHealth>('/ollama/health', signal)
}
