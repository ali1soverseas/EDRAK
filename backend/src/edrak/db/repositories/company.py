"""The company profile: the baseline every analysis compares against.

The profile is spread over company_profiles (scalar fields), company_markets (markets as
watchlist entities), company_links (website and social accounts) and tracked competitor
entities. get_company returns all of it as one dict; save_company updates only the keys given,
so onboarding can save one step at a time.
"""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from edrak.db.repositories.entities import (
    add_entity_link,
    entity_links,
    normalise_url,
    set_tracked,
    upsert_entity,
)
from edrak.db.util import dumps, fetch_all, fetch_one, loads, new_id, now_iso

SOCIAL_PLATFORMS = ("website", "facebook", "instagram", "x", "youtube", "linkedin", "tiktok")
TOTAL_ONBOARDING_STEPS = 8


INDUSTRY_LABELS = {
    "fintech": "Fintech", "saas": "SaaS", "retail": "Retail", "technology": "Technology", "healthcare": "Healthcare",
    "health": "Healthcare", "manufacturing": "Manufacturing", "logistics": "Logistics", "education": "Education", "other": "Other",
}


def as_list(raw: Any) -> list[str]:
    """A column that used to hold one value and now holds a JSON list. Reads both."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return _clean_list(raw)
    text = str(raw).strip()
    if text.startswith("["):
        return _clean_list(loads(text, []))
    # Older profiles typed one sentence: "SMEs in Egypt, freelancers". Each part is a group.
    return _clean_list([part for part in text.replace(";", ",").split(",")])


def industry_text(company: dict[str, Any]) -> str:
    """The industries in words, for search queries: "Fintech, SaaS" (with the free text for Other)."""
    names = [INDUSTRY_LABELS.get(i, i) for i in company.get("industries") or [] if i != "other"]
    if company.get("industry_other"):
        names.append(company["industry_other"])
    return ", ".join(names)


def _clean_list(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [v for v in values.replace("\r", "").split("\n")]
    seen: dict[str, None] = {}
    for v in values:
        text = str(v).strip()
        if text and text.lower() not in {s.lower() for s in seen}:
            seen[text] = None
    return list(seen)


async def get_company(db: aiosqlite.Connection, workspace_id: str) -> dict[str, Any]:
    row = await fetch_one(db, "SELECT * FROM company_profiles WHERE workspace_id = ?", (workspace_id,))
    if row is None:
        await db.execute("INSERT OR IGNORE INTO company_profiles (workspace_id) VALUES (?)", (workspace_id,))
        await db.commit()
        row = await fetch_one(db, "SELECT * FROM company_profiles WHERE workspace_id = ?", (workspace_id,))
    assert row is not None

    markets = await fetch_all(
        db,
        """
        SELECT e.id AS entity_id, e.name, cm.relation
        FROM company_markets cm JOIN entities e ON e.id = cm.entity_id
        WHERE cm.workspace_id = ? ORDER BY cm.relation DESC, lower(e.name)
        """,
        (workspace_id,),
    )
    links = await fetch_all(
        db,
        "SELECT id, platform, url, position FROM company_links WHERE workspace_id = ? ORDER BY position, created_at",
        (workspace_id,),
    )
    competitors = await fetch_all(
        db,
        """
        SELECT id AS entity_id, name, website_domain, discovered_by, discovery_reason, discovery_confidence
        FROM entities WHERE workspace_id = ? AND kind = 'competitor' AND is_tracked = 1
        ORDER BY created_at
        """,
        (workspace_id,),
    )
    comp_links = await entity_links(db, [c["entity_id"] for c in competitors])
    for c in competitors:
        c["links"] = comp_links.get(c["entity_id"], [])

    website = next((l["url"] for l in links if l["platform"] == "website"), "")
    return {
        "name": row["name"] or "",
        "aliases": loads(row["aliases"], []),
        "industries": [("healthcare" if i == "health" else i) for i in as_list(row["industry"])],
        "industry": (as_list(row["industry"]) or [None])[0],  # the first one, for older readers
        "industry_other": row["industry_other"],
        "description": row["notes"] or "",
        "offerings": loads(row["products"], []),
        "target_customer_groups": as_list(row["target_customers"]),
        "target_customers": ", ".join(as_list(row["target_customers"])),
        "goals": loads(row["goals"], []),
        "markets": markets,
        "website": website,
        "links": links,
        "competitors": competitors,
        "onboarding_step": int(row["onboarding_step"] or 1),
        "completed": row["completed_at"] is not None,
        "completed_at": row["completed_at"],
        "updated_at": row["updated_at"],
    }


async def save_company(
    db: aiosqlite.Connection,
    workspace_id: str,
    fields: dict[str, Any],
    *,
    user_id: Optional[str] = None,
    commit: bool = True,
) -> None:
    """Update the keys present in `fields`. Unknown keys are ignored."""
    sets: list[str] = []
    params: list[Any] = []

    def put(column: str, value: Any) -> None:
        sets.append(f"{column} = ?")
        params.append(value)

    if "name" in fields:
        put("name", (fields["name"] or "").strip())
    if "aliases" in fields:
        put("aliases", dumps(_clean_list(fields["aliases"])))
    if "offerings" in fields:
        put("products", dumps(_clean_list(fields["offerings"])))
    if "description" in fields:
        put("notes", (fields["description"] or "").strip() or None)
    if "target_customer_groups" in fields:
        groups = _clean_list(fields["target_customer_groups"])
        put("target_customers", dumps(groups) if groups else None)
    elif "target_customers" in fields:
        put("target_customers", (fields["target_customers"] or "").strip() or None)
    if "industries" in fields:
        chosen = _clean_list(fields["industries"])
        put("industry", dumps(chosen) if chosen else None)
    elif "industry" in fields:
        put("industry", fields["industry"] or None)
    if "industry_other" in fields:
        put("industry_other", (fields["industry_other"] or "").strip() or None)
    if "goals" in fields:
        put("goals", dumps(_clean_list(fields["goals"])))
    if "onboarding_step" in fields and fields["onboarding_step"]:
        put("onboarding_step", max(1, min(TOTAL_ONBOARDING_STEPS, int(fields["onboarding_step"]))))
    if sets:
        params.append(workspace_id)
        await db.execute(f"UPDATE company_profiles SET {', '.join(sets)} WHERE workspace_id = ?", params)

    if "markets" in fields:
        await _replace_markets(db, workspace_id, fields["markets"], user_id)
    if "links" in fields or "website" in fields:
        if "links" in fields:
            links = [l for l in (fields["links"] or []) if isinstance(l, dict)]
        else:
            # Only the website changed: keep the social accounts as they are.
            links = await fetch_all(
                db, "SELECT platform, url FROM company_links WHERE workspace_id = ? AND platform <> 'website' ORDER BY position",
                (workspace_id,),
            )
        if "website" in fields:
            links = [l for l in links if l.get("platform") != "website"]
            if (fields["website"] or "").strip():
                links.insert(0, {"platform": "website", "url": fields["website"]})
        await _replace_links(db, workspace_id, links)
    if "competitors" in fields:
        await _replace_competitors(db, workspace_id, fields["competitors"], user_id)

    if commit:
        await db.commit()


async def mark_completed(db: aiosqlite.Connection, workspace_id: str) -> None:
    await db.execute(
        "UPDATE company_profiles SET completed_at = coalesce(completed_at, ?), onboarding_step = ? WHERE workspace_id = ?",
        (now_iso(), TOTAL_ONBOARDING_STEPS, workspace_id),
    )
    await db.commit()


async def _replace_markets(db: aiosqlite.Connection, workspace_id: str, markets: Any, user_id: Optional[str]) -> None:
    wanted: list[tuple[str, str]] = []
    for m in markets or []:
        if isinstance(m, str):
            name, relation = m, "serves"
        else:
            name, relation = m.get("name", ""), m.get("relation") or "serves"
        name = (name or "").strip()
        if name and relation in ("serves", "considers") and name.lower() not in {w[0].lower() for w in wanted}:
            wanted.append((name, relation))
    await db.execute("DELETE FROM company_markets WHERE workspace_id = ?", (workspace_id,))
    for name, relation in wanted:
        entity = await upsert_entity(db, workspace_id, kind="market", name=name, created_by=user_id)
        await db.execute(
            "INSERT OR REPLACE INTO company_markets (id, workspace_id, entity_id, relation) VALUES (?, ?, ?, ?)",
            (new_id(), workspace_id, entity["id"], relation),
        )


async def _replace_links(db: aiosqlite.Connection, workspace_id: str, links: list[Any]) -> None:
    await db.execute("DELETE FROM company_links WHERE workspace_id = ?", (workspace_id,))
    seen: set[str] = set()
    has_website = False
    for position, link in enumerate(links or []):
        platform = link.get("platform") if isinstance(link, dict) else None
        url = normalise_url(link.get("url") if isinstance(link, dict) else None)
        if platform not in SOCIAL_PLATFORMS or not url or url.lower() in seen:
            continue
        if platform == "website":
            if has_website:
                continue
            has_website = True
        seen.add(url.lower())
        await db.execute(
            "INSERT INTO company_links (id, workspace_id, platform, url, position) VALUES (?, ?, ?, ?, ?)",
            (new_id(), workspace_id, platform, url, position),
        )


async def _replace_competitors(db: aiosqlite.Connection, workspace_id: str, competitors: Any, user_id: Optional[str]) -> None:
    keep: set[str] = set()
    for c in competitors or []:
        if isinstance(c, str):
            c = {"name": c}
        name = (c.get("name") or "").strip()
        if not name:
            continue
        entity = await upsert_entity(
            db,
            workspace_id,
            kind="competitor",
            name=name,
            created_by=user_id,
            website=c.get("website") or None,
            discovered_by=c.get("discovered_by") or "user",
            discovery_reason=c.get("discovery_reason"),
            discovery_confidence=c.get("discovery_confidence"),
        )
        keep.add(entity["id"])
        for link in c.get("links") or []:
            await add_entity_link(
                db, entity["id"], link.get("platform", ""), link.get("url", ""),
                verified=bool(link.get("verified")), found_on_url=link.get("found_on_url"),
            )
    tracked = await fetch_all(
        db, "SELECT id FROM entities WHERE workspace_id = ? AND kind = 'competitor' AND is_tracked = 1", (workspace_id,)
    )
    for row in tracked:
        if row["id"] not in keep:
            await set_tracked(db, workspace_id, row["id"], False)


def profile_contract_fields(company: dict[str, Any]) -> dict[str, Any]:
    """The contract's CompanyProfile, plus the website and social accounts the workers use."""
    socials = [
        {"platform": l["platform"], "url": l["url"]} for l in company.get("links", []) if l["platform"] != "website"
    ]
    return {
        "name": company.get("name") or "Unnamed company",
        "aliases": company.get("aliases") or [],
        "products": company.get("offerings") or [],
        "notes": company.get("description") or None,
        "website": company.get("website") or None,
        "social_accounts": socials,
    }
