"""Challenge a card, or add a web finding to it.

The text goes back through extract and verify like any other source. Only a number that is really in
the supplied text is accepted. If it agrees with what the brief holds, the card gains a second source;
if it disagrees, the card shows both as a conflict. Either way a NEW card version is written and the
old one is kept. Web results never edit a card on their own.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import aiosqlite

from edrak.briefing.derive import derive, record_conflicts
from edrak.briefing.extract import extract_from_evidence
from edrak.briefing.fill import build_cards, slug
from edrak.briefing.index import index_card_version
from edrak.briefing.reconcile import CONFLICT_TOLERANCE
from edrak.contracts import Evidence
from edrak.contracts.evidence import SourceType
from edrak.db.repositories import cards as cards_repo
from edrak.db.repositories import metrics as metrics_repo
from edrak.db.util import dumps, fetch_one, new_id
from edrak.guardrails.content import sanitize
from edrak.guardrails.plan import check_free_text
from edrak.guardrails import patterns
from edrak.metrics.contracts import MetricScope
from edrak.metrics.registry import get_registry

logger = logging.getLogger(__name__)


class ChallengeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _agree(a: float, b: float) -> bool:
    top = max(abs(a), abs(b))
    return top == 0 or abs(a - b) / top <= CONFLICT_TOLERANCE


async def challenge_card(db: aiosqlite.Connection, *, brief_id: str, request_id: str, document: dict[str, Any], card_key: str, source: str,
                         text: str, entity: Optional[str] = None, url: Optional[str], title: Optional[str], user_id: Optional[str]) -> dict[str, Any]:
    card = (document.get("cards") or {}).get(card_key)
    if not card:
        raise ChallengeError("card_not_found", "That card does not exist.")
    metric_id = card.get("metric_id")
    uids = card.get("metric_uids") or []
    sole = {(document.get("metrics") or {}).get(u, {}).get("entity") for u in uids} - {None}
    entity = (entity or card.get("entity") or (next(iter(sole)) if len(sole) == 1 else "") or "").strip()
    if not entity:
        raise ChallengeError("entity_required", "This card covers several companies. Say which one the new source is about.")
    defn = get_registry().get(metric_id)
    if not defn or card.get("slots", {}).get("data_source"):
        raise ChallengeError("not_challengeable", "This card is built from a data series and cannot be challenged with text.")
    if check_free_text(text):
        raise ChallengeError("blocked", "That text looks like an instruction aimed at the AI, so it was not used.")
    if patterns.find_pii(text):
        raise ChallengeError("blocked", "The text contains personal data. Remove it and try again.")

    clean, _ = sanitize(text)
    clean = clean.strip()[:4000]
    challenge_id = new_id()
    await db.execute("INSERT INTO card_challenges (id, brief_id, card_key, source, text, url, title, created_by) VALUES (?,?,?,?,?,?,?,?)",
                     (challenge_id, brief_id, card_key, source, clean, url, title, user_id))
    evidence = Evidence(evidence_id="challenge", source_type=SourceType.WEB_PAGE if url else SourceType.OTHER, source_url=url,
                        source_title=title or ("Added by you" if source == "user" else None), extracted_fact=clean[:500], excerpt=clean)
    found = await asyncio.to_thread(extract_from_evidence, evidence, entity, metric_id)
    if not found:
        await db.execute("UPDATE card_challenges SET outcome = 'no_figure' WHERE id = ?", (challenge_id,))
        await db.commit()
        return {"changed": False, "reason": "no_figure", "message": f"No {defn.label()} figure was found in that text, so the card did not change."}

    existing = [r for r in await metrics_repo.list_metrics(db, request_id) if r["metric_id"] == metric_id and r["entity"].lower() == entity.lower()
                and r["status"] in ("verified", "thin", "conflict") and not r["derived"]]
    new_uids: list[str] = []
    conflict = False
    for obs in found:
        peers = [r for r in existing if (r["period"] or "").lower() == (obs.period or "").lower() and (r["label"] or "") == (obs.label or "")]
        disagree = any(not _agree(obs.value, r["value"]) for r in peers if r["value"] is not None)
        status = "conflict" if disagree else ("verified" if peers else "thin")
        obs = obs.model_copy(update={"scope": MetricScope.THIRD_PARTY})
        uid = await metrics_repo.insert_metric(db, request_id, obs, worker=None, status=status, method="challenge", commit=False)
        await db.execute("UPDATE metrics SET challenge_id = ? WHERE uid = ?", (challenge_id, uid))
        new_uids.append(uid)
        if disagree:
            conflict = True
            for r in peers:
                await db.execute("UPDATE metrics SET status = 'conflict' WHERE uid = ?", (r["uid"],))
        elif peers:
            for r in peers:
                if r["status"] == "thin":
                    await db.execute("UPDATE metrics SET status = 'verified' WHERE uid = ?", (r["uid"],))
    await db.commit()

    company = ((document.get("request") or {}).get("company_profile") or {}).get("name", "")
    await derive(db, request_id, company)
    await record_conflicts(db, request_id)

    rows = await metrics_repo.list_metrics(db, request_id)
    conflicts = await metrics_repo.list_conflicts(db, request_id)
    outcomes = await metrics_repo.list_outcomes(db, request_id)
    template = get_registry().template((document.get("template") or {}).get("key") or document.get("use_case") or "competitive_intelligence")
    cards, _ = build_cards(rows, conflicts, outcomes, [], template, company)
    wanted = card_key if not card_key.startswith("gap:") else f"{metric_id}:{slug(entity)}"
    fresh = next((c for c in cards if c["card_key"] == wanted or (c["metric_id"] == metric_id and c["card_key"].startswith(f"{metric_id}") and slug(entity) in c["card_key"])), None)
    if fresh is None:
        await db.execute("UPDATE card_challenges SET outcome = 'no_card' WHERE id = ?", (challenge_id,))
        await db.commit()
        return {"changed": False, "reason": "no_card", "message": "The figure was stored, but not enough data exists yet to draw this card."}
    fresh["card_key"] = card_key if not card_key.startswith("gap:") else fresh["card_key"]
    fresh["headline"] = fresh["title"]
    fresh["caveat"] = (fresh.get("caveat") or "") + (" " if fresh.get("caveat") else "") + (
        "Updated after a challenge: sources now disagree." if conflict else "Updated after you added a source.")
    fresh["refs"] = fresh.get("refs") or []
    if dumps(fresh["slots"]) == dumps(card.get("slots")) and fresh["status"] == card.get("status") and fresh["component"] == card.get("component"):
        await db.execute("UPDATE card_challenges SET outcome = 'nothing_new' WHERE id = ?", (challenge_id,))
        await db.commit()
        return {"changed": False, "reason": "nothing_new", "message": "That matches what the card already shows."}

    ver = await cards_repo.add_version(db, brief_id, fresh["card_key"], fresh, note=f"Challenge: {clean[:120]}", created_by=user_id)
    await db.execute("UPDATE card_challenges SET outcome = ? WHERE id = ?", ("conflict" if conflict else "added", challenge_id))
    await db.commit()
    await index_card_version(db, brief_id, fresh, ver["version"])
    return {"changed": True, "version": ver["version"], "conflict": conflict, "card": {**fresh, "version": ver["version"], "origin": "challenge"},
            "before": {"status": card.get("status"), "slots": card.get("slots")}, "after": {"status": fresh["status"], "slots": fresh["slots"]},
            "metric_uids": new_uids}
