from __future__ import annotations

from pathlib import Path


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md", ".json", ".csv", ".log", ".py", ".js", ".ts", ".html", ".css"}:
            return path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".docx":
            from docx import Document

            doc = Document(str(path))
            return "\n".join(paragraph.text for paragraph in doc.paragraphs)
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        return f"[No se pudo extraer el texto: {exc}]"
    return ""
