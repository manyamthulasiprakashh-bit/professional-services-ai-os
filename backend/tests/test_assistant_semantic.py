"""Hybrid keyword+semantic retrieval and /assistant/ask integration (Step 5.3).

Network-free: fake embedder and fake assistant via dependency overrides.
Every workspace is seeded with the embedder override active, so upload-time
indexing never reaches real Ollama.
"""

from app import embeddings, repository, retrieval
from embedding_fakes import BrokenEmbedder, FakeAssistant, FakeEmbedder
from test_assistant_retrieval import _seed_workspace


def _override(embedder):
    from app.main import app
    from app.routers import assistant as assistant_router

    app.dependency_overrides[assistant_router.get_embedder] = lambda: embedder


def _clear():
    from app.main import app

    app.dependency_overrides.clear()


def _seed(client, embedder) -> dict:
    """Seed a workspace with the fake embedder override active throughout."""
    _override(embedder)
    try:
        return _seed_workspace(client)
    finally:
        _clear()


def _ask(client, question: str, assistant, embedder):
    from app.main import app
    from app.routers import assistant as assistant_router

    app.dependency_overrides[assistant_router.get_assistant] = lambda: assistant
    app.dependency_overrides[assistant_router.get_embedder] = lambda: embedder
    try:
        return client.post("/assistant/ask", json={"question": question})
    finally:
        app.dependency_overrides.clear()


# --- hybrid retrieval -------------------------------------------------------


def test_hybrid_retrieval_adds_semantic_chunk_without_keyword_overlap(client, conn) -> None:
    embedder = FakeEmbedder()
    _seed(client, embedder)  # document (text: "fee cap") indexed during upload

    # "price ceiling" shares NO literal keyword with the document text
    # ("fee cap"), but is semantically adjacent via the fake synonymous vocab.
    keyword_only = retrieval.retrieve(conn, "price ceiling")
    assert keyword_only.chunks == []  # nothing matches by keyword

    hybrid = retrieval.retrieve(conn, "price ceiling", embedder=embedder)
    assert hybrid.semantic_used is True
    assert any(c.source == "semantic" for c in hybrid.chunks)


def test_keyword_behaviour_preserved_without_embedder(client, conn) -> None:
    embedder = FakeEmbedder()
    seed = _seed(client, embedder)

    with_embedder = retrieval.retrieve(
        conn, f"client {seed['other']['id']}", embedder=embedder
    )
    without = retrieval.retrieve(conn, f"client {seed['other']['id']}")
    assert [c["id"] for c in with_embedder.clients] == [c["id"] for c in without.clients]
    assert with_embedder.clients


def test_semantic_dedupes_overlapping_keyword_chunks(client, conn) -> None:
    embedder = FakeEmbedder()
    _seed(client, embedder)

    result = retrieval.retrieve(conn, "Harbor fee cap", embedder=embedder)
    keys = [(c.document_id, c.page_number, c.excerpt[:120]) for c in result.chunks]
    assert len(keys) == len(set(keys))


def test_stale_index_falls_back_to_keyword(client, conn) -> None:
    stored = FakeEmbedder("model-a")
    _seed(client, stored)  # index written as model-a

    current = FakeEmbedder("model-b")  # config changed -> stale
    result = retrieval.retrieve(conn, "Harbor fee cap", embedder=current)
    assert result.semantic_used is False
    assert result.semantic_skipped_reason == "index_stale"
    assert result.chunks  # keyword retrieval still works


def test_unavailable_embedder_falls_back_to_keyword(client, conn) -> None:
    _seed(client, FakeEmbedder())
    result = retrieval.retrieve(conn, "Harbor fee cap", embedder=BrokenEmbedder())
    assert result.semantic_used is False
    assert result.semantic_skipped_reason == "embedder_unavailable"
    assert result.chunks


# --- endpoint-level ---------------------------------------------------------


def test_ask_returns_semantic_evidence_reference(client, conn) -> None:
    embedder = FakeEmbedder()
    seed = _seed(client, embedder)

    response = _ask(client, "price ceiling fee", FakeAssistant(), embedder)
    assert response.status_code == 200
    body = response.json()
    doc_evidence = [e for e in body["evidence"] if e["kind"] == "document"]
    assert doc_evidence
    assert any(f'Document #{seed["doc"]["id"]}' in e["reference"] for e in doc_evidence)
    assert body["evidence_sufficient"] is True


