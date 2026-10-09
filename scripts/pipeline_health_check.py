"""Report whether a pipeline run is actually healthy, not merely successful.

A run exits 0 and reports ``completed`` even when a worker returns nothing.
``finalize_node`` derives the status from whether a worker *failed* or an error
was raised, and a worker that collected no evidence comes back ``partial``
rather than ``failed``. Exit code alone therefore hides a broken worker, which
is how customer-trends could contribute nothing for several runs unnoticed.

This reads the run artifact and prints one line per check, then the gaps each
worker reported. It changes nothing; it only reports.

    python scripts/pipeline_health_check.py                      # newest artifact
    python scripts/pipeline_health_check.py path/to/run.json     # a specific run

Exit code is 0 when every check passes and 1 when any fails, so it can gate a
run in CI. Use --no-strict to always exit 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "runs"

EXPECTED_WORKERS = (
    "internal_intelligence",
    "competitor_intelligence",
    "market_intelligence",
    "customer_trends",
)


def newest_artifact() -> Path | None:
    if not ARTIFACTS.is_dir():
        return None
    runs = sorted(ARTIFACTS.glob("*.json"), key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in payload.get("results") or [] if isinstance(r, dict)]


def check(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return (severity, label, detail) for every check.

    FAIL means something is broken: a missing worker, a worker that produced
    nothing, an unresolved citation. WARN is a worker that reported partial
    status, which is the honest outcome when it has real gaps and is not by
    itself a fault.
    """
    checks: list[tuple[str, str, str]] = []
    results = _results(payload)
    by_worker = {str(r.get("worker")): r for r in results}

    status = str(payload.get("status"))
    checks.append(("FAIL" if status != "completed" else "PASS", "run status is completed", status))

    present = set(by_worker)
    missing = [w for w in EXPECTED_WORKERS if w not in present]
    checks.append(
        (
            "FAIL" if missing else "PASS",
            "every worker is present in the results",
            f"{len(present & set(EXPECTED_WORKERS))}/{len(EXPECTED_WORKERS)} present"
            + (f", missing {missing}" if missing else ""),
        )
    )

    # The check that the exit code cannot make. A worker that found nothing
    # still counts as having run.
    empty = [w for w in EXPECTED_WORKERS if w in by_worker and not (by_worker[w].get("findings") or [])]
    detail = ", ".join(
        f"{w}: {len(by_worker[w].get('findings') or [])} findings"
        for w in EXPECTED_WORKERS
        if w in by_worker
    )
    checks.append(
        ("FAIL" if empty else "PASS", "every worker produced a finding", detail)
    )

    not_completed = [
        w for w in EXPECTED_WORKERS if w in by_worker and by_worker[w].get("status") != "completed"
    ]
    checks.append(
        (
            "WARN" if not_completed else "PASS",
            "every worker finished as completed",
            ", ".join(f"{w}={by_worker[w].get('status')}" for w in not_completed) or "all completed",
        )
    )

    total_refs = dangling = 0
    for result in results:
        known = {e.get("evidence_id") for e in (result.get("evidence") or [])}
        for finding in result.get("findings") or []:
            for ref in finding.get("evidence_refs") or []:
                total_refs += 1
                if isinstance(ref, dict) and ref.get("evidence_id") not in known:
                    dangling += 1
    checks.append(
        (
            "FAIL" if dangling else "PASS",
            "every evidence reference resolves",
            f"{total_refs - dangling}/{total_refs} resolve",
        )
    )

    cross = payload.get("cross_signal") or {}
    cross_status = str(cross.get("status")) if isinstance(cross, dict) else "absent"
    signals = len(cross.get("signals") or []) if isinstance(cross, dict) else 0
    checks.append(
        (
            "FAIL" if cross_status != "completed" else "PASS",
            "cross-signal completed",
            f"{cross_status}, {signals} signals",
        )
    )

    return checks


def report(payload: dict[str, Any]) -> bool:
    checks = check(payload)
    width = max(len(label) for _, label, _ in checks)
    print(f"run {payload.get('request_id')}")
    print("-" * (width + 34))
    for severity, label, detail in checks:
        print(f"  {severity:<4}  {label:<{width}}  {detail}")
    print("-" * (width + 34))

    results = _results(payload)
    total_findings = sum(len(r.get("findings") or []) for r in results)
    total_evidence = sum(len(r.get("evidence") or []) for r in results)
    print(
        f"  totals: {len(results)} workers, {total_findings} findings, "
        f"{total_evidence} evidence"
    )

    for result in results:
        gaps = result.get("gaps") or []
        if gaps:
            print(f"\n  gaps reported by {result.get('worker')}:")
            for gap in gaps:
                print(f"    - {str(gap)[:150]}")
    return all(severity != "FAIL" for severity, _, _ in checks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifact",
        nargs="?",
        help="run artifact to inspect; defaults to the most recent one",
    )
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="always exit 0, even when a check fails",
    )
    args = parser.parse_args(argv)

    path = Path(args.artifact) if args.artifact else newest_artifact()
    if path is None or not path.is_file():
        print(
            f"no run artifact found in {ARTIFACTS}; run scripts/run_pipeline.py first",
            file=sys.stderr,
        )
        return 2

    payload = json.loads(path.read_text(encoding="utf-8"))
    healthy = report(payload)
    if args.no_strict:
        return 0
    return 0 if healthy else 1

if __name__ == "__main__":
    raise SystemExit(main())
