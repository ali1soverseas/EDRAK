"""Fill preference_tags on decisions recorded before tagging existed.

The stored rationale may be generated plan text, so it is not treated as the user's reason.
Options come from the locked brief snapshot, then from the current brief.
"""

from __future__ import annotations

import sqlite3

from edrak.DecisionAnalysis.personalization import tag_decision
from edrak.db.connection import get_db_path, init_db
from edrak.db.util import dumps, loads


def main() -> None:
    import asyncio

    asyncio.run(init_db())
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT d.id, d.option_label, d.brief_id, d.preference_tags, s.brief_content, b.document
           FROM decisions d
           JOIN briefs b ON b.id = d.brief_id
           LEFT JOIN evidence_snapshots s ON s.decision_id = d.id
           WHERE d.preference_tags IS NULL
              OR coalesce(json_extract(d.preference_tags, '$.version'), 0) < 2"""
    ).fetchall()
    updated = 0
    for row in rows:
        document = loads(row["brief_content"], None) or loads(row["document"], {}) or {}
        tags = tag_decision(document.get("options") or [], row["option_label"], None)
        previous = loads(row["preference_tags"], None)
        if not tags["option"] and not tags["reason"] and previous:
            continue
        conn.execute("UPDATE decisions SET preference_tags = ? WHERE id = ?", (dumps(tags), row["id"]))
        updated += 1
    conn.commit()
    conn.close()
    print(f"Tagged {updated} decision(s).")


if __name__ == "__main__":
    main()
