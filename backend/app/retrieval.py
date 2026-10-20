"""Deterministic, read-only retrieval for the AI assistant (Step 5.2/5.3).

Keyword + explicit-identifier retrieval is the base (Step 5.2). Step 5.3 adds
an OPTIONAL semantic path: when an embedder is supplied and the stored index is
compatible, document chunks are also ranked by cosine similarity and merged
in. The LLM never constructs queries and all result sizes are bounded. If the
embedder is missing, unavailable or the index is stale, retrieval degrades to
exactly the keyword/ID behaviour.
"""

import os
import re
import sqlite3
from dataclasses import dataclass, field

from .llm import Embedder

DEFAULT_MAX_RECORDS = 5  # per record type (clients, matters, tasks)
DEFAULT_MAX_CHUNKS = 6  # document chunks
DEFAULT_MAX_EXCERPT_CHARS = 600  # per chunk excerpt
DEFAULT_MAX_CONTEXT_CHARS = 6000  # total document text in the prompt
DEFAULT_MAX_TERMS = 12

_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "any", "can", "had",
    "her", "was", "one", "our", "out", "has", "have", "what", "when", "who",
    "how", "why", "does", "did", "with", "this", "that", "these", "those",
    "from", "about", "into", "them", "they", "their", "there", "would",
    "could", "should", "tell", "give", "show", "list", "find", "get", "all",
}

_TERM_RE = re.compile(r"[a-z0-9][a-z0-9'-]{2,}")  # min 3 chars avoids "in"/"is" false matches
_IDENTIFIER_RE = re.compile(r"\b(client|matter|task|document)\s*#?\s*(\d+)\b", re.I)


def _limit(name: str, default: int) -> int:
    override = os.environ.get(name)
    return int(override) if override else default


def max_records() -> int:
    return _limit("RETRIEVAL_MAX_RECORDS", DEFAULT_MAX_RECORDS)


def max_chunks() -> int:
    return _limit("RETRIEVAL_MAX_CHUNKS", DEFAULT_MAX_CHUNKS)


def max_excerpt_chars() -> int:
    return _limit("RETRIEVAL_MAX_EXCERPT_CHARS", DEFAULT_MAX_EXCERPT_CHARS)


def max_context_chars() -> int:
    return _limit("RETRIEVAL_MAX_CONTEXT_CHARS", DEFAULT_MAX_CONTEXT_CHARS)


@dataclass
class RetrievedChunk:
    document_id: int
    original_filename: str
    page_number: int | None
    excerpt: str
    source: str = "keyword"  # "keyword" or "semantic" — provenance for logs/tests


@dataclass
class RetrievalResult:
    clients: list[sqlite3.Row] = field(default_factory=list)
    matters: list[sqlite3.Row] = field(default_factory=list)
    tasks: list[sqlite3.Row] = field(default_factory=list)
    chunks: list[RetrievedChunk] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    identifiers_found: bool = False
    semantic_used: bool = False
    semantic_skipped_reason: str | None = None


def tokenize_question(question: str, max_terms: int | None = None) -> list[str]:
    """Deterministic term extraction: lowercase words of 3+ chars, de-duped."""
    cap = max_terms if max_terms is not None else DEFAULT_MAX_TERMS
    terms: list[str] = []
    for match in _TERM_RE.finditer(question.lower()):
        term = match.group(0)
        if term in _STOPWORDS or term in terms:
            continue
        terms.append(term)
        if len(terms) >= cap:
            break
    return terms


def parse_identifiers(question: str) -> dict[str, list[int]]:
    """Extract explicit references like 'client 2' or 'matter #3'."""
    found: dict[str, list[int]] = {}
    for kind, raw_id in _IDENTIFIER_RE.findall(question):
        kind = kind.lower()
        number = int(raw_id)
        if number >= 1 and number not in found.setdefault(kind, []):
            found[kind].append(number)
    return found


