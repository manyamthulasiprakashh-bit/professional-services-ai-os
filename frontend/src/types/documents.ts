export interface DocumentRecord {
  id: number
  matter_id: number
  original_filename: string
  size_bytes: number
  sha256: string
  status: 'uploaded' | 'extracted' | 'extraction_failed'
  extraction_error: string | null
  created_at: string
}

/** Machine-extracted verbatim text — source facts, never model-generated. */
export interface SourceExcerpt {
  page_number: number | null
  excerpt: string
}

export interface DocumentSummary {
  document_id: number
  document_title: string
  source_excerpts: SourceExcerpt[]
  source_note: string
  summary: string
  key_points: string[]
  model: string
  machine_generated: boolean
  review_required: boolean
  generated_at: string
}

export interface OllamaHealth {
  available: boolean
  models: string[]
  summary_model: string
}