def test_ask_keyword_only_when_embedder_broken(client, conn) -> None:
    _seed(client, FakeEmbedder())
    response = _ask(client, "Acme Legal Harbor", FakeAssistant(), BrokenEmbedder())
    assert response.status_code == 200
    body = response.json()
    assert body["evidence_sufficient"] is True
    assert any(e["kind"] in {"client", "matter"} for e in body["evidence"])


def test_ask_empty_index_returns_no_evidence(client, conn) -> None:
    _seed(client, FakeEmbedder())
    response = _ask(client, "zzz quantum blockchain", FakeAssistant(), FakeEmbedder())
    body = response.json()
    assert body["evidence"] == []
    assert body["evidence_sufficient"] is False


def test_ask_still_503_when_assistant_broken(client, conn) -> None:
    from test_assistant_retrieval import BrokenAssistant

    _seed(client, FakeEmbedder())
    response = _ask(client, "Harbor fee cap", BrokenAssistant(), FakeEmbedder())
    assert response.status_code == 503


# --- index endpoints --------------------------------------------------------


def test_index_status_reports_state(client, conn) -> None:
    embedder = FakeEmbedder()
    _seed(client, embedder)
    _override(embedder)
    try:
        status = client.get("/documents/index").json()
    finally:
        _clear()

    assert status["indexed_chunks"] >= 1
    assert status["total_documents"] >= 1
    assert status["dimension"] is not None
    assert status["stale"] is False


def test_backfill_returns_honest_counts(client, conn) -> None:
    embedder = FakeEmbedder()
    _seed(client, embedder)
    _override(embedder)
    try:
        body = client.post("/documents/index").json()
    finally:
        _clear()

    assert body["indexed"] >= 1
    assert body["failed"] == 0


def test_backfill_reports_failure_without_raising(client, conn) -> None:
    _seed(client, FakeEmbedder())
    _override(BrokenEmbedder())
    try:
        response = client.post("/documents/index")
    finally:
        _clear()

    assert response.status_code == 200
    body = response.json()
    assert body["failed"] >= 1
    assert body["errors"]


def test_upload_succeeds_even_when_embedder_broken(client, conn) -> None:
    seed = _seed(client, FakeEmbedder())
    _override(BrokenEmbedder())
    try:
        response = client.post(
            "/documents",
            data={"matter_id": str(seed["harbor"]["id"])},
            files={"file": ("extra.txt", b"another fee cap note")},
        )
    finally:
        _clear()
    assert response.status_code == 201  # indexing failure must not fail upload


def test_single_document_index_endpoint(client, conn) -> None:
    seed = _seed(client, FakeEmbedder())
    _override(FakeEmbedder())
    try:
        response = client.post(f"/documents/{seed['doc']['id']}/index")
    finally:
        _clear()
    assert response.status_code == 200
    assert response.json()["indexed"] >= 1


# --- empty-index fast path --------------------------------------------------


def _propose(client, request: str, assistant, embedder):
    from app.main import app
    from app.routers import assistant as assistant_router

    app.dependency_overrides[assistant_router.get_assistant] = lambda: assistant
    app.dependency_overrides[assistant_router.get_embedder] = lambda: embedder
    try:
        return client.post("/assistant/propose-action", json={"request": request})
    finally:
        app.dependency_overrides.clear()


def test_ask_empty_index_skips_query_embedding(client) -> None:
    embedder = FakeEmbedder()
    response = _ask(client, "Acme Legal Harbor", FakeAssistant(), embedder)

    assert response.status_code == 200
    assert embedder.calls == []


def test_ask_populated_index_embeds_query_once(client, conn) -> None:
    embedder = FakeEmbedder()
    _seed(client, embedder)
    before = len(embedder.calls)

    response = _ask(client, "Acme Legal Harbor", FakeAssistant(), embedder)

    assert response.status_code == 200
    assert len(embedder.calls) == before + 1


def test_propose_empty_index_skips_query_embedding(client) -> None:
    embedder = FakeEmbedder()
    response = _propose(client, "add a task", FakeAssistant("not json"), embedder)

    assert response.status_code == 200
    assert embedder.calls == []


def test_propose_populated_index_embeds_query_once(client, conn) -> None:
    embedder = FakeEmbedder()
    _seed(client, embedder)
    before = len(embedder.calls)

    response = _propose(
        client, "add a task for Harbor", FakeAssistant("not json"), embedder
    )

    assert response.status_code == 200
    assert len(embedder.calls) == before + 1
