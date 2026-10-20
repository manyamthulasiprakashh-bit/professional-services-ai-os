"""Network-free tests for embedding persistence and cosine ranking (Step 5.3)."""

import sqlite3

import pytest
from app import embeddings
from app.llm import LLMUnavailableError
from embedding_fakes import (
    BrokenEmbedder,
    FakeEmbedder,
    WrongDimensionEmbedder,
)


def _seed_document(conn, chunks, *, filename="harbor.txt") -> int:
    cur = conn.execute(
        "INSERT INTO clients (name, email, created_at) VALUES ('Acme', NULL, 'now')"
    )
    client_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO matters (client_id, title, status, created_at)"
        " VALUES (?, 'Harbor merger', 'open', 'now')",
        (client_id,),
    )
    matter_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO documents (matter_id, original_filename, stored_name, size_bytes,"
        " sha256, status, extraction_error, created_at)"
        " VALUES (?, ?, ?, 10, 'sha', 'extracted', NULL, 'now')",
        (matter_id, filename, f"stored-{matter_id}"),
    )
    document_id = cur.lastrowid
    for page, content in chunks:
        conn.execute(
            "INSERT INTO document_chunks (document_id, page_number, content)"
            " VALUES (?, ?, ?)",
            (document_id, page, content),
        )
    conn.commit()
    return document_id


# --- encoding round-trips ---------------------------------------------------


def test_pack_unpack_round_trip() -> None:
    vector = [0.5, -1.25, 3.0]
    blob = embeddings.pack_vector(vector)
    assert embeddings.unpack_vector(blob, 3) == pytest.approx(vector)


def test_unpack_rejects_wrong_size_and_corruption() -> None:
    assert embeddings.unpack_vector(b"abcdef", 3) is None  # wrong length
    assert embeddings.unpack_vector(b"", 0) is None
    assert embeddings.unpack_vector(embeddings.pack_vector([0.1, 0.2]), 5) is None


def test_cosine_similarity_basics() -> None:
    assert embeddings.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert embeddings.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert embeddings.cosine_similarity([0.0, 0.0], [1.0, 1.0]) == -1.0
    assert embeddings.cosine_similarity([1.0], [1.0, 2.0]) == -1.0


# --- persistence and re-indexing --------------------------------------------


def test_index_persists_and_is_idempotent(conn) -> None:
    doc = _seed_document(conn, [(1, "The fee cap is 50k"), (2, "Harbor merger")])
    embedder = FakeEmbedder()
    first = embeddings.upsert_chunk_embeddings(conn, embedder, doc)
    conn.commit()
    assert first.indexed == 2
    n, models, dims = embeddings.index_stats(conn)
    assert n == 2 and models == {"fake-embed"} and len(dims) == 1

    second = embeddings.upsert_chunk_embeddings(conn, embedder, doc)
    conn.commit()
    n_after, _, _ = embeddings.index_stats(conn)
    assert second.indexed == 2 and n_after == 2  # replaced, not duplicated


def test_blank_chunks_are_skipped(conn) -> None:
    doc = _seed_document(conn, [(1, "   "), (2, "real text here")])
    result = embeddings.upsert_chunk_embeddings(conn, FakeEmbedder(), doc)
    assert result.indexed == 1 and result.skipped == 1


def test_provider_failure_leaves_previous_index_intact(conn) -> None:
    doc = _seed_document(conn, [(1, "fee cap")])
    embeddings.upsert_chunk_embeddings(conn, FakeEmbedder(), doc)
    conn.commit()
    n_before, _, _ = embeddings.index_stats(conn)

    with pytest.raises(LLMUnavailableError):
        embeddings.upsert_chunk_embeddings(conn, BrokenEmbedder(), doc)
    # staged failure must not delete existing rows
    n_after, _, _ = embeddings.index_stats(conn)
    assert n_after == n_before == 1


def test_transaction_rollback_keeps_old_index(conn) -> None:
    doc = _seed_document(conn, [(1, "fee cap")])
    embeddings.upsert_chunk_embeddings(conn, FakeEmbedder(), doc)
    conn.commit()
    old = conn.execute("SELECT embedding FROM chunk_embeddings").fetchone()["embedding"]

    try:
        embeddings.upsert_chunk_embeddings(conn, FakeEmbedder("other-model"), doc)
        raise RuntimeError("simulated crash after staging")
    except RuntimeError:
        conn.rollback()

    n, models, _ = embeddings.index_stats(conn)
    assert n == 1 and models == {"fake-embed"}
    assert conn.execute("SELECT embedding FROM chunk_embeddings").fetchone()["embedding"] == old


def test_replace_document_chunks_invalidates_embeddings(conn) -> None:
    from app import repository
    from app.extraction import ExtractedChunk

    doc = _seed_document(conn, [(1, "fee cap")])
    embeddings.upsert_chunk_embeddings(conn, FakeEmbedder(), doc)
    conn.commit()
    assert embeddings.index_stats(conn)[0] == 1

    repository.replace_document_chunks(
        conn, doc, [ExtractedChunk(page_number=1, content="new content")]
    )
    conn.commit()
    assert embeddings.index_stats(conn)[0] == 0


# --- staleness / model & dimension changes ----------------------------------


