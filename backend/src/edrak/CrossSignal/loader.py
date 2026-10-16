from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from ..contracts.CrossSignal import (
    CrossSignalInput,
)
from .graph import run_cross_signal

# ============================================================
# Convert Verification Payload
# ============================================================

def cross_signal_input_from_payload(
    payload: dict[str, Any],
) -> CrossSignalInput:

    verification_metadata = dict(
        payload.get(
            "verification_metadata",
        )
        or {}
    )

    decision_status = (
        verification_metadata.get(
            "decision_status"
        )
    )

    if decision_status != "verified":

        raise ValueError(
            "Cross-Signal cannot run: verification did not "
            "complete successfully "
            f"(decision_status={decision_status!r})."
        )

    findings = [
        finding

        for finding
        in payload.get(
            "verified_findings",
            [],
        )

        if finding.get(
            "verification_status"
        ) == "verified"
    ]

    verification_metadata[
        "evidence"
    ] = payload.get(
        "evidence",
        [],
    )

    verification_metadata.setdefault(
        "source_stage",
        "verification",
    )

    verification_metadata[
        "verified_findings"
    ] = len(findings)

    return CrossSignalInput.model_validate(
        {
            "research_run_id": payload[
                "research_run_id"
            ],

            "business_request": payload[
                "business_request"
            ],

            "verified_findings": findings,

            "metadata": verification_metadata,
        }
    )


# ============================================================
# Load JSON
# ============================================================

def load_cross_signal_input(
    path: str | Path,
) -> CrossSignalInput:

    path = Path(path)

    payload = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    return cross_signal_input_from_payload(
        payload
    )


# ============================================================
# CLI
# ============================================================

async def main(
    path: str,
) -> None:

    cross_signal_input = (
        load_cross_signal_input(path)
    )

    output = await run_cross_signal(
        cross_signal_input
    )

    print(
        output.model_dump_json(
            indent=2
        )
    )


if __name__ == "__main__":

    if len(sys.argv) != 2:

        raise SystemExit(
            "Usage: python -m "
            "edrak.CrossSignal.loader "
            "<verification_result.json>"
        )

    asyncio.run(
        main(
            sys.argv[1]
        )
    )