import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DocumentsPage from './DocumentsPage'

const MATTERS = [
  {
    id: 1,
    client_id: 1,
    title: 'Merger diligence',
    status: 'open',
    created_at: '2026-01-01T00:00:00+00:00',
  },
]

const HEALTH_DOWN = { available: false, models: [], summary_model: 'qwen3:4b' }
const HEALTH_UP = { available: true, models: ['qwen3:4b'], summary_model: 'qwen3:4b' }

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('DocumentsPage', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the empty state and disables summarization when Ollama is down', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse([])) // GET /documents
      .mockResolvedValueOnce(jsonResponse(MATTERS)) // GET /matters
      .mockResolvedValueOnce(jsonResponse(HEALTH_DOWN)) // GET /ollama/health

    render(<DocumentsPage />)

    expect(await screen.findByText(/No documents yet/i)).toBeInTheDocument()
    expect(
      await screen.findByText(/summarization service \(Ollama\) is not reachable/i),
    ).toBeInTheDocument()
  })

  it('lists documents with their extraction status', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse([
          {
            id: 1,
            matter_id: 1,
            original_filename: 'memo.txt',
            size_bytes: 12,
            sha256: 'abc',
            status: 'extracted',
            extraction_error: null,
            created_at: '2026-01-01T00:00:00+00:00',
          },
          {
            id: 2,
            matter_id: 1,
            original_filename: 'scan.pdf',
            size_bytes: 999,
            sha256: 'def',
            status: 'extraction_failed',
            extraction_error: 'No extractable text found; OCR is not enabled.',
            created_at: '2026-01-02T00:00:00+00:00',
          },
        ]),
      )
      .mockResolvedValueOnce(jsonResponse(MATTERS))
      .mockResolvedValueOnce(jsonResponse(HEALTH_UP))

    render(<DocumentsPage />)

    expect(await screen.findByText('memo.txt')).toBeInTheDocument()
    expect(screen.getByText('scan.pdf')).toBeInTheDocument()
    expect(screen.getByText('Text extracted')).toBeInTheDocument()
    expect(screen.getByText('Extraction failed')).toBeInTheDocument()
    expect(
      screen.getByText(/No extractable text found; OCR is not enabled/),
    ).toBeInTheDocument()
  })

  it('surfaces backend upload errors', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse(MATTERS))
      .mockResolvedValueOnce(jsonResponse(HEALTH_UP))
      .mockResolvedValueOnce(
        jsonResponse({ detail: 'An identical file has already been uploaded to this matter.' }, 409),
      )

    render(<DocumentsPage />)

    await screen.findByText(/No documents yet/i)

    fireEvent.change(screen.getByLabelText(/^Matter/i), { target: { value: '1' } })
    const input = screen.getByLabelText(/File \(/i) as HTMLInputElement
    fireEvent.change(input, {
      target: { files: [new File(['same bytes'], 'notes.txt', { type: 'text/plain' })] },
    })
    fireEvent.submit(input.closest('form')!)

    expect(
      await screen.findByText(/already been uploaded/i),
    ).toBeInTheDocument()
  })
})
