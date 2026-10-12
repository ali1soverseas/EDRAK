"""Download a brief as Markdown, Word or PDF.

Markdown and Word are built here. PDF renders print HTML with headless Chromium (Playwright), so the
layout and Arabic right-to-left text come out as on screen. Install it once with
`pip install playwright && playwright install chromium`.
"""

from __future__ import annotations

import html
import io
from typing import Any

WORKER_LABELS = {
    "internal_intelligence": "Internal",
    "competitor_intelligence": "Competitors",
    "market_intelligence": "Market",
    "customer_trends": "Customers and trends",
}


class ExportUnavailable(RuntimeError):
    pass


def _seg_text(segments: Any) -> str:
    if isinstance(segments, list):
        return "".join(s.get("text", "") for s in segments if isinstance(s, dict)).strip()
    return str(segments or "").strip()


def _sections(doc: dict[str, Any], include_sources: bool, include_limitations: bool) -> list[tuple[str, list[str]]]:
    """The brief as (heading, paragraphs) sections, shared by every format."""
    summary = doc.get("summary") or {}
    out: list[tuple[str, list[str]]] = []
    out.append(("Summary", [p for p in (
        _seg_text(summary.get("found")), _seg_text(summary.get("matters")), _seg_text(summary.get("options"))
    ) if p]))
    for lens in doc.get("lenses") or []:
        rows = []
        for item in lens.get("findings") or []:
            f = item.get("finding") or {}
            refs = " ".join(f"[E{r['display_no']}]" for r in item.get("refs") or [])
            status = item.get("status") or "unchecked"
            rows.append(f"{f.get('statement', '')} {refs} ({status})".strip())
        if rows:
            out.append((f"Findings: {WORKER_LABELS.get(lens.get('worker'), lens.get('worker'))}", rows))
    comp = doc.get("comparison") or {}
    if comp.get("dimensions"):
        rows = []
        for d in comp["dimensions"]:
            cells = "; ".join(f"{c['competitor']}: {c.get('relative_to_target', '').replace('_', ' ')}" for c in d.get("cells", []))
            rows.append(f"{d.get('dimension')}: {cells}")
        out.append(("Alternatives compared", rows))
    if doc.get("options"):
        out.append(("Options (not ranked)", [f"{o['key']}. {o['title']}: {o.get('description', '')}" for o in doc["options"]]))
    if doc.get("opportunities"):
        out.append(("Opportunities", [o["title"] for o in doc["opportunities"]]))
    if doc.get("risks"):
        out.append(("Risks", [r["title"] for r in doc["risks"]]))
    if include_limitations and doc.get("gaps"):
        out.append(("Information gaps and limitations", [g.get("text", "") for g in doc["gaps"]]))
    if doc.get("next_steps"):
        out.append(("Next steps", list(doc["next_steps"])))
    if include_sources and doc.get("evidence"):
        rows = []
        for e in doc["evidence"]:
            ev = e.get("evidence") or {}
            rows.append(f"E{e.get('display_no')}: {ev.get('source_title') or ev.get('publisher') or 'Source'}"
                        f"{' - ' + ev['source_url'] if (ev.get('source_url') or '').startswith('http') else ''}")
        out.append(("Sources", rows))
    return out


def _title(doc: dict[str, Any]) -> str:
    return f"Brief {int(doc.get('brief_no') or 0):04d}: {doc.get('title') or doc.get('question') or ''}"


def to_markdown(doc: dict[str, Any], *, include_sources: bool = True, include_limitations: bool = True) -> bytes:
    lines = [f"# {_title(doc)}", "", f"Evidence status: {doc.get('evidence_status', '')}", ""]
    for heading, rows in _sections(doc, include_sources, include_limitations):
        lines += [f"## {heading}", ""] + [f"- {r}" for r in rows] + [""]
    lines.append("_Edrak informs decisions. People make them._")
    return "\n".join(lines).encode("utf-8")


def to_docx(doc: dict[str, Any], *, include_sources: bool = True, include_limitations: bool = True) -> bytes:
    import docx

    d = docx.Document()
    d.add_heading(_title(doc), level=0)
    d.add_paragraph(f"Evidence status: {doc.get('evidence_status', '')}")
    for heading, rows in _sections(doc, include_sources, include_limitations):
        d.add_heading(heading, level=1)
        for r in rows:
            d.add_paragraph(r, style="List Bullet")
    d.add_paragraph("Edrak informs decisions. People make them.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def to_html(doc: dict[str, Any], *, include_sources: bool = True, include_limitations: bool = True) -> str:
    rtl = doc.get("language") == "ar"
    parts = [
        f'<!doctype html><html lang="{"ar" if rtl else "en"}" dir="{"rtl" if rtl else "ltr"}"><head><meta charset="utf-8">',
        "<style>body{font-family:'Instrument Sans','IBM Plex Sans Arabic',system-ui,sans-serif;color:#0b0f3b;margin:40px;line-height:1.5}"
        "h1{font-size:26px}h2{font-size:17px;margin-top:26px;border-bottom:1px solid #ddd;padding-bottom:4px}"
        "li{margin:4px 0}.muted{color:#666;font-size:12px}</style></head><body>",
        f"<h1>{html.escape(_title(doc))}</h1><p class='muted'>Evidence status: {html.escape(str(doc.get('evidence_status', '')))}</p>",
    ]
    for heading, rows in _sections(doc, include_sources, include_limitations):
        parts.append(f"<h2>{html.escape(heading)}</h2><ul>" + "".join(f"<li>{html.escape(r)}</li>" for r in rows) + "</ul>")
    parts.append("<p class='muted'>Edrak informs decisions. People make them.</p></body></html>")
    return "".join(parts)


async def to_pdf(doc: dict[str, Any], *, include_sources: bool = True, include_limitations: bool = True) -> bytes:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ExportUnavailable("PDF export needs Playwright: pip install playwright && playwright install chromium") from exc
    page_html = to_html(doc, include_sources=include_sources, include_limitations=include_limitations)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(page_html, wait_until="load")
            return await page.pdf(format="A4", print_background=True,
                                  margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"})
        finally:
            await browser.close()
