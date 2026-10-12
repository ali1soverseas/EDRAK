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


def _card_line(card: dict[str, Any]) -> str:
    """One card as a sentence. Every number here was already rendered from the metrics table."""
    s = card.get("slots") or {}
    comp = card.get("component")
    if comp == "kpi":
        body = (f"{s['range']['low']} to {s['range']['high']} (sources disagree)" if s.get("range") else str(s.get("value", "")))
        if s.get("period"):
            body += f", {s['period']}"
        if s.get("comparator"):
            body += f"; {s['comparator']['value']} {s['comparator']['label']}"
    elif comp == "ladder":
        body = "; ".join(f"{r['label']} {r['value']}" for r in s.get("rows", []))
    elif comp == "segmented":
        body = ", ".join(f"{p['label']} {p['display']}" for p in s.get("parts", []))
    elif comp == "line":
        pts = s.get("points", [])
        body = f"{pts[0]['x']} {pts[0]['display']} → {pts[-1]['x']} {pts[-1]['display']}" if pts else ""
    elif comp == "timeline":
        body = "; ".join(f"{e.get('when') or ''} {e['label']}".strip() for e in s.get("events", []))
    elif comp == "text":
        body = "; ".join(f"{i['entity']} {i['text']}" for i in s.get("items", []))
    elif comp == "gap":
        body = f"not established. {s.get('explanation', '')}"
    else:
        body = "; ".join(f"{r['entity']} {r['value']}" for r in s.get("rows", []))
    refs = " ".join(f"[E{n}]" for n in card.get("refs") or [])
    caveat = f" Note: {card['caveat']}" if card.get("caveat") else ""
    return f"{card.get('title')}: {body} {refs} ({card.get('status')}).{caveat}".replace("  ", " ").strip()


def _plan_sections(plan: dict[str, Any]) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    opt = plan.get("chosen_option") or {}
    out.append((f"Action plan: option {opt.get('key', '')}. {opt.get('title', '')}", [
        f"Objective: {plan.get('objective', '')}", f"Why this option: {opt.get('why', '')}",
        *[f"Not chosen, option {a['key']} ({a.get('title', '')}): {a.get('why_not', '')}" for a in plan.get("alternatives_not_chosen", [])],
        f"Length: {plan.get('total_weeks', 0)} weeks. Review on {plan.get('review_date') or 'a date to be set'}."]))
    if plan.get("assumptions") or plan.get("open_questions"):
        out.append(("Assumptions and open questions", [*[f"Assumption: {a['text']}" for a in plan.get("assumptions", [])],
                                                       *[f"Open question: {q['text']}" for q in plan.get("open_questions", [])]]))
    for w in plan.get("workstreams", []):
        out.append((f"Workstream {w['id']}: {w['name']}", [
            f"{a['id']} (weeks {a['start_week']}-{a['start_week'] + a['duration_weeks'] - 1}, {a.get('owner_role') or 'owner to be set'}"
            f"{', after ' + ', '.join(a['depends_on']) if a.get('depends_on') else ''}): {a['title']}."
            f"{' Deliverable: ' + a['deliverable'] + '.' if a.get('deliverable') else ''}"
            f"{' Success: ' + a['success_measure'] + '.' if a.get('success_measure') else ''}" for a in w.get("actions", [])]))
    if plan.get("milestones"):
        out.append(("Milestones and decision gates", [f"Week {m['week']}: {m['title']}. Go if: {m.get('go_criteria') or '-'}. Stop if: {m.get('kill_criteria') or '-'}."
                                                       for m in plan["milestones"]]))
    if plan.get("kpis"):
        out.append(("KPIs", [f"{k['name']}{' (' + k['entity'] + ')' if k.get('entity') else ''}: baseline {k.get('baseline') or 'new measure'}; "
                             f"target {k.get('target') or 'to be set'} ({k.get('target_status')}); {k.get('cadence') or ''}".strip() for k in plan["kpis"]]))
    if plan.get("risks"):
        out.append(("Risk register", [f"{r['id']} ({r['likelihood']} likelihood, {r['impact']} impact): {r['text']} Early warning: {r.get('early_warning') or '-'}. "
                                      f"Mitigation: {r.get('mitigation') or '-'}{' (' + ', '.join(r['mitigation_action_ids']) + ')' if r.get('mitigation_action_ids') else ''}." for r in plan["risks"]]))
    if plan.get("opportunities"):
        out.append(("Opportunities to capture", [f"{o['text']} How: {o.get('how_to_capture') or '-'}" for o in plan["opportunities"]]))
    if plan.get("resources"):
        out.append(("Resources", list(plan["resources"])))
    return out


def _typed_sections(doc: dict[str, Any], include_sources: bool, include_limitations: bool) -> list[tuple[str, list[str]]]:
    cards = doc.get("cards") or {}
    out: list[tuple[str, list[str]]] = [("Headline", [doc.get("headline") or doc.get("summary_text") or ""])]
    kpis = doc.get("kpis") or []
    if kpis:
        out.append(("Key figures", [f"{(k.get('label') or {}).get('en', k.get('metric_id'))}{' · ' + k['entity'] if k.get('entity') else ''}: {k['value']} ({k['status']})" for k in kpis]))
    cov = doc.get("coverage") or {}
    if cov.get("required"):
        out.append(("Coverage", [f"{cov['met']} of {cov['required']} required figures are verified ({round((cov.get('score') or 0) * 100)}%)."]))
    if doc.get("signals_view"):
        out.append(("What this means", [f"{s.get('title')}: {s.get('signal') or ''}{' Why it matters: ' + s['why_it_matters'] if s.get('why_it_matters') else ''}"
                                         f"{' ' + ' '.join('[E%s]' % n for n in s.get('refs') or []) if s.get('refs') else ''}" for s in doc["signals_view"]]))
    for sec in doc.get("sections") or []:
        rows = [_card_line(cards[k]) for k in [*sec.get("cards", []), *sec.get("gap_cards", [])] if k in cards]
        if rows:
            out.append((sec.get("label") or sec.get("lens"), rows))
    if include_limitations and (doc.get("gaps_panel") or {}).get("gaps"):
        out.append(("Gaps", [f"{g['title']}: {g['explanation']}" for g in doc["gaps_panel"]["gaps"]]))
    if doc.get("options"):
        rows = []
        for o in doc["options"]:
            rows.append(f"{o['key']}. {o['title']}: {o.get('thesis') or o.get('description', '')}")
            rows += [f"   Opportunity: {x['text']}" for x in o.get("opportunities", [])] + [f"   Risk: {x['text']}" for x in o.get("risks", [])]
            if o.get("would_change_if"):
                rows.append(f"   Would change if: {o['would_change_if']}")
        out.append(("Options (not ranked)", rows))
    if doc.get("action_plan"):
        out += _plan_sections(doc["action_plan"])
    if include_sources and doc.get("evidence"):
        out.append(("Sources", [f"E{e.get('display_no')}: {(e.get('evidence') or {}).get('source_title') or 'Source'}"
                                f"{' - ' + e['evidence']['source_url'] if ((e.get('evidence') or {}).get('source_url') or '').startswith('http') else ''}" for e in doc["evidence"]]))
    return out


def _sections(doc: dict[str, Any], include_sources: bool, include_limitations: bool) -> list[tuple[str, list[str]]]:
    """The brief as (heading, paragraphs) sections, shared by every format."""
    if doc.get("cards") is not None:
        return _typed_sections(doc, include_sources, include_limitations)
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
