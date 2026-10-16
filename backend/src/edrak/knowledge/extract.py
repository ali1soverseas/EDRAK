"""Turn an uploaded file into text the indexer can chunk.

Every format produces markdown-ish text whose `##` headings become chunk locators: a PDF gets one
heading per page, a spreadsheet one per sheet and block of rows. Tables also keep a small grid
preview for the file viewer.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Optional

ALLOWED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx", ".json")
TABLE_EXTENSIONS = (".csv", ".xlsx")
ROWS_PER_CHUNK = 40
PREVIEW_ROWS = 500

MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".json": "application/json",
}


class ExtractionError(ValueError):
    """The file could not be read. The message is shown next to "Failed to parse"."""


@dataclass
class Extracted:
    text: str
    kind: str = "document"                 # "document" | "table"
    page_count: Optional[int] = None
    row_count: Optional[int] = None
    sheets: list[dict[str, Any]] = field(default_factory=list)   # [{name, columns, rows}] for tables
    json_data: Any = None


def extension(filename: str) -> str:
    return PurePath(filename.lower()).suffix


def extract(filename: str, content: bytes) -> Extracted:
    ext = extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise ExtractionError(f"{ext or 'This file type'} is not supported.")
    if ext == ".pdf":
        return _pdf(content)
    if ext == ".docx":
        return _docx(content)
    if ext == ".csv":
        return _csv(content)
    if ext == ".xlsx":
        return _xlsx(content)
    if ext == ".json":
        return _json(content)
    return _text(content)


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1256", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _text(content: bytes) -> Extracted:
    text = _decode(content)
    return Extracted(text=text, row_count=len(text.splitlines()))


def _pdf(content: bytes) -> Extracted:
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001 - pypdf raises many types
        raise ExtractionError("This PDF could not be opened.") from exc
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise ExtractionError("This PDF is password protected.")
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError("This PDF is password protected.") from exc
    pages: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            body = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 - one bad page should not lose the file
            body = ""
        if body:
            pages.append(f"## Page {number}\n\n{body}")
    return Extracted(text="\n\n".join(pages), page_count=len(reader.pages))


def _docx(content: bytes) -> Extracted:
    import docx

    try:
        document = docx.Document(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError("This Word file could not be opened.") from exc
    lines: list[str] = []
    for p in document.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style = (p.style.name if p.style is not None else "").lower()
        if style.startswith("heading"):
            level = "".join(ch for ch in style if ch.isdigit()) or "2"
            lines.append("#" * min(int(level) + 1, 4) + " " + text)
        else:
            lines.append(text)
    for t_index, table in enumerate(document.tables, start=1):
        lines.append(f"## Table {t_index}")
        for row in table.rows:
            lines.append(" | ".join(cell.text.strip() for cell in row.cells))
    text = "\n\n".join(lines)
    words = len(text.split())
    return Extracted(text=text, page_count=max(1, round(words / 450)) if words else 0)


def _rows_to_text(sheet: str, columns: list[str], rows: list[list[Any]]) -> str:
    parts: list[str] = []
    for start in range(0, len(rows), ROWS_PER_CHUNK):
        block = rows[start : start + ROWS_PER_CHUNK]
        label = f"{sheet}, rows {start + 1}-{start + len(block)}" if sheet else f"Rows {start + 1}-{start + len(block)}"
        lines = [f"## {label}"]
        for row in block:
            pairs = [f"{c}: {v}" for c, v in zip(columns, row) if v not in (None, "")]
            if pairs:
                lines.append("; ".join(pairs))
        parts.append("\n\n".join(lines))
    return "\n\n".join(parts)


def _csv(content: bytes) -> Extracted:
    text = _decode(content)
    try:
        dialect = csv.Sniffer().sniff(text[:4096]) if text.strip() else csv.excel
    except csv.Error:
        dialect = csv.excel
    reader = list(csv.reader(io.StringIO(text), dialect))
    if not reader:
        return Extracted(text="", kind="table", row_count=0)
    columns = [c.strip() or f"Column {i + 1}" for i, c in enumerate(reader[0])]
    rows = [r for r in reader[1:] if any(cell.strip() for cell in r)]
    return Extracted(
        text=_rows_to_text("", columns, rows),
        kind="table",
        row_count=len(rows),
        sheets=[{"name": "Sheet 1", "columns": columns, "rows": rows[:PREVIEW_ROWS]}],
    )


def _xlsx(content: bytes) -> Extracted:
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError("This Excel file could not be opened.") from exc
    texts: list[str] = []
    sheets: list[dict[str, Any]] = []
    total = 0
    for ws in workbook.worksheets:
        values = [["" if v is None else v for v in row] for row in ws.iter_rows(values_only=True)]
        values = [r for r in values if any(str(v).strip() for v in r)]
        if not values:
            continue
        columns = [str(c).strip() or f"Column {i + 1}" for i, c in enumerate(values[0])]
        rows = [[str(v) for v in r] for r in values[1:]]
        total += len(rows)
        texts.append(_rows_to_text(ws.title, columns, rows))
        sheets.append({"name": ws.title, "columns": columns, "rows": rows[:PREVIEW_ROWS]})
    workbook.close()
    return Extracted(text="\n\n".join(texts), kind="table", row_count=total, sheets=sheets)


def _json(content: bytes) -> Extracted:
    text = _decode(content)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ExtractionError("This JSON file is not valid JSON.") from exc
    pretty = json.dumps(data, indent=2, ensure_ascii=False)
    return Extracted(text=pretty, json_data=data, row_count=len(pretty.splitlines()))


def sniff_matches_extension(filename: str, content: bytes) -> bool:
    """The file's first bytes agree with its extension (a renamed executable fails this)."""
    ext = extension(filename)
    head = content[:8]
    if ext == ".pdf":
        return head.startswith(b"%PDF")
    if ext in (".docx", ".xlsx"):
        return head.startswith(b"PK\x03\x04")
    if ext in (".txt", ".md", ".csv", ".json"):
        # Text formats: reject obvious binaries (executables, archives, NUL-heavy content).
        if head.startswith((b"MZ", b"\x7fELF", b"PK\x03\x04", b"%PDF", b"\xca\xfe\xba\xbe")):
            return False
        sample = content[:4096]
        return sample.count(b"\x00") <= len(sample) // 50 or sample.startswith((b"\xff\xfe", b"\xfe\xff"))
    return False