def retrieve(
    conn: sqlite3.Connection,
    question: str,
    embedder: Embedder | None = None,
) -> RetrievalResult:
    """Retrieve bounded, relevant workspace records for a question.

    Keyword/ID retrieval always runs. When `embedder` is provided and the
    stored index is compatible, semantically-ranked chunks are merged in.
    """
    result = RetrievalResult(terms=tokenize_question(question))
    identifiers = parse_identifiers(question)
    result.identifiers_found = bool(identifiers)

    like_clauses = " OR ".join("name LIKE ?" for _ in result.terms) if result.terms else None
    params: list[object] = [f"%{t}%" for t in result.terms]

    # Clients: explicit ID and/or name/email keyword match.
    client_rows: dict[int, sqlite3.Row] = {}
    for client_id in identifiers.get("client", [])[: max_records()]:
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if row:
            client_rows[client_id] = row
    if like_clauses:
        for row in conn.execute(
            f"SELECT * FROM clients WHERE {like_clauses}"
            f" ORDER BY id LIMIT ?",
            [*params, max_records()],
        ):
            client_rows[row["id"]] = row
    result.clients = list(client_rows.values())

    # Matters: explicit ID and/or title keyword match.
    matter_rows: dict[int, sqlite3.Row] = {}
    for matter_id in identifiers.get("matter", [])[: max_records()]:
        row = conn.execute("SELECT * FROM matters WHERE id = ?", (matter_id,)).fetchone()
        if row:
            matter_rows[matter_id] = row
    if result.terms:
        title_clauses = " OR ".join("title LIKE ?" for _ in result.terms)
        for row in conn.execute(
            f"SELECT * FROM matters WHERE {title_clauses} ORDER BY id LIMIT ?",
            [*params, max_records()],
        ):
            matter_rows[row["id"]] = row
    result.matters = list(matter_rows.values())

    # Tasks: explicit ID and/or title keyword match.
    task_rows: dict[int, sqlite3.Row] = {}
    for task_id in identifiers.get("task", [])[: max_records()]:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row:
            task_rows[task_id] = row
    if result.terms:
        task_clauses = " OR ".join("title LIKE ?" for _ in result.terms)
        for row in conn.execute(
            f"SELECT * FROM tasks WHERE {task_clauses} ORDER BY id LIMIT ?",
            [*params, max_records()],
        ):
            task_rows[row["id"]] = row
    result.tasks = list(task_rows.values())

    # Document chunks: keyword match on extracted text or filename.
    if result.terms:
        chunk_clauses = " OR ".join(
            "(c.content LIKE ? OR d.original_filename LIKE ?)" for _ in result.terms
        )
        chunk_params: list[object] = []
        for term in result.terms:
            chunk_params.extend([f"%{term}%", f"%{term}%"])
        rows = conn.execute(
            "SELECT c.document_id, c.page_number, c.content, d.original_filename"
            f" FROM document_chunks c JOIN documents d ON d.id = c.document_id"
            f" WHERE {chunk_clauses} ORDER BY c.document_id, c.id LIMIT ?",
            [*chunk_params, max_chunks()],
        ).fetchall()
        budget = max_context_chars()
        for row in rows:
            excerpt = row["content"][: max_excerpt_chars()]
            if budget <= 0:
                break
            result.chunks.append(
                RetrievedChunk(
                    document_id=row["document_id"],
                    original_filename=row["original_filename"],
                    page_number=row["page_number"],
                    excerpt=excerpt[:budget],
                    source="keyword",
                )
            )
            budget -= len(excerpt)

    if embedder is not None:
        _merge_semantic_chunks(conn, question, embedder, result)

    return result


def _merge_semantic_chunks(
    conn: sqlite3.Connection,
    question: str,
    embedder: Embedder,
    result: RetrievalResult,
) -> None:
    """Add semantically-ranked chunks, preserving and deduping keyword hits.

    Never raises: any unavailability or staleness is recorded on the result
    and keyword/ID retrieval stands. No question or chunk text is logged.
    """
    from . import embeddings  # local import avoids a circular import at module load

    try:
        if embeddings.is_stale(conn, embedder):
            result.semantic_skipped_reason = "index_stale"
            return
        query_vec = embeddings.query_embedding(conn, embedder, question)
        if not query_vec:
            result.semantic_skipped_reason = "embedder_unavailable"
            return
        candidates = embeddings.semantic_candidates(
            conn, query_vec, embed_model=embedder.embed_model
        )
    except Exception:  # defensive: semantic path must never break keyword retrieval
        result.semantic_skipped_reason = "semantic_error"
        return

    if not candidates:
        result.semantic_used = True
        return

    seen = {_chunk_key(c.document_id, c.page_number, c.excerpt) for c in result.chunks}
    budget = max_context_chars() - sum(len(c.excerpt) for c in result.chunks)
    for candidate in candidates:
        if len(result.chunks) >= max_chunks():
            break
        excerpt = candidate.content[: max_excerpt_chars()]
        key = _chunk_key(candidate.document_id, candidate.page_number, excerpt)
        if key in seen:
            continue  # already present from the keyword path
        if budget <= 0:
            break
        result.chunks.append(
            RetrievedChunk(
                document_id=candidate.document_id,
                original_filename=candidate.original_filename,
                page_number=candidate.page_number,
                excerpt=excerpt[:budget],
                source="semantic",
            )
        )
        seen.add(key)
        budget -= len(excerpt)
    result.semantic_used = True


def _chunk_key(document_id: int, page_number: int | None, excerpt: str) -> tuple:
    """Dedup key: same document, page and leading text are one chunk."""
    return (document_id, page_number, excerpt[:120])