def test_model_change_marks_index_stale(conn) -> None:
    doc = _seed_document(conn, [(1, "fee cap")])
    embeddings.upsert_chunk_embeddings(conn, FakeEmbedder("model-a"), doc)
    conn.commit()
    assert embeddings.is_stale(conn, FakeEmbedder("model-a")) is False
    assert embeddings.is_stale(conn, FakeEmbedder("model-b")) is True


def test_empty_index_is_not_stale(conn) -> None:
    assert embeddings.is_stale(conn, FakeEmbedder()) is False


def test_mixed_models_count_as_stale(conn) -> None:
    doc = _seed_document(conn, [(1, "fee cap")])
    embeddings.upsert_chunk_embeddings(conn, FakeEmbedder("model-a"), doc)
    embeddings.upsert_chunk_embeddings(conn, FakeEmbedder("model-b"), doc)
    conn.commit()
    assert embeddings.is_stale(conn, FakeEmbedder("model-a")) is True


# --- ranking ----------------------------------------------------------------


def test_semantic_ranking_orders_by_similarity(conn) -> None:
    doc = _seed_document(
        conn,
        [(1, "the fee cap is fifty thousand"), (2, "parking ticket appeal")],
    )
    embedder = FakeEmbedder()
    embeddings.upsert_chunk_embeddings(conn, embedder, doc)
    conn.commit()

    query = embedder.embed(["what is the fee cap"])[0]
    candidates = embeddings.semantic_candidates(
        conn, query, embed_model=embedder.embed_model, min_similarity=0.0
    )
    assert candidates
    assert "fee cap" in candidates[0].content
    assert candidates[0].page_number == 1
    assert candidates[0].similarity >= candidates[-1].similarity


def test_semantic_threshold_filters_weak_matches(conn) -> None:
    doc = _seed_document(conn, [(1, "parking ticket appeal")])
    embedder = FakeEmbedder()
    embeddings.upsert_chunk_embeddings(conn, embedder, doc)
    conn.commit()
    query = embedder.embed(["unrelated quantum topic"])[0]
    candidates = embeddings.semantic_candidates(
        conn, query, embed_model=embedder.embed_model, min_similarity=0.99
    )
    assert candidates == []


def test_semantic_skips_incompatible_dimension_and_model(conn) -> None:
    doc = _seed_document(conn, [(1, "fee cap")])
    embedder = FakeEmbedder()
    embeddings.upsert_chunk_embeddings(conn, embedder, doc)
    conn.commit()
    query = embedder.embed(["fee cap"])[0]

    # different model name -> no rows scored
    assert embeddings.semantic_candidates(
        conn, query, embed_model="other-model", min_similarity=0.0
    ) == []
    # different dimension -> no rows scored
    assert embeddings.semantic_candidates(
        conn, query + [0.0], embed_model=embedder.embed_model, min_similarity=0.0
    ) == []


def test_corrupt_and_nonfinite_vectors_are_skipped(conn) -> None:
    doc = _seed_document(conn, [(1, "fee cap"), (2, "more fee cap")])
    embedder = FakeEmbedder()
    embeddings.upsert_chunk_embeddings(conn, embedder, doc)
    conn.commit()

    # Corrupt row 1: truncate its BLOB; row 2: store NaN.
    conn.execute(
        "UPDATE chunk_embeddings SET embedding = ? WHERE chunk_id ="
        " (SELECT id FROM document_chunks WHERE page_number = 1)",
        (sqlite3.Binary(b"xx"),),
    )
    conn.execute(
        "UPDATE chunk_embeddings SET embedding = ? WHERE chunk_id ="
        " (SELECT id FROM document_chunks WHERE page_number = 2)",
        (sqlite3.Binary(embeddings.pack_vector([float("nan")] * 9)),),
    )
    conn.commit()

    query = embedder.embed(["fee cap"])[0]
    candidates = embeddings.semantic_candidates(
        conn, query, embed_model=embedder.embed_model, min_similarity=-1.0
    )
    assert candidates == []


def test_result_limit_is_enforced(conn, monkeypatch) -> None:
    doc = _seed_document(conn, [(i, f"fee cap {i}") for i in range(1, 8)])
    embedder = FakeEmbedder()
    embeddings.upsert_chunk_embeddings(conn, embedder, doc)
    conn.commit()
    query = embedder.embed(["fee cap"])[0]
    candidates = embeddings.semantic_candidates(
        conn, query, embed_model=embedder.embed_model, limit=2, min_similarity=-1.0
    )
    assert len(candidates) == 2


def test_query_embedding_returns_none_on_failure(conn) -> None:
    assert embeddings.query_embedding(conn, BrokenEmbedder(), "fee cap") is None
    assert embeddings.query_embedding(conn, FakeEmbedder(), "   ") is None


def test_embed_batch_size_is_respected(conn) -> None:
    doc = _seed_document(conn, [(i, f"chunk {i}") for i in range(1, 6)])
    embedder = FakeEmbedder()
    embeddings.upsert_chunk_embeddings(conn, embedder, doc)
    # default EMBED_MAX_TEXTS_PER_REQUEST is 16, so one batch of 5
    assert [len(batch) for batch in embedder.calls] == [5]
