"""Tests for document upload, validation, storage and text extraction."""

from pathlib import Path

import pytest
from app import documents_storage
from tests.pdf_helpers import (
    make_blank_pdf,
    make_minimal_pdf,
    make_scanned_like_pdf,
)


def _seed_matter(client) -> int:
    client_id = client.post("/clients", json={"name": "Acme Legal"}).json()["id"]
    return client.post(
        "/matters", json={"client_id": client_id, "title": "Merger diligence"}
    ).json()["id"]


def _upload(client, matter_id: int, filename: str, data: bytes):
    return client.post(
        "/documents",
        data={"matter_id": str(matter_id)},
        files={"file": (filename, data)},
    )


def test_upload_txt_and_list(client, docs_dir: Path) -> None:
    matter_id = _seed_matter(client)
    response = _upload(client, matter_id, "notes.txt", b"Engagement kickoff notes.")
    assert response.status_code == 201
    body = response.json()
    assert body["original_filename"] == "notes.txt"
    assert body["status"] == "extracted"
    assert body["size_bytes"] == len(b"Engagement kickoff notes.")
    assert body["extraction_error"] is None
    # Never expose the stored path/name.
    assert "stored_name" not in body
    assert "data/" not in str(body)

    listed = client.get("/documents", params={"matter_id": matter_id})
    assert listed.status_code == 200
    assert [d["id"] for d in listed.json()] == [body["id"]]

    # The file exists on disk under the isolated storage dir with a UUID name.
    stored = list(docs_dir.iterdir())
    assert len(stored) == 1
    assert stored[0].name.endswith(".txt")
    assert stored[0].read_bytes() == b"Engagement kickoff notes."


def test_upload_pdf_extracts_per_page(client, docs_dir: Path, db_path) -> None:
    matter_id = _seed_matter(client)
    response = _upload(client, matter_id, "diligence.pdf", make_minimal_pdf("Harbor numbers"))
    assert response.status_code == 201
    assert response.json()["status"] == "extracted"

    from app.db import connect

    conn = connect(db_path)
    try:
        chunks = conn.execute(
            "SELECT page_number, content FROM document_chunks"
            " WHERE document_id = ?",
            (response.json()["id"],),
        ).fetchall()
    finally:
        conn.close()
    assert len(chunks) == 1
    assert chunks[0]["page_number"] == 1
    assert "Harbor numbers" in chunks[0]["content"]


def test_scanned_pdf_reports_extraction_failure(client) -> None:
    matter_id = _seed_matter(client)
    response = _upload(client, matter_id, "scan.pdf", make_scanned_like_pdf())
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "extraction_failed"
    assert "OCR" in body["extraction_error"]


def test_reextract_endpoint_recovers_after_fix_is_not_possible_for_missing_file(
    client,
) -> None:
    matter_id = _seed_matter(client)
    created = _upload(client, matter_id, "scan.pdf", make_scanned_like_pdf()).json()
    again = client.post(f"/documents/{created['id']}/extract")
    assert again.status_code == 200
    assert again.json()["status"] == "extraction_failed"


def test_reextract_unknown_document_404(client) -> None:
    assert client.post("/documents/9999/extract").status_code == 404


def test_index_unknown_document_404(client) -> None:
    assert client.post("/documents/9999/index").status_code == 404


def test_traversal_filename_is_neutralized_by_uuid_storage(client, docs_dir: Path) -> None:
    """A traversal-looking filename is display-only; storage name is a UUID."""
    matter_id = _seed_matter(client)
    response = _upload(client, matter_id, "../../evil.txt", b"harmless text")
    assert response.status_code == 201
    body = response.json()
    # Original filename is retained as display metadata...
    assert body["original_filename"] == "../../evil.txt"
    # ...but nothing resolves it to a filesystem path: the stored file lives
    # directly under the isolated docs_dir with a separator-free name.
    stored = list(docs_dir.iterdir())
    assert len(stored) == 1
    assert stored[0].parent == docs_dir
    assert "/" not in stored[0].name and ".." not in stored[0].name
    # No file escaped to a parent directory.
    assert not (docs_dir.parent / "evil.txt").exists()


