"""Text extraction from uploaded documents.

Extraction is honest: if no machine-readable text can be recovered the document
is marked failed with a clear reason. OCR is deliberately out of scope, so
scanned/image-only PDFs report failure rather than inventing text.
"""

import io
import os
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

DEFAULT_MAX_PDF_PAGES = 100
DEFAULT_MAX_EXTRACTED_CHARS = 500_000


class ExtractionError(Exception):
    """Extraction cannot be completed; the message is safe to surface."""


def max_pdf_pages() -> int:
    override = os.environ.get("PS_MAX_PDF_PAGES")
    return int(override) if override else DEFAULT_MAX_PDF_PAGES


def max_extracted_chars() -> int:
    override = os.environ.get("PS_MAX_EXTRACTED_CHARS")
    return int(override) if override else DEFAULT_MAX_EXTRACTED_CHARS


@dataclass
class ExtractedChunk:
    page_number: int | None  # None for plain text
    content: str


def extract_chunks(path: Path, extension: str) -> list[ExtractedChunk]:
    """Extract text from a stored document. Raises ExtractionError on failure."""
    data = path.read_bytes()
    if extension == ".pdf":
        chunks = _extract_pdf(data)
    elif extension == ".txt":
        chunks = _extract_txt(data)
    else:  # pragma: no cover - guarded at upload time
        raise ExtractionError(f"Unsupported document type: {extension}")

    if not any(chunk.content.strip() for chunk in chunks):
        raise ExtractionError(
            "No extractable text found. The file may be scanned or image-only; "
            "OCR is not enabled in this prototype."
        )

    total = sum(len(chunk.content) for chunk in chunks)
    if total > max_extracted_chars():
        raise ExtractionError(
            f"Extracted text ({total} characters) exceeds the limit of "
            f"{max_extracted_chars()} characters."
        )
    return chunks


def _extract_pdf(data: bytes) -> list[ExtractedChunk]:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError("PDF could not be opened or is corrupted.") from exc

    page_count = len(reader.pages)
    if page_count > max_pdf_pages():
        raise ExtractionError(
            f"PDF has {page_count} pages which exceeds the limit of "
            f"{max_pdf_pages()} pages."
        )

    chunks: list[ExtractedChunk] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            raise ExtractionError(
                f"Text extraction failed on page {number}."
            ) from exc
        chunks.append(ExtractedChunk(page_number=number, content=text.strip()))
    return chunks


def _extract_txt(data: bytes) -> list[ExtractedChunk]:
    text = data.decode("utf-8-sig")
    return [ExtractedChunk(page_number=None, content=text.strip())]
