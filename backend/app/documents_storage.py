"""Safe on-disk storage for uploaded documents.

Files live under backend/data/documents/ (never publicly served). The stored
name is a server-generated UUID — a user-supplied filename is display metadata
only and is never used as a filesystem path. Writes use exclusive mode so an
existing file can never be overwritten.
"""

import hashlib
import os
import uuid
from pathlib import Path

DEFAULT_DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "documents"
DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB

ALLOWED_EXTENSIONS = (".pdf", ".txt")
_PDF_MAGIC = b"%PDF-"


class UploadTooLargeError(Exception):
    """The upload exceeds the configured size limit."""


class InvalidDocumentError(Exception):
    """The upload is empty, has a disallowed extension, or fails content sniffing."""


def resolve_docs_dir() -> Path:
    override = os.environ.get("PS_DOCS_DIR")
    return Path(override) if override else DEFAULT_DOCS_DIR


def max_upload_bytes() -> int:
    override = os.environ.get("PS_MAX_UPLOAD_BYTES")
    if override:
        return int(override)
    return DEFAULT_MAX_UPLOAD_BYTES


def read_upload_limited(file, limit: int) -> bytes:
    """Read an UploadFile-like object in chunks, enforcing a size limit.

    Raises UploadTooLargeError as soon as the limit is exceeded so a huge
    upload is never fully buffered.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise UploadTooLargeError(
                f"File exceeds the {limit}-byte upload limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def validate_content(original_filename: str, data: bytes) -> str:
    """Validate extension and sniff actual content. Returns the extension.

    Raises InvalidDocumentError with a clear, non-leaky reason.
    """
    if not data:
        raise InvalidDocumentError("File is empty.")

    name = original_filename or ""
    extension = Path(name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise InvalidDocumentError(
            "Unsupported file type. Allowed types: .pdf and .txt."
        )

    if extension == ".pdf":
        if _PDF_MAGIC not in data[:1024]:
            raise InvalidDocumentError(
                "File content does not look like a PDF (missing %PDF header)."
            )
        return extension

    # .txt: must be valid UTF-8 (allow BOM) and contain no NUL bytes.
    if b"\x00" in data:
        raise InvalidDocumentError(
            "File content is not plain text (binary data found)."
        )
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidDocumentError(
            "File is not valid UTF-8 plain text."
        ) from exc
    return extension


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_new_file(data: bytes, extension: str) -> tuple[str, Path]:
    """Write data to a new UUID-named file. Returns (stored_name, path).

    Exclusive creation — never overwrites. Collisions on the UUID are
    astronomically unlikely; regenerate a few times just in case.
    """
    docs_dir = resolve_docs_dir()
    docs_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(5):
        stored_name = f"{uuid.uuid4().hex}{extension}"
        path = docs_dir / stored_name
        try:
            with open(path, "xb") as handle:
                handle.write(data)
        except FileExistsError:
            continue
        return stored_name, path
    raise RuntimeError("Could not allocate a unique storage name.")


def delete_quietly(path: Path) -> None:
    """Remove a stored file, ignoring missing-file errors. Never logs content."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def stored_path(stored_name: str) -> Path:
    """Resolve a stored name to its path, rejecting any path traversal."""
    if "/" in stored_name or "\\" in stored_name or ".." in stored_name:
        raise InvalidDocumentError("Invalid stored name.")
    return resolve_docs_dir() / stored_name
