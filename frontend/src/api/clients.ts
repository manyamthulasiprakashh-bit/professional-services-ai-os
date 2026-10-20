import { apiGet, apiPost } from './http'
import type { Client, ClientCreateInput } from '../types/business'

export function fetchClients(signal?: AbortSignal): Promise<Client[]> {
  return apiGet<Client[]>('/clients', signal)
}

export function createClient(input: ClientCreateInput): Promise<Client> {
  return apiPost<Client>('/clients', input)
}
