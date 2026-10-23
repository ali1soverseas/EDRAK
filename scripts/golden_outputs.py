"""Ground-truth outputs for the golden dataset, stored in the same layout and schema as a run bundle.

    golden/ground_truth/<name>/
        context/plan.json                       orchestrator plan
        inputs/initial_input.json               the query
        inputs/worker_<worker>.json             final attempt of each worker only
        inputs/verification_pass_001.json       final verification pass only
        inputs/cross_signal.json
        inputs/decision_analysis.json

Seed it from a run bundle, review/edit it, then set "curated" to true in ground_truth.json:
    python scripts/golden_outputs.py capture <name> artifacts/evaluation_runs/<id>

Compare a fresh EDRAK bundle with it (per-worker findings):
    python scripts/golden_outputs.py compare <name> artifacts/evaluation_runs/<id>

Fields about what is missing, uncertain or weak (confidence, status, gaps, conflicts, contradictions, missing
information, limitations, warnings, evidence quality) are removed, and compare never uses them.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "golden" / "ground_truth"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _latest_workers(inputs: Path) -> dict[str, tuple[Path, dict]]:
    latest: dict[str, tuple[Path, dict]] = {}
    for path in sorted(inputs.glob("worker_*.json")):
        data = _load(path)
        worker = data.get("worker")
        rank = (int(data.get("attempt") or 1), str(data.get("completed_at") or ""))
        if worker and (worker not in latest or rank >= (int(latest[worker][1].get("attempt") or 1), str(latest[worker][1].get("completed_at") or ""))):
            latest[worker] = (path, data)
    return latest


def _trusted(ver_findings: list[dict]) -> set[str]:
    """Statements verification confirmed with medium or high evidence; only these can be ground truth."""
    return {
        f.get("statement") for f in ver_findings
        if f.get("verification_status") == "verified" and f.get("evidence_quality") in ("medium", "high")
    }


# Ground truth describes what is known, never what is missing, uncertain or weak: these keys are removed everywhere.
DROP_KEYS = {
    "confidence", "status", "error", "gaps", "conflicts", "contradictions", "missing_information",
    "limitations", "evidence_gaps", "warnings", "failures", "assumptions", "key_uncertainties",
    "unsupported_questions", "blocking_gaps", "additional_information_needed", "next_research_targets",
    "evidence_quality", "evidence_refs", "evidence", "targeted_actions", "control_summary", "metric_context",
    "insufficient_count", "discarded_missing_support", "discarded_insufficient_support",
    "unresolved_support_references", "support_references_unresolved",
}


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in DROP_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _worker(data: dict, trusted: set[str]) -> dict:
    out = dict(data)
    out["findings"] = [f for f in data.get("findings", []) if f.get("statement") in trusted]
    out["metadata"] = {}
    return _strip(out)


def _verification(data: dict, trusted: set[str]) -> dict:
    out = dict(data)
    out["findings"] = [f for f in data.get("findings", []) if f.get("statement") in trusted]
    out["decision"] = {"summary": f"{len(out['findings'])} findings verified."}
    out["metadata"] = {**(data.get("metadata") or {}), "finding_count": len(out["findings"]),
                       "verified_count": len(out["findings"])}
    return _strip(out)


def _cross_signal(data: dict) -> dict:
    return _strip(data)


def _decision(data: dict) -> dict:
    return _strip(data)


def capture(name: str, bundle: Path) -> None:
    out, src = ROOT / name, bundle / "inputs"
    passes = sorted(src.glob("verification_pass_*.json"))
    if not passes:
        raise SystemExit(f"No verification pass in {src}")
    ver = _load(passes[-1])
    trusted = _trusted(ver.get("findings", []))

    _write(out / "context" / "plan.json", _load(bundle / "context" / "plan.json"))
    _write(out / "inputs" / "initial_input.json", _load(src / "initial_input.json"))
    for worker, (path, data) in _latest_workers(src).items():
        _write(out / "inputs" / f"worker_{worker}.json", _worker(data, trusted))
    _write(out / "inputs" / "verification_pass_001.json", _verification(ver, trusted))
    _write(out / "inputs" / "cross_signal.json", _cross_signal(_load(src / "cross_signal.json")))
    _write(out / "inputs" / "decision_analysis.json", _decision(_load(src / "decision_analysis.json")))
    _write(out / "ground_truth.json", {
        "curated": False,
        "seeded_from": bundle.name,
        "seeded_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Seeded from an EDRAK run and filtered to findings verified with medium/high evidence. "
                "Not human-reviewed; review and edit, then set curated to true.",
    })
    print(f"Wrote {out}")


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9$%.]+", (text or "").lower()) if len(t) > 3}


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def compare(name: str, bundle: Path, threshold: float) -> list[dict]:
    truth_inputs = ROOT / name / "inputs"
    if not truth_inputs.exists():
        raise SystemExit(f"No ground truth '{name}' in {ROOT}; run capture first.")
    truth = _latest_workers(truth_inputs)
    current = _latest_workers(bundle / "inputs")
    rows = []
    for worker, (_, exp) in sorted(truth.items()):
        got = current.get(worker, (None, None))[1]
        row = {"stage": worker, "in_edrak": got is not None}
        if got:
            exp_f, got_f = exp.get("findings", []), got.get("findings", [])
            matched = sum(
                1 for a in exp_f
                if any(_overlap(a.get("statement"), b.get("statement")) >= threshold for b in got_f)
            )
            row.update(
                findings=[len(exp_f), len(got_f)],
                truth_findings_found_in_edrak=f"{matched}/{len(exp_f)}",
            )
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["capture", "compare"])
    parser.add_argument("name", help="ground-truth folder name under golden/ground_truth")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--threshold", type=float, default=0.5, help="word overlap (Jaccard) to call two findings a match")
    args = parser.parse_args()
    if args.action == "capture":
        capture(args.name, args.bundle)
    else:
        print(json.dumps(compare(args.name, args.bundle, args.threshold), indent=2))


if __name__ == "__main__":
    main()
