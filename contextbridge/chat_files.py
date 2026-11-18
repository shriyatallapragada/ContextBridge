"""Local text and PDF import for Chat bridge; no OCR or model calls."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from contextbridge.chat_bridge import MAX_CHAT_CHARS

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_PAGE_CONTENT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ImportedChat:
    text: str
    notice: str


def read_chat_file(path: Path) -> ImportedChat:
    if path.suffix.lower() == ".pdf":
        return _read_pdf(path)
    if path.suffix.lower() not in (".txt", ".md"):
        raise ValueError("Choose a PDF, Markdown, or text file.")
    with path.open(encoding="utf-8-sig") as stream:
        text = stream.read(MAX_CHAT_CHARS + 1)
    if len(text) > MAX_CHAT_CHARS:
        raise ValueError("Choose a smaller excerpt (100,000 characters maximum).")
    if not text.strip():
        raise ValueError("This file has no text to import.")
    return ImportedChat(text, "Text imported. Review it, then prepare the handoff.")


def _read_pdf(path: Path) -> ImportedChat:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    if path.stat().st_size > MAX_PDF_BYTES:
        raise ValueError("Choose a PDF smaller than 20 MB.")
    try:
        with path.open("rb") as stream:
            reader = PdfReader(stream)
            if reader.is_encrypted:
                raise ValueError(
                    "This PDF is password protected. Save an unlocked copy and import it."
                )
            total = len(reader.pages)
            if total > MAX_PDF_PAGES:
                raise ValueError("Choose a PDF with at most 100 pages.")
            parts: list[str] = []
            size = 0
            empty = 0
            for number, page in enumerate(reader.pages, start=1):
                contents = page.get_contents()
                if contents is not None and len(contents.get_data()) > MAX_PAGE_CONTENT_BYTES:
                    raise ValueError(
                        "A PDF page is too complex to import. Export a simpler text PDF."
                    )
                text = (page.extract_text() or "").replace("\t", " ")
                if not text.strip():
                    empty += 1
                    continue
                part = f"[PDF page {number}]\n{text.strip()}"
                size += len(part) + (2 if parts else 0)
                if size > MAX_CHAT_CHARS:
                    raise ValueError(
                        "This PDF contains too much text. "
                        "Choose fewer pages (100,000 characters maximum)."
                    )
                parts.append(part)
    except (PyPdfError, NotImplementedError) as exc:
        raise ValueError("Could not read this PDF. Try saving it again as a text PDF.") from exc
    if not parts:
        raise ValueError(
            "No selectable text found. This may be a scanned PDF. OCR is not included; "
            "import a text-based PDF or paste the conversation instead."
        )
    notice = (
        f"Imported text from {total - empty} of {total} PDF pages. "
        "Check speaker labels and reading order before preparing the handoff."
    )
    if empty:
        notice += (
            f" {empty} page(s) had no extractable text and were skipped; "
            "images and scans are not imported."
        )
    return ImportedChat("\n\n".join(parts), notice)
