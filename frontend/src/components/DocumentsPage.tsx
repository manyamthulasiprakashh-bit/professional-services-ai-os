import { useCallback, useEffect, useState } from 'react'
import { fetchDocuments, summarizeDocument, uploadDocument } from '../api/documents'
import { ApiError } from '../api/http'
import { fetchMatters } from '../api/matters'
import { fetchOllamaHealth } from '../api/ollama'
import type { Matter } from '../types/business'
import type { DocumentRecord, DocumentSummary, OllamaHealth } from '../types/documents'

const MAX_UPLOAD_BYTES = 5 * 1024 * 1024 // mirrors the backend default

type ListState =
  | { kind: 'loading' }
  | { kind: 'loaded'; documents: DocumentRecord[]; matters: Matter[] }
  | { kind: 'error'; message: string }

const STATUS_LABELS: Record<DocumentRecord['status'], string> = {
  uploaded: 'Uploaded',
  extracted: 'Text extracted',
  extraction_failed: 'Extraction failed',
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function DocumentsPage() {
  const [list, setList] = useState<ListState>({ kind: 'loading' })
  const [reloadKey, setReloadKey] = useState(0)
  const [ollama, setOllama] = useState<OllamaHealth | null>(null)

  const [matterId, setMatterId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [formSuccess, setFormSuccess] = useState<string | null>(null)

  const [summarizingId, setSummarizingId] = useState<number | null>(null)
  const [summary, setSummary] = useState<DocumentSummary | null>(null)
  const [summaryError, setSummaryError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setList({ kind: 'loading' })
    Promise.all([fetchDocuments(undefined, controller.signal), fetchMatters(controller.signal)])
      .then(([documents, matters]) => setList({ kind: 'loaded', documents, matters }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setList({
          kind: 'error',
          message:
            error instanceof ApiError
              ? error.message
              : 'Unexpected error while loading documents.',
        })
      })
    return () => controller.abort()
  }, [reloadKey])

  useEffect(() => {
    const controller = new AbortController()
    fetchOllamaHealth(controller.signal)
      .then(setOllama)
      .catch(() => setOllama(null))
    return () => controller.abort()
  }, [])

  const onSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault()
      setFormError(null)
      setFormSuccess(null)
      if (!file || !matterId) return
      if (file.size > MAX_UPLOAD_BYTES) {
        setFormError(`File is larger than the ${formatSize(MAX_UPLOAD_BYTES)} limit.`)
        return
      }
      setUploading(true)
      try {
        const created = await uploadDocument(Number(matterId), file)
        setFormSuccess(
          created.status === 'extracted'
            ? `"${created.original_filename}" uploaded and text extracted.`
            : `"${created.original_filename}" uploaded, but text extraction failed — see status below.`,
        )
        setFile(null)
        const input = document.querySelector<HTMLInputElement>('input[type="file"]')
        if (input) input.value = ''
        setReloadKey((key) => key + 1)
      } catch (error) {
        setFormError(
          error instanceof ApiError
            ? error.message
            : 'Unexpected error while uploading the document.',
        )
      } finally {
        setUploading(false)
      }
    },
    [file, matterId],
  )

  const onSummarize = useCallback(async (documentId: number) => {
    setSummaryError(null)
    setSummary(null)
    setSummarizingId(documentId)
    try {
      setSummary(await summarizeDocument(documentId))
    } catch (error) {
      setSummaryError(
        error instanceof ApiError
          ? error.message
          : 'Unexpected error while summarizing the document.',
      )
    } finally {
      setSummarizingId(null)
    }
  }, [])

  return (
    <section className="page" aria-labelledby="documents-heading">
      <h2 id="documents-heading">Documents</h2>

      <form className="card form" onSubmit={onSubmit} aria-label="Upload document">
        <h3>Upload document</h3>
        <label className="form__field">
          <span>Matter</span>
          <select
            value={matterId}
            onChange={(e) => setMatterId(e.target.value)}
            required
          >
            <option value="">Select a matter…</option>
            {list.kind === 'loaded' &&
              list.matters.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.title}
                </option>
              ))}
          </select>
        </label>
        <label className="form__field">
          <span>File (PDF or plain text, max {formatSize(MAX_UPLOAD_BYTES)})</span>
          <input
            type="file"
            accept=".pdf,.txt,application/pdf,text/plain"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            required
          />
        </label>
        {formError && (
          <p className="form__error" role="alert">
            {formError}
          </p>
        )}
        {formSuccess && (
          <p className="form__success" role="status">
            {formSuccess}
          </p>
        )}
        <button
          className="button"
          type="submit"
          disabled={uploading || !file || !matterId}
        >
          {uploading ? 'Uploading…' : 'Upload'}
        </button>
      </form>

      {ollama && !ollama.available && (
        <p className="page__message" role="status">
          The local summarization service (Ollama) is not reachable, so summaries
          are disabled. Everything else works normally.
        </p>
      )}

      {list.kind === 'loading' && (
        <p className="page__message" role="status">
          Loading documents…
        </p>
      )}

      {list.kind === 'error' && (
        <div className="page__error" role="alert">
          <strong>Could not load documents</strong>
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

      {list.kind === 'loaded' && list.documents.length === 0 && (
        <p className="page__message" role="status">
          No documents yet. Upload one against a matter using the form above.
        </p>
      )}

      {list.kind === 'loaded' && list.documents.length > 0 && (
        <table className="table">
          <caption className="visually-hidden">Documents</caption>
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">Filename</th>
              <th scope="col">Matter</th>
              <th scope="col">Size</th>
              <th scope="col">Status</th>
              <th scope="col">Created</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {list.documents.map((doc) => (
              <tr key={doc.id}>
                <td>{doc.id}</td>
                <td>
                  {doc.original_filename}
                  {doc.extraction_error && (
                    <div className="doc__error-detail">{doc.extraction_error}</div>
                  )}
                </td>
                <td>
                  {list.matters.find((m) => m.id === doc.matter_id)?.title ??
                    `Matter #${doc.matter_id}`}
                </td>
                <td>{formatSize(doc.size_bytes)}</td>
                <td>
                  <span className={`pill pill--doc-${doc.status}`}>
                    {STATUS_LABELS[doc.status]}
                  </span>
                </td>
                <td>{new Date(doc.created_at).toLocaleDateString()}</td>
                <td>
                  <button
                    type="button"
                    className="button button--small"
                    disabled={
                      doc.status !== 'extracted' ||
                      summarizingId !== null ||
                      ollama?.available === false
                    }
                    onClick={() => onSummarize(doc.id)}
                    title={
                      doc.status !== 'extracted'
                        ? 'Text must be extracted first'
                        : ollama?.available === false
                          ? 'Summarization service unavailable'
                          : 'Summarize with the local model'
                    }
                  >
                    {summarizingId === doc.id ? 'Summarizing…' : 'Summarize'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {summaryError && (
        <div className="page__error" role="alert" style={{ marginTop: '1rem' }}>
          <strong>Summarization unavailable</strong>
          <p>{summaryError}</p>
        </div>
      )}

      {summary && (
        <section className="card summary" aria-labelledby="summary-heading">
          <h3 id="summary-heading">
            Summary for “{summary.document_title}”
            <span className="pill pill--machine" style={{ marginLeft: '0.5rem' }}>
              Machine-generated — review required
            </span>
          </h3>

          <div className="summary__section summary__section--facts">
            <h4>Source excerpts (extracted from the document)</h4>
            {summary.source_excerpts.map((excerpt, index) => (
              <blockquote key={index} className="summary__excerpt">
                {excerpt.page_number !== null && (
                  <span className="summary__page">Page {excerpt.page_number}</span>
                )}
                <p>{excerpt.excerpt}</p>
              </blockquote>
            ))}
            <p className="summary__note">{summary.source_note}</p>
          </div>

          <div className="summary__section summary__section--model">
            <h4>Model interpretation ({summary.model})</h4>
            <p>{summary.summary}</p>
            {summary.key_points.length > 0 && (
              <ul>
                {summary.key_points.map((point, index) => (
                  <li key={index}>{point}</li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}
    </section>
  )
}
