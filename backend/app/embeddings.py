"""Chunk-embedding persistence and bounded cosine ranking (Step 5.3).

Vectors are float32 BLOBs in SQLite (`array('f')` bytes). All provider calls
go through the `Embedder` protocol; all DB access is plain sqlite3 — no
FastAPI imports. Embeddings are staged and validated BEFORE any existing
index rows are deleted, so a failed re-index leaves the previous valid index
intact. Vectors from incompatible models or dimensions are never ranked
together: only rows matching the current `embed_model` (and the query
dimension) are scored.
"""

import math
import os
import sqlite3
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone

from .llm import Embedder, LLMUnavailableError

DEFAULT_EMBED_MAX_TEXT_CHARS = 2000  # per chunk text sent to the embedder
DEFAULT_EMBED_QUERY_CHARS = 2000  # question length bound
DEFAULT_EMBED_MAX_TEXTS_PER_REQUEST = 16  # batch size
DEFAULT_EMBED_MAX_VECTORS_SCORED = 2000  # candidates scored per query
DEFAULT_SEMANTIC_MAX_CHUNKS = 4  # semantic chunks merged into retrieval
DEFAULT_SEMANTIC_MIN_SIMILARITY = 0.25  # provisional; not a support guarantee


def _limit(name: str, default: int | float) -> int | float:
    override = os.environ.get(name)
    return type(default)(override) if override else default


def embed_max_text_chars() -> int:
    return int(_limit("EMBED_MAX_TEXT_CHARS", DEFAULT_EMBED_MAX_TEXT_CHARS))


def embed_query_chars() -> int:
    return int(_limit("EMBED_QUERY_CHARS", DEFAULT_EMBED_QUERY_CHARS))


def embed_max_texts_per_request() -> int:
    return int(_limit("EMBED_MAX_TEXTS_PER_REQUEST", DEFAULT_EMBED_MAX_TEXTS_PER_REQUEST))


def embed_max_vectors_scored() -> int:
    return int(_limit("EMBED_MAX_VECTORS_SCORED", DEFAULT_EMBED_MAX_VECTORS_SCORED))


def semantic_max_chunks() -> int:
    return int(_limit("SEMANTIC_MAX_CHUNKS", DEFAULT_SEMANTIC_MAX_CHUNKS))


def semantic_min_similarity() -> float:
    return float(_limit("SEMANTIC_MIN_SIMILARITY", DEFAULT_SEMANTIC_MIN_SIMILARITY))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class IndexResult:
    """Honest indexing counts for one document or a batch."""

    indexed: int = 0
    skipped: int = 0  # blank texts, nothing to embed
    failed: int = 0  # provider errors (no index rows were changed)
    embed_model: str = ""
    dimension: int | None = None


# --- Vector encoding ----------------------------------------------------------


def pack_vector(vector: list[float]) -> bytes:
    """Encode a float vector as float32 BLOB bytes."""
    return array("f", vector).tobytes()


