"""Document endpoints: upload, list, re-extract, summarize, index."""

import logging
import sqlite3

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from .. import documents_storage, embeddings, extraction, repository
from ..db import get_db
from ..llm import (
    MAX_EXCERPT_CHARS,
    Embedder,
    LLMConcurrencyError,
    LLMUnavailableError,
    Summarizer,
    get_embedder,
    get_summarizer,
)
from ..schemas import (
    Document,
    DocumentIndexResult,
    DocumentIndexStatus,
    DocumentSummary,
    SourceExcerpt,
)
from ..ratelimit_config import document_rate_limit
from ..security import require_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/documents", tags=["documents"], dependencies=[Depends(require_user)]
)

SOURCE_NOTE = (
    "Source excerpts below are machine-extracted verbatim text from the "
    "uploaded file. The summary and key points are model-generated "
    "interpretations and require human review."
)


def _document_model(row: sqlite3.Row) -> Document:
    return Document.model_validate(repository.row_to_dict(row))


def _try_index(conn: sqlite3.Connection, embedder: Embedder, document_id: int) -> None:
    """Best-effort indexing: failures never break upload/extraction.

    On failure the previous index (if any) is left untouched and the document
    stays retrievable by keyword. No document text is logged.
    """
    try:
        embeddings.upsert_chunk_embeddings(conn, embedder, document_id)
    except LLMUnavailableError:
        logger.info("document %d: embedding index update skipped (provider unavailable)", document_id)
    except Exception:  # defensive: indexing must never fail upload/extract
        logger.warning("document %d: embedding index update failed", document_id)


@router.get("", response_model=list[Document])
def list_documents(
    matter_id: int | None = Query(default=None, ge=1),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[Document]:
    """List document metadata, optionally filtered by matter."""
    return [
        _document_model(row)
        for row in repository.list_documents(conn, matter_id=matter_id)
    ]


@router.post("", response_model=Document, status_code=status.HTTP_201_CREATED)
def upload_document(
    matter_id: int = Form(..., ge=1),
    file: UploadFile = File(...),
    _limit: None = Depends(document_rate_limit),
    conn: sqlite3.Connection = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
) -> Document:
    """Upload a PDF or plain-text document against an existing matter.

    404 missing matter · 413 too large · 422 invalid type/empty/content ·
    409 duplicate content on the same matter. The stored file is removed
    again if the database write fails. Embedding-index updates are best-effort
    and never fail the upload.
    """
    try:
        repository.get_matter(conn, matter_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Matter {matter_id} not found.",
        ) from exc

    limit = documents_storage.max_upload_bytes()
    try:
        data = documents_storage.read_upload_limited(file, limit)
    except documents_storage.UploadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
        ) from exc

    try:
        extension = documents_storage.validate_content(file.filename or "", data)
    except documents_storage.InvalidDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    digest = documents_storage.sha256_of(data)
    if repository.find_document_by_sha256(conn, matter_id, digest):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An identical file has already been uploaded to this matter.",
        )

    stored_name, path = documents_storage.write_new_file(data, extension)

    try:
        try:
            chunks = extraction.extract_chunks(path, extension)
        except extraction.ExtractionError as exc:
            row = repository.create_document(
                conn,
                matter_id=matter_id,
                original_filename=file.filename or "",
                stored_name=stored_name,
                size_bytes=len(data),
                sha256=digest,
                status="extraction_failed",
                extraction_error=str(exc),
            )
        else:
            row = repository.create_document(
                conn,
                matter_id=matter_id,
                original_filename=file.filename or "",
                stored_name=stored_name,
                size_bytes=len(data),
                sha256=digest,
                status="extracted",
                chunks=chunks,
            )
    except Exception:
        documents_storage.delete_quietly(path)
        raise

    if row["status"] == "extracted":
        _try_index(conn, embedder, row["id"])

    return _document_model(row)


@router.post("/{document_id}/extract", response_model=Document)
def reextract_document(
    document_id: int,
    _limit: None = Depends(document_rate_limit),
    conn: sqlite3.Connection = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
) -> Document:
    """Re-run extraction for an existing document."""
    try:
        row = repository.get_document(conn, document_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found.",
        ) from exc

    document = _document_model(row)
    extension = (document.original_filename or "").lower()
    extension = ".pdf" if extension.endswith(".pdf") else ".txt"

    try:
        path = documents_storage.stored_path(row["stored_name"])
        chunks = extraction.extract_chunks(path, extension)
    except (extraction.ExtractionError, documents_storage.InvalidDocumentError) as exc:
        repository.replace_document_chunks(conn, document_id, [])
        embeddings.delete_embeddings_for_document(conn, document_id)
        updated = repository.set_document_extraction_status(
            conn, document_id, "extraction_failed", str(exc)
        )
        return _document_model(updated)

    repository.replace_document_chunks(conn, document_id, chunks)
    updated = repository.set_document_extraction_status(conn, document_id, "extracted")
    _try_index(conn, embedder, document_id)
    return _document_model(updated)


