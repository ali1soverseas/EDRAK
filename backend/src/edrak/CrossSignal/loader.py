"""Adapt the Verification stage's JSON payload into a CrossSignalInput.

The payload contains top-level `evidence` and `verification_metadata` keys that
CrossSignalInput does not declare, so they are folded into `metadata`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..contracts.CrossSignal import CrossSignalInput


def cross_signal_input_from_payload(payload: dict[str, Any]) -> CrossSignalInput:
    meta = dict(payload.get("verification_metadata") or {})

    if meta.get("decision_status") != "verified":
        raise ValueError(
            "Cross-Signal cannot run: verification did not complete successfully "
            f"(decision_status={meta.get('decision_status')!r})."
        )

    findings = [
        f
        for f in payload.get("verified_findings", [])
        if f.get("verification_status") == "verified"
    ]

    meta["evidence"] = payload.get("evidence", [])
    meta.setdefault("source_stage", "verification")
    meta["verified_findings"] = len(findings)

    return CrossSignalInput.model_validate(
        {
            "research_run_id": payload["research_run_id"],
            "business_request": payload["business_request"],
            "verified_findings": findings,
            "metadata": meta,
        }
    )


def load_cross_signal_input(path: str | Path) -> CrossSignalInput:
    return cross_signal_input_from_payload(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )


if __name__ == "__main__":
    import asyncio
    import sys

    from .graph import run_cross_signal

    async def main(path: str) -> None:
        out = await run_cross_signal(load_cross_signal_input(path))
        print(out.model_dump_json(indent=2))

    asyncio.run(main(sys.argv[1]))
