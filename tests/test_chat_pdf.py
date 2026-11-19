from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from PySide6.QtWidgets import QApplication

from contextbridge.chat_files import read_chat_file
from contextbridge.gui.chat_pdf import save_chat_pdf


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_pdf_roundtrip_pagination_and_mixed_pages(app, tmp_path: Path) -> None:
    path = tmp_path / "chat.pdf"
    text = "# Conversation handoff\n\n## Goal\nContinue my project.\n\n"
    text += "\n\n".join(
        f"User turn {i}: Keep this detail for the next assistant." for i in range(100)
    )
    save_chat_pdf(path, text)
    reader = PdfReader(path)
    assert len(reader.pages) > 1
    extracted = read_chat_file(path)
    assert "User turn 0" in extracted.text and "User turn 99" in extracted.text
    assert "Continue my project" in extracted.text
    mixed = PdfWriter()
    mixed.append(reader)
    mixed.add_blank_page(width=595, height=842)
    mixed_path = tmp_path / "mixed.pdf"
    mixed.write(mixed_path)
    assert "1 page(s)" in read_chat_file(mixed_path).notice


def test_scanned_encrypted_invalid_and_page_limit(tmp_path: Path) -> None:
    path = tmp_path / "input.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(path)
    with pytest.raises(ValueError, match="No selectable text"):
        read_chat_file(path)
    writer.encrypt("private")
    writer.write(path)
    with pytest.raises(ValueError, match="password protected"):
        read_chat_file(path)
    path.write_bytes(b"invalid PDF")
    with pytest.raises(ValueError, match="Could not read"):
        read_chat_file(path)
    writer = PdfWriter()
    for _ in range(101):
        writer.add_blank_page(width=595, height=842)
    writer.write(path)
    with pytest.raises(ValueError, match="100 pages"):
        read_chat_file(path)


def test_failed_pdf_export_preserves_existing_file(app, tmp_path: Path) -> None:
    path = tmp_path / "keep.pdf"
    path.write_bytes(b"original")
    with pytest.raises(ValueError):
        save_chat_pdf(path, "")
    assert path.read_bytes() == b"original"
    with pytest.raises(OSError):
        save_chat_pdf(tmp_path / "missing" / "x.pdf", "hello")


def test_text_import_unchanged_and_empty_file_rejected(tmp_path: Path) -> None:
    path = tmp_path / "chat.txt"
    path.write_text("User: Hello\nAssistant: Hi", encoding="utf-8-sig")
    assert read_chat_file(path).text.startswith("User: Hello")
    path.write_text("")
    with pytest.raises(ValueError, match="no text"):
        read_chat_file(path)