@router.get("/index", response_model=DocumentIndexStatus)
def index_status(
    conn: sqlite3.Connection = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
) -> DocumentIndexStatus:
    """Report embedding-index status for all documents (Step 5.3)."""
    _, _, dims = embeddings.index_stats(conn)
    indexed_chunks = conn.execute(
        "SELECT COUNT(*) AS n FROM chunk_embeddings"
    ).fetchone()["n"]
    indexed_documents = conn.execute(
        "SELECT COUNT(DISTINCT document_id) AS n FROM chunk_embeddings"
    ).fetchone()["n"]
    total_documents = conn.execute(
        "SELECT COUNT(*) AS n FROM documents WHERE status = 'extracted'"
    ).fetchone()["n"]
    dimension = next(iter(dims)) if len(dims) == 1 else None
    return DocumentIndexStatus(
        embed_model=embedder.embed_model,
        dimension=dimension,
        indexed_chunks=indexed_chunks,
        indexed_documents=indexed_documents,
        total_documents=total_documents,
        stale=embeddings.is_stale(conn, embedder),
    )


@router.post("/index", response_model=DocumentIndexResult)
def backfill_index(
    conn: sqlite3.Connection = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
) -> DocumentIndexResult:
    """Backfill/retry embeddings for all extracted documents (Step 5.3).

    Never raises on provider failure: returns honest per-document counts.
    Failed documents keep their previous index untouched and can be retried;
    keyword retrieval remains available throughout. Not run automatically at
    startup.
    """
    rows = conn.execute(
        "SELECT id FROM documents WHERE status = 'extracted' ORDER BY id"
    ).fetchall()

    result = DocumentIndexResult(
        embed_model=embedder.embed_model,
        dimension=None,
        documents=len(rows),
        indexed=0,
        skipped=0,
        failed=0,
    )
    for row in rows:
        try:
            outcome = embeddings.upsert_chunk_embeddings(conn, embedder, row["id"])
        except LLMUnavailableError as exc:
            result.failed += 1
            if str(exc) not in result.errors:
                result.errors.append(str(exc))
            continue
        except Exception:
            result.failed += 1
            if "Indexing failed for one or more documents." not in result.errors:
                result.errors.append("Indexing failed for one or more documents.")
            continue
        result.indexed += outcome.indexed
        result.skipped += outcome.skipped
        if outcome.dimension is not None:
            result.dimension = outcome.dimension

    return result


@router.post("/{document_id}/index", response_model=DocumentIndexResult)
def index_document(
    document_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
) -> DocumentIndexResult:
    """Index (or re-index) a single extracted document. 404 if unknown or not
    extracted; honest counts otherwise."""
    try:
        row = repository.get_document(conn, document_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found.",
        ) from exc

    result = DocumentIndexResult(
        embed_model=embedder.embed_model,
        dimension=None,
        documents=1,
        indexed=0,
        skipped=0,
        failed=0,
    )
    if row["status"] != "extracted":
        result.failed = 1
        result.errors.append("Document text has not been extracted successfully.")
        return result

    try:
        outcome = embeddings.upsert_chunk_embeddings(conn, embedder, document_id)
    except LLMUnavailableError as exc:
        result.failed = 1
        result.errors.append(str(exc))
        return result

    result.indexed = outcome.indexed
    result.skipped = outcome.skipped
    result.dimension = outcome.dimension
    return result


@router.post("/{document_id}/summarize", response_model=DocumentSummary)
def summarize_document(
    document_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    summarizer: Summarizer = Depends(get_summarizer),
) -> DocumentSummary:
    """Summarize an extracted document using the configured provider.

    409 if extraction has not succeeded; 503 if the provider is unavailable.
    """
    try:
        row = repository.get_document(conn, document_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found.",
        ) from exc

    if row["status"] != "extracted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Document text has not been extracted successfully; "
                "summarization is unavailable."
            ),
        )

    chunk_rows = repository.get_document_chunks(conn, document_id)
    chunks = [
        extraction.ExtractedChunk(
            page_number=r["page_number"], content=r["content"]
        )
        for r in chunk_rows
    ]

    excerpts = [
        SourceExcerpt(
            page_number=chunk.page_number,
            excerpt=chunk.content[:MAX_EXCERPT_CHARS],
        )
        for chunk in chunks[:5]
        if chunk.content.strip()
    ]

    try:
        output = summarizer.summarize(row["original_filename"], chunks)
    except LLMConcurrencyError as exc:
        # Generation gate saturated: deliberate, safe 429 with a retry hint.
        # The document and its chunks are untouched (read-only path).
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    except LLMUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return DocumentSummary(
        document_id=document_id,
        document_title=row["original_filename"],
        source_excerpts=excerpts,
        source_note=SOURCE_NOTE,
        summary=output.summary,
        key_points=output.key_points,
        model=output.model,
        machine_generated=True,
        review_required=True,
        generated_at=output.generated_at,
    )