def unpack_vector(blob: bytes, dimension: int) -> list[float] | None:
    """Decode a stored BLOB; return None on corruption or size mismatch."""
    if dimension <= 0 or len(blob) != dimension * 4:
        return None
    try:
        vector = array("f")
        vector.frombytes(blob)
        values = [float(value) for value in vector]
    except (ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return values


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity; returns -1.0 for zero-norm or mismatched inputs."""
    if len(a) != len(b) or not a:
        return -1.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return -1.0
    return dot / math.sqrt(norm_a * norm_b)


# --- Persistence --------------------------------------------------------------


def index_stats(conn: sqlite3.Connection) -> tuple[int, set[str], set[int]]:
    """Return (row count, distinct models, distinct dimensions) of the index."""
    row = conn.execute(
        "SELECT COUNT(*) AS n,"
        " COUNT(DISTINCT embed_model) AS models,"
        " COUNT(DISTINCT dimension) AS dims"
        " FROM chunk_embeddings"
    ).fetchone()
    models = {
        r["embed_model"]
        for r in conn.execute("SELECT DISTINCT embed_model FROM chunk_embeddings")
    }
    dims = {
        r["dimension"]
        for r in conn.execute("SELECT DISTINCT dimension FROM chunk_embeddings")
    }
    return row["n"], models, dims


def is_stale(conn: sqlite3.Connection, embedder: Embedder) -> bool:
    """True when stored vectors don't match the configured embedding model.

    An empty index is not stale — it is simply not yet populated. When the
    embedder has not produced a vector yet (dimension unknown), only the model
    name is compared.
    """
    _, models, dims = index_stats(conn)
    if not models:
        return False
    if embedder.embed_model not in models:
        return True
    if len(models) > 1 or len(dims) > 1:
        return True
    if embedder.dimension is not None and dims and dims != {embedder.dimension}:
        return True
    return False


def delete_embeddings_for_document(conn: sqlite3.Connection, document_id: int) -> int:
    """Remove all embeddings for a document (chunk ids may have changed)."""
    cur = conn.execute(
        "DELETE FROM chunk_embeddings WHERE document_id = ?", (document_id,)
    )
    return cur.rowcount


def upsert_chunk_embeddings(
    conn: sqlite3.Connection, embedder: Embedder, document_id: int
) -> IndexResult:
    """Embed a document's chunks and replace its existing index rows.

    Staged: every vector is embedded and validated first; only then are the
    document's old rows deleted and new rows written (in the ambient
    transaction). A provider failure raises LLMUnavailableError with the
    previous index untouched. Blank chunk texts are skipped.
    """
    rows = conn.execute(
        "SELECT id, content FROM document_chunks"
        " WHERE document_id = ? ORDER BY id",
        (document_id,),
    ).fetchall()
    result = IndexResult(embed_model=embedder.embed_model)

    candidates: list[tuple[int, str]] = []
    for row in rows:
        text = (row["content"] or "")[: embed_max_text_chars()].strip()
        if not text:
            result.skipped += 1
            continue
        candidates.append((row["id"], text))

    if not candidates:
        return result

    # Stage: embed everything before touching the index.
    vectors: list[list[float]] = []
    batch_size = embed_max_texts_per_request()
    for start in range(0, len(candidates), batch_size):
        batch = [text for _, text in candidates[start : start + batch_size]]
        vectors.extend(embedder.embed(batch))  # raises LLMUnavailableError

    dimension = len(vectors[0]) if vectors else None

    # Replace: delete old rows for this document, insert validated new rows.
    delete_embeddings_for_document(conn, document_id)
    for (chunk_id, _text), vector in zip(candidates, vectors):
        conn.execute(
            "INSERT INTO chunk_embeddings"
            " (chunk_id, document_id, embedding, dimension, embed_model, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                chunk_id,
                document_id,
                sqlite3.Binary(pack_vector(vector)),
                len(vector),
                embedder.embed_model,
                _utc_now(),
            ),
        )
        result.indexed += 1
    result.dimension = dimension
    return result


# --- Query-time ranking -------------------------------------------------------


def query_embedding(conn: sqlite3.Connection, embedder: Embedder, question: str):
    """Embed the question for semantic search; None when unavailable.

    Provider failures are swallowed here — the caller falls back to keyword
    retrieval rather than failing the request.
    """
    text = question[: embed_query_chars()].strip()
    if not text:
        return None
    try:
        vectors = embedder.embed([text])
    except LLMUnavailableError:
        return None
    return vectors[0] if vectors else None


@dataclass
class SemanticCandidate:
    chunk_id: int
    document_id: int
    original_filename: str
    page_number: int | None
    content: str
    similarity: float


def semantic_candidates(
    conn: sqlite3.Connection,
    query_vec: list[float],
    *,
    embed_model: str,
    limit: int | None = None,
    min_similarity: float | None = None,
) -> list[SemanticCandidate]:
    """Rank stored chunk vectors by cosine similarity to the query vector.

    Only rows whose `embed_model` and `dimension` match the query vector are
    scored — incompatible vectors are never mixed into one ranking. Malformed,
    corrupt or non-finite stored vectors are skipped safely. Bounded by
    EMBED_MAX_VECTORS_SCORED candidates and the requested result limit.
    """
    cap = limit if limit is not None else semantic_max_chunks()
    threshold = (
        min_similarity if min_similarity is not None else semantic_min_similarity()
    )
    if cap <= 0 or not query_vec:
        return []

    rows = conn.execute(
        "SELECT e.chunk_id, e.document_id, e.embedding, e.dimension,"
        " c.page_number, c.content, d.original_filename"
        " FROM chunk_embeddings e"
        " JOIN document_chunks c ON c.id = e.chunk_id"
        " JOIN documents d ON d.id = e.document_id"
        " WHERE e.embed_model = ? AND e.dimension = ?"
        " ORDER BY e.chunk_id LIMIT ?",
        (embed_model, len(query_vec), embed_max_vectors_scored()),
    ).fetchall()

    scored: list[SemanticCandidate] = []
    for row in rows:
        vector = unpack_vector(row["embedding"], row["dimension"])
        if vector is None:
            continue  # corrupt or non-finite stored vector
        similarity = cosine_similarity(query_vec, vector)
        if similarity < threshold:
            continue
        scored.append(
            SemanticCandidate(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                original_filename=row["original_filename"],
                page_number=row["page_number"],
                content=row["content"],
                similarity=similarity,
            )
        )
    scored.sort(key=lambda c: (-c.similarity, c.chunk_id))
    return scored[:cap]
