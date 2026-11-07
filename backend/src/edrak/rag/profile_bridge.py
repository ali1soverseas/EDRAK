"""Profile & Research to RAG Bridge for EDRAK.

Enables company profile imports (from URL or PDF) and Company Research Agent results
(from web crawls, PDFs, and search) to be automatically indexed into the ChromaDB
vector store, making them searchable by the Internal Intelligence agent.
"""

import logging
from typing import Any, Dict, List, Optional

from edrak.rag.indexer import InternalIndexer

logger = logging.getLogger(__name__)


def format_facts_as_markdown(company_name: str, facts: List[Any]) -> Dict[str, str]:
    """Groups facts by category and formats each category as a structured Markdown document."""
    categories: Dict[str, List[Any]] = {}
    for f in facts:
        cat = getattr(f, "category", None) or (f.get("category") if isinstance(f, dict) else "general")
        categories.setdefault(str(cat), []).append(f)

    docs: Dict[str, str] = {}
    for cat, items in categories.items():
        title = f"{company_name} - {cat.replace('_', ' ').title()} Intelligence"
        lines = [
            f"# {title}",
            "",
            f"- **Company**: {company_name}",
            f"- **Category**: {cat}",
            f"- **Source**: Automated Company Research & Fact Extraction",
            "",
            "## Key Extracted Findings",
            "",
        ]
        for item in items:
            key = getattr(item, "key", None) or (item.get("key") if isinstance(item, dict) else "")
            val = getattr(item, "value", None) or (item.get("value") if isinstance(item, dict) else "")
            context = getattr(item, "context", None) or (item.get("context") if isinstance(item, dict) else "")
            source_url = getattr(item, "source_url", None) or (item.get("source_url") if isinstance(item, dict) else "")

            heading = key.replace("_", " ").title() if key else "Fact"
            lines.append(f"### {heading}")
            lines.append(f"**Value**: {val}")
            if context:
                lines.append(f"**Context**: {context}")
            if source_url:
                lines.append(f"**Source URL**: {source_url}")
            lines.append("")

        docs[cat] = "\n".join(lines)

    return docs


def index_company_profile(workspace_id: str, profile: Any) -> int:
    """Indexes a company profile draft or saved profile into RAG for this workspace."""
    indexer = InternalIndexer()

    name = getattr(profile, "name", None) or (profile.get("name") if isinstance(profile, dict) else "Company")
    aliases = getattr(profile, "aliases", None) or (profile.get("aliases") if isinstance(profile, dict) else [])
    products = getattr(profile, "products", None) or (profile.get("products") if isinstance(profile, dict) else [])
    notes = getattr(profile, "notes", None) or (profile.get("notes") if isinstance(profile, dict) else "")

    lines = [
        f"# {name} - Corporate Profile & Offering Summary",
        "",
        f"- **Company**: {name}",
        f"- **Aliases**: {', '.join(aliases) if aliases else 'None'}",
        "- **Source**: Company Profile Ingestion (URL/PDF)",
        "",
        "## Company Overview & Strategy",
        notes or "No notes provided.",
        "",
        "## Product & Service Offerings",
    ]
    for p in products:
        lines.append(f"- {p}")
    lines.append("")

    content = "\n".join(lines)
    source_uri = f"internal://profile/{workspace_id}/corporate_overview"

    chunks = indexer.index_document(
        content=content,
        source_uri=source_uri,
        title=f"{name} Corporate Profile",
        doc_type="internal_profile",
        is_synthetic=False,
        workspace_id=workspace_id,
    )
    logger.info("Indexed company profile for workspace %s into RAG (%d chunks).", workspace_id, chunks)
    return chunks


def index_company_facts(workspace_id: str, company_name: str, facts: List[Any]) -> int:
    """Indexes a list of CompanyFact objects or dictionaries into RAG."""
    if not facts:
        return 0

    indexer = InternalIndexer()
    docs = format_facts_as_markdown(company_name, facts)
    total_chunks = 0

    for cat, md_content in docs.items():
        source_uri = f"internal://research/{workspace_id}/{cat}"
        title = f"{company_name} - {cat.replace('_', ' ').title()}"
        chunks = indexer.index_document(
            content=md_content,
            source_uri=source_uri,
            title=title,
            doc_type="internal_research_fact",
            is_synthetic=False,
            workspace_id=workspace_id,
        )
        total_chunks += chunks

    logger.info("Indexed %d company facts into RAG for workspace %s (%d chunks across %d categories).",
                len(facts), workspace_id, total_chunks, len(docs))
    return total_chunks


async def sync_workspace_facts_to_rag(workspace_id: str, db_session: Any) -> int:
    """Queries all active facts for workspace_id from database and indexes them into RAG."""
    from edrak.db.repositories import company_facts as facts_repo
    from edrak.db.repositories import company_profiles as profile_repo

    profile = await profile_repo.get_profile(db_session, workspace_id)
    company_name = profile.name if profile else "Company"

    facts = await facts_repo.list_facts(db_session, workspace_id)
    if not facts:
        logger.info("No saved facts found for workspace %s to push to RAG.", workspace_id)
        return 0

    return index_company_facts(workspace_id, company_name, facts)

