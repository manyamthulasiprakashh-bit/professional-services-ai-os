"""Tests for the summarization endpoint and Ollama health reporting."""

from datetime import datetime, timezone

import pytest
from app.llm import LLMUnavailableError, SummaryOutput
from tests.pdf_helpers import make_minimal_pdf


class FakeSummarizer:
    """Provider double — no network access in tests."""

    summary_model = "fake-model"

    def summarize(self, title, chunks) -> SummaryOutput:
        return SummaryOutput(
            summary="The document discusses merger diligence items.",
            key_points=["Financial statements requested", "Question list pending"],
            model=self.summary_model,
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )


class BrokenSummarizer:
    summary_model = "broken-model"

    def summarize(self, title, chunks) -> SummaryOutput:
        raise LLMUnavailableError("Could not reach the summarization service at test.")


@pytest.fixture()
def extracted_document(client) -> dict:
    client_id = client.post("/clients", json={"name": "Acme Legal"}).json()["id"]
    matter_id = client.post(
        "/matters", json={"client_id": client_id, "title": "Merger diligence"}
    ).json()["id"]
    response = client.post(
        "/documents",
        data={"matter_id": str(matter_id)},
        files={"file": ("memo.pdf", make_minimal_pdf("Financial summary of Harbor"))},
    )
    assert response.status_code == 201
    return response.json()


def test_summarize_separates_source_facts_from_model_output(
    client, extracted_document, monkeypatch
) -> None:
    from app.main import app
    from app.routers import documents as documents_router

    app.dependency_overrides[documents_router.get_summarizer] = lambda: FakeSummarizer()
    try:
        response = client.post(f"/documents/{extracted_document['id']}/summarize")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    # Model-generated side, explicitly flagged.
    assert body["summary"].startswith("The document discusses")
    assert body["machine_generated"] is True
    assert body["review_required"] is True
    assert body["model"] == "fake-model"
    assert body["key_points"]
    # Source-fact side: verbatim extracted text, distinct fields.
    assert body["source_excerpts"]
    assert body["source_excerpts"][0]["page_number"] == 1
    assert "Harbor" in body["source_excerpts"][0]["excerpt"]
    assert "model-generated" in body["source_note"]


def test_summarize_requires_extracted_status(client, extracted_document, monkeypatch) -> None:
    from app.main import app
    from app.routers import documents as documents_router

    # Force the document into the failed state via re-extraction of a scan is
    # not possible here, so upload a scanned-like PDF instead.
    matter_id = extracted_document["matter_id"]
    from tests.pdf_helpers import make_scanned_like_pdf

    failed = client.post(
        "/documents",
        data={"matter_id": str(matter_id)},
        files={"file": ("scan.pdf", make_scanned_like_pdf())},
    ).json()
    assert failed["status"] == "extraction_failed"

    app.dependency_overrides[documents_router.get_summarizer] = lambda: FakeSummarizer()
    try:
        response = client.post(f"/documents/{failed['id']}/summarize")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "not been extracted" in response.json()["detail"]


def test_summarize_unknown_document_404(client) -> None:
    assert client.post("/documents/9999/summarize").status_code == 404


def test_summarize_returns_503_when_provider_unavailable(
    client, extracted_document
) -> None:
    from app.main import app
    from app.routers import documents as documents_router

    app.dependency_overrides[documents_router.get_summarizer] = lambda: BrokenSummarizer()
    try:
        response = client.post(f"/documents/{extracted_document['id']}/summarize")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "Could not reach" in response.json()["detail"]


def test_ollama_health_reports_unavailable_without_crashing(
    client, monkeypatch
) -> None:
    from app.routers import ollama as ollama_router

    def unreachable(*args, **kwargs):
        raise LLMUnavailableError("down")

    monkeypatch.setattr(ollama_router, "list_models", unreachable)
    response = client.get("/ollama/health")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["models"] == []
    assert body["summary_model"]


def test_ollama_health_reports_models_when_available(client, monkeypatch) -> None:
    from app.routers import ollama as ollama_router

    monkeypatch.setattr(
        ollama_router, "list_models", lambda *a, **k: ["qwen3:4b", "nomic-embed-text:latest"]
    )
    response = client.get("/ollama/health")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert "qwen3:4b" in body["models"]