def test_extraction_failure_leaves_consistent_state(client, docs_dir: Path, db_path) -> None:
    """A failed extraction is recorded honestly: no chunks, no orphaned index."""
    matter_id = _seed_matter(client)
    created = _upload(client, matter_id, "scan.pdf", make_scanned_like_pdf()).json()
    assert created["status"] == "extraction_failed"

    # No chunks were produced for the failed document.
    from app.db import connect

    conn = connect(db_path)
    try:
        chunks = conn.execute(
            "SELECT COUNT(*) AS n FROM document_chunks WHERE document_id = ?",
            (created["id"],),
        ).fetchone()["n"]
        embeds = conn.execute(
            "SELECT COUNT(*) AS n FROM chunk_embeddings WHERE document_id = ?",
            (created["id"],),
        ).fetchone()["n"]
    finally:
        conn.close()
    assert chunks == 0
    assert embeds == 0

    # The stored file is retained deliberately (re-extract remains possible),
    # and there is exactly one stored file — no duplicate/orphan was left.
    assert len(list(docs_dir.iterdir())) == 1


def test_wrong_extension_rejected(client, docs_dir: Path) -> None:
    matter_id = _seed_matter(client)
    response = _upload(client, matter_id, "photo.png", b"\x89PNG fake")
    assert response.status_code == 422
    assert "Unsupported file type" in response.json()["detail"]
    assert not docs_dir.exists() or not any(docs_dir.iterdir())


def test_pdf_magic_mismatch_rejected(client) -> None:
    matter_id = _seed_matter(client)
    response = _upload(client, matter_id, "fake.pdf", b"<html>not a pdf</html>")
    assert response.status_code == 422
    assert "does not look like a PDF" in response.json()["detail"]


def test_binary_txt_rejected(client) -> None:
    matter_id = _seed_matter(client)
    response = _upload(client, matter_id, "binary.txt", b"ab\x00cd")
    assert response.status_code == 422
    assert "not plain text" in response.json()["detail"]


def test_empty_file_rejected(client) -> None:
    matter_id = _seed_matter(client)
    response = _upload(client, matter_id, "empty.txt", b"")
    assert response.status_code == 422
    assert "empty" in response.json()["detail"].lower()


def test_oversized_file_rejected(client, monkeypatch) -> None:
    monkeypatch.setenv("PS_MAX_UPLOAD_BYTES", "100")
    matter_id = _seed_matter(client)
    response = _upload(client, matter_id, "big.txt", b"x" * 200)
    assert response.status_code == 413


def test_missing_matter_404(client) -> None:
    response = _upload(client, 9999, "notes.txt", b"hello")
    assert response.status_code == 404
    assert "9999" in response.json()["detail"]


def test_duplicate_content_409(client) -> None:
    matter_id = _seed_matter(client)
    assert _upload(client, matter_id, "notes.txt", b"same bytes").status_code == 201
    duplicate = _upload(client, matter_id, "renamed.txt", b"same bytes")
    assert duplicate.status_code == 409


def test_page_count_limit_enforced(client, monkeypatch) -> None:
    monkeypatch.setenv("PS_MAX_PDF_PAGES", "2")
    matter_id = _seed_matter(client)
    response = _upload(client, matter_id, "long.pdf", make_blank_pdf(5))
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "extraction_failed"
    assert "exceeds the limit of 2 pages" in body["extraction_error"]


def test_file_cleaned_up_when_database_write_fails(client, docs_dir, monkeypatch) -> None:
    from app import repository

    matter_id = _seed_matter(client)

    def broken_create(*args, **kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(repository, "create_document", broken_create)
    with pytest.raises(RuntimeError):
        client.post(
            "/documents",
            data={"matter_id": str(matter_id)},
            files={"file": ("notes.txt", b"payload")},
        )
    assert not docs_dir.exists() or not any(docs_dir.iterdir())


def test_documents_persist_across_connections(db_path, docs_dir, client) -> None:
    matter_id = _seed_matter(client)
    created = _upload(client, matter_id, "notes.txt", b"durable bytes").json()

    from app.db import connect

    fresh = connect(db_path)
    try:
        rows = fresh.execute("SELECT * FROM documents").fetchall()
    finally:
        fresh.close()
    assert [r["id"] for r in rows] == [created["id"]]
    assert rows[0]["status"] == "extracted"


def test_storage_module_rejects_path_traversal() -> None:
    with pytest.raises(documents_storage.InvalidDocumentError):
        documents_storage.stored_path("../secrets.txt")
