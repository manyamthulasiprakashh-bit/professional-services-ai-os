import { apiGet, apiPost, apiPostMultipart } from './http'
import type { DocumentRecord, DocumentSummary } from '../types/documents'

export function fetchDocuments(matterId?: number, signal?: AbortSignal): Promise<DocumentRecord[]> {
  const query = matterId ? `?matter_id=${matterId}` : ''
  return apiGet<DocumentRecord[]>(`/documents${query}`, signal)
}

export function uploadDocument(
  matterId: number,
  file: File,
): Promise<DocumentRecord> {
  const form = new FormData()
  form.append('matter_id', String(matterId))
  form.append('file', file)
  return apiPostMultipart<DocumentRecord>('/documents', form)
}

export function summarizeDocument(id: number): Promise<DocumentSummary> {
  return apiPost<DocumentSummary>(`/documents/${id}/summarize`, {})
}
