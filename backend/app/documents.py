"""Upload validation and real PDF/DOCX text extraction."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from .config import settings

ALLOWED_TYPES: dict[str, bytes] = {
    ".pdf": b"%PDF",
    ".docx": b"PK\x03\x04",
}
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_DOCX_ENTRIES = 2000
MAX_EXTRACTED_CHARACTERS = 5_000_000


def validate_upload(filename: str, data: bytes) -> str:
    """Return the normalised extension or raise ValueError."""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_TYPES:
        raise ValueError("Only PDF and DOCX files are accepted.")
    if not data:
        raise ValueError("The uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise ValueError("The file exceeds the upload size limit.")
    if not data.startswith(ALLOWED_TYPES[suffix]):
        raise ValueError("File content does not match its extension.")
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_DOCX_ENTRIES:
                    raise ValueError("The DOCX contains too many archive entries.")
                expanded_size = sum(entry.file_size for entry in entries)
                if expanded_size > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise ValueError("The DOCX expands beyond the safe processing limit.")
                if "word/document.xml" not in archive.namelist():
                    raise ValueError("The DOCX file is missing its document content.")
                if any(
                    entry.file_size > max(1_048_576, entry.compress_size * 200)
                    for entry in entries
                ):
                    raise ValueError("The DOCX compression ratio exceeds the safe limit.")
        except zipfile.BadZipFile as exc:
            raise ValueError("The DOCX archive is invalid.") from exc
    return suffix


def extract_text(path: Path, suffix: str) -> tuple[str, int]:
    """Extract page-tagged plain text. Returns (text, page_count)."""
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        if len(reader.pages) > 2000:
            raise ValueError("The PDF contains too many pages to process safely.")
        parts: list[str] = []
        for number, page in enumerate(reader.pages, start=1):
            parts.append(f"\n\n[Page {number}]\n{page.extract_text() or ''}")
        text = "".join(parts)
        if len(text) > MAX_EXTRACTED_CHARACTERS:
            raise ValueError("The PDF text exceeds the safe processing limit.")
        return text, len(reader.pages)

    document = Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    text = "\n".join(parts)
    if len(text) > MAX_EXTRACTED_CHARACTERS:
        raise ValueError("The DOCX text exceeds the safe processing limit.")
    return text, 0
