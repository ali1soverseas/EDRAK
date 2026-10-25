"""Compare EDRAK runs against the golden ground truth (v2).

    python compare.py <ground_truth_name> <run_bundle> [<run_bundle> ...] [options]

The JSON report contains only the fields consumed by the dashboard's ground-truth view:
mean recall, alignment, F1, pooled recall, per-stage volume, and uncovered ground-truth items.

Ground-truth item schema (all new fields optional):
  {"statement": "...", "alternatives": ["other wording"]}

Judge responses are fetched fresh on every run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

GROUND_TRUTH = Path(__file__).resolve().parents[2] / "golden" / "ground_truth"

CREDIT = {"full": 1.0, "partial": 0.5, "none": 0.0, "contradicts": 0.0}
RESCUE_CANDIDATE_LIMIT = 5

MATCH_PROMPT = """You compare a system's output with ground truth for a business-intelligence task.
Both lists are about: {label}.
For EVERY ground-truth item choose a verdict:
  full         a system item states the same claim (wording, examples, numbers may differ)
  partial      system items cover the same theme but only part of the claim, or a different angle
  none         nothing in the system list addresses it
  contradicts  a system item states the opposite
List ALL system items that support the ground-truth item (several are allowed).
Ground-truth items may list acceptable alternatives after "ALT:"; matching any of them counts.
Return JSON only: {{"matches": [{{"truth": <index>, "verdict": "full|partial|none|contradicts",
"system": [<indexes>], "reason": "<max 12 words>"}}]}} with exactly one entry per ground-truth index.

GROUND TRUTH:
{truth}

SYSTEM:
{system}
"""

RESCUE_PROMPT = """Ground-truth items below were not matched in a first pass. For each one, look ONLY at its
candidate system items and decide again with the same verdicts (full | partial | none | contradicts).
Be fair: different wording or specifics are fine, an unrelated topic is not.
Return JSON only: {{"matches": [{{"truth": <index>, "verdict": "...", "system": [<candidate indexes>],
"reason": "<max 12 words>"}}]}} with one entry per ground-truth index.

{blocks}
"""

# ----------------------------------------------------------------------------- judge
class Judge:
    def __init__(self, model: str) -> None:
        self.model = model
        try:
            from dotenv import load_dotenv
            load_dotenv(".env")
        except ImportError:
            pass
        if not os.getenv("OPENAI_API_KEY"):
            raise SystemExit("Set OPENAI_API_KEY to run the ground-truth comparison.")
        from openai import OpenAI
        self.client = OpenAI()

    def _chat(self, prompt: str) -> dict:
        last = None
        for _ in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model, messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"})
                data = json.loads(resp.choices[0].message.content)
                return data
            except Exception as exc:  # transient API/JSON errors are retried, exhausted quota is not
                last = exc
                if "insufficient_quota" in str(exc):
                    break
        raise SystemExit(f"Judge call failed: {last}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Create semantic vectors while preserving positions for blank texts."""
        nonempty = [
            (index, text.replace("\n", " ").strip())
            for index, text in enumerate(texts)
            if text.strip()
        ]
        if not nonempty:
            return [[] for _ in texts]

        response = self.client.embeddings.create(
            model="text-embedding-3-small",
            input=[text for _, text in nonempty],
        )
        vectors = {item.index: item.embedding for item in response.data}
        if len(vectors) != len(nonempty) or any(
            index not in vectors for index in range(len(nonempty))
        ):
            raise RuntimeError("Embedding response did not contain every requested vector.")

        dimension = len(next(iter(vectors.values())))
        vector_by_text = {
            original_index: vectors[request_index]
            for request_index, (original_index, _) in enumerate(nonempty)
        }
        return [
            vector_by_text.get(index, [0.0] * dimension)
            for index in range(len(texts))
        ]

    # --- matching -----------------------------------------------------------------
    @staticmethod
    def _clean(entry: dict, n_sys: int, order: list[int] | None = None) -> dict:
        sys_ = entry.get("system")
        sys_ = [] if sys_ is None else [sys_] if isinstance(sys_, int) else list(sys_)
        sys_ = [s for s in sys_ if isinstance(s, int) and 0 <= s < n_sys]
        if order:
            sys_ = [order[s] for s in sys_]
        verdict = entry.get("verdict")
        if verdict not in CREDIT:
            verdict = "full" if sys_ else "none"
        if verdict != "none" and not sys_:
            verdict = "none"
        if verdict == "none":
            sys_ = []
        return {"verdict": verdict, "system": sorted(set(sys_)), "reason": str(entry.get("reason", ""))[:120]}

    def match(self, label: str, truth: list[str], system: list[str], reverse: bool = False) -> list[dict]:
        if not system:
            return [{"verdict": "none", "system": [], "reason": "no system items"} for _ in truth]
        order = list(range(len(system)))[::-1] if reverse else list(range(len(system)))
        fmt_t = "\n".join(f"[{i}] {t}" for i, t in enumerate(truth))
        fmt_s = "\n".join(f"[{k}] {system[o]}" for k, o in enumerate(order))
        data = self._chat(MATCH_PROMPT.format(label=label, truth=fmt_t, system=fmt_s))
        by_idx = {m.get("truth"): m for m in data.get("matches", []) if isinstance(m, dict)}
        return [self._clean(by_idx.get(i, {}), len(system), order) for i in range(len(truth))]

    def rescue(self, label: str, truth: list[str], system: list[str], cands: dict[int, list[int]]) -> dict[int, dict]:
        """Re-judge unmatched truth items against their own nearest candidates only."""
        if not cands:
            return {}
        blocks = []
        for t_idx, c in cands.items():
            lines = "\n".join(f"   [{j}] {system[j]}" for j in c)
            blocks.append(f"TRUTH [{t_idx}]: {truth[t_idx]}\n CANDIDATES:\n{lines}")
        data = self._chat(RESCUE_PROMPT.format(blocks="\n\n".join(blocks)))
        out = {}
        for m in data.get("matches", []):
            if isinstance(m, dict) and m.get("truth") in cands:
                e = self._clean(m, len(system))
                e["system"] = [s for s in e["system"] if s in cands[m["truth"]]]
                if not e["system"]:
                    e["verdict"] = "none"
                out[m["truth"]] = e
        return out

# ----------------------------------------------------------------------------- helpers
def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def final_workers(inputs: Path) -> dict[str, dict]:
    best: dict[str, tuple[tuple, dict]] = {}
    for path in inputs.glob("worker_*.json"):
        data = load(path)
        worker = data.get("worker")
        rank = (
            int(data.get("attempt") or 1),
            str(data.get("completed_at") or ""),
            path.stat().st_mtime_ns,
            path.name,
        )
        if worker and (worker not in best or rank >= best[worker][0]):
            best[worker] = (rank, data)
    return {w: d for w, (_, d) in best.items()}


def final_verification(inputs: Path) -> dict:
    passes = sorted(inputs.glob("verification_pass_*.json"))
    return load(passes[-1]) if passes else {}


def text_of(item: dict, keys: tuple[str, ...]) -> str:
    parts: list[str] = []
    for k in keys:
        v = str(item.get(k) or "").strip()
        if v and v not in parts:
            parts.append(v)
    return " ".join(parts)


def truth_text(item: dict, keys: tuple[str, ...]) -> str:
    base = text_of(item, keys)
    alts = [str(a) for a in item.get("alternatives", []) if a]
    return f"{base} ALT: {' | '.join(alts)}" if alts else base


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if len(s.strip()) > 20]


def merge_verdicts(a: dict, b: dict) -> dict:
    better = a if CREDIT[a["verdict"]] >= CREDIT[b["verdict"]] else b
    pick = dict(better)
    if a["verdict"] == b["verdict"]:
        pick["system"] = sorted(set(a["system"]) | set(b["system"]))
    return pick


# ----------------------------------------------------------------------------- scoring
def semantic_candidates_for(
    query_vector: list[float], candidate_vectors: list[list[float]], k: int
) -> list[int]:
    if not query_vector or k <= 0:
        return []

    query_norm = sum(value * value for value in query_vector) ** 0.5
    if query_norm == 0:
        return []

    scores: list[tuple[float, int]] = []
    for index, candidate in enumerate(candidate_vectors):
        if len(candidate) != len(query_vector):
            raise ValueError("Query and candidate embeddings must have matching dimensions.")
        candidate_norm = sum(value * value for value in candidate) ** 0.5
        if candidate_norm == 0:
            continue
        similarity = sum(
            left * right for left, right in zip(query_vector, candidate)
        ) / (query_norm * candidate_norm)
        scores.append((similarity, index))
    return [
        index for _, index in sorted(scores, key=lambda item: (-item[0], item[1]))[:k]
    ]


def score(ctx: dict, label: str, truth: list[dict], got: list[dict], keys: tuple[str, ...]) -> dict:
    judge: Judge = ctx["judge"]
    t_text = [truth_text(x, keys) for x in truth]
    g_text = [text_of(x, keys) for x in got]

    claims = g_text
    parent = list(range(len(g_text)))
    verdicts = judge.match(label, t_text, claims)
    if ctx["consistency"]:
        second = judge.match(label, t_text, claims, reverse=True)
        verdicts = [merge_verdicts(a, b) for a, b in zip(verdicts, second)]
    missed = [i for i, verdict in enumerate(verdicts) if verdict["verdict"] == "none"]
    candidates = {}
    if missed and claims:
        embeddings = judge.embed([t_text[i] for i in missed] + claims)
        expected_count = len(missed) + len(claims)
        if len(embeddings) != expected_count:
            raise RuntimeError("Embedding response count did not match requested texts.")
        truth_vectors = embeddings[:len(missed)]
        claim_vectors = embeddings[len(missed):]
        candidates = {
            truth_index: semantic_candidates_for(
                truth_vector, claim_vectors, RESCUE_CANDIDATE_LIMIT
            )
            for truth_index, truth_vector in zip(missed, truth_vectors)
        }
    candidates = {i: items for i, items in candidates.items() if items}
    for i, verdict in judge.rescue(label, t_text, claims, candidates).items():
        if CREDIT[verdict["verdict"]] > 0 or verdict["verdict"] == "contradicts":
            verdicts[i] = verdict | {"reason": "rescued: " + verdict["reason"]}

    # claim indexes -> parent EDRAK item indexes
    parents = [sorted({parent[s] for s in v["system"]}) for v in verdicts]
    n = len(truth)
    supported = [CREDIT[v["verdict"]] > 0 for v in verdicts]
    matched = sum(supported)
    used = {p for ps, ok in zip(parents, supported) if ok for p in ps}
    recall = matched / n if n else None
    alignment = len(used) / len(got) if got else None
    f1 = (
        round(2 * recall * alignment / (recall + alignment), 3)
        if recall and alignment
        else 0.0 if truth and got else None
    )
    return {
        "truth_items": n,
        "matched": matched,
        "recall": round(recall, 3) if recall is not None else None,
        "alignment": round(alignment, 3) if alignment is not None else None,
        "f1": f1,
        "volume_ratio": round(len(got) / n, 2) if n else None,
        "not_covered": [
            {"item": t_text[i], "reason": verdicts[i]["reason"], "verdict": verdicts[i]["verdict"]}
            for i in range(n) if not supported[i]
        ],
    }


def flat_stages(report: dict) -> dict[str, dict]:
    flat = {f"workers/{w}": v for w, v in report["workers"].items()}
    flat |= {"verification": report["verification"], "cross_signal": report["cross_signal"]}
    flat |= {f"decision/{k}": v for k, v in report["decision"].items()}
    return flat


def mean(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(sum(present) / len(present), 3) if present else None


def orchestration_score(truth: dict, got: dict) -> dict:
    expected_workers = {task.get("worker") for task in truth.get("tasks", [])}
    actual_workers = {task.get("worker") for task in got.get("tasks", [])}
    matched = len(expected_workers & actual_workers)
    truth_count, run_count = len(expected_workers), len(actual_workers)
    recall = matched / truth_count if truth_count else None
    alignment = matched / run_count if run_count else None
    f1 = (
        round(2 * recall * alignment / (recall + alignment), 3)
        if recall and alignment else 0.0 if truth_count and run_count else None
    )
    return {
        "truth_items": truth_count,
        "matched": matched,
        "recall": round(recall, 3) if recall is not None else None,
        "alignment": round(alignment, 3) if alignment is not None else None,
        "f1": f1,
    }


def coverage_overview(report: dict, orchestration: dict | None = None) -> dict:
    flat = flat_stages(report)
    stages = list(flat.values())
    if orchestration is not None:
        stages.append(orchestration)
    truth_total = sum(s["truth_items"] for s in stages)
    return {
        "mean_recall": mean([stage["recall"] for stage in stages]),
        "mean_alignment": mean([stage["alignment"] for stage in stages]),
        "mean_f1": mean([stage["f1"] for stage in stages]),
        "pooled_recall": (
            round(sum(stage["matched"] for stage in stages) / truth_total, 3)
            if truth_total else None
        ),
        "stages_with_zero_recall": [
            name for name, stage in flat.items() if stage["recall"] == 0
        ],
    }


# ----------------------------------------------------------------------------- compare one bundle
def compare(name: str, bundle: Path, judge: Judge, opts: argparse.Namespace) -> dict:
    gt, gt_in, run_in = GROUND_TRUTH / name, GROUND_TRUTH / name / "inputs", bundle / "inputs"
    if not gt_in.exists():
        raise SystemExit(f"No ground truth '{name}' in {GROUND_TRUTH}")
    ctx = {
        "judge": judge,
        "consistency": opts.consistency,
    }
    report: dict = {"ground_truth": name, "run_bundle": bundle.name,
                    "method": f"llm judge ({judge.model}) + semantic embedding rescue "
                              "(text-embedding-3-small)"
                              + ("; consistency x2" if opts.consistency else "")}

    g_workers = final_workers(run_in)
    report["workers"] = {
        w: score(ctx, f"{w} research findings", t.get("findings", []), g_workers.get(w, {}).get("findings", []),
                 ("statement",))
        for w, t in sorted(final_workers(gt_in).items())}

    g_ver = final_verification(run_in)
    report["verification"] = score(
        ctx, "verified findings across all workers", final_verification(gt_in).get("findings", []),
        [f for f in g_ver.get("findings", []) if f.get("verification_status") == "verified"],
        ("statement",))

    report["cross_signal"] = score(
        ctx, "cross-domain signals", load(gt_in / "cross_signal.json").get("signals", []),
        load(run_in / "cross_signal.json").get("signals", []),
        ("title", "signal", "statement", "interpretation"))

    t_dec, g_dec = load(gt_in / "decision_analysis.json"), load(run_in / "decision_analysis.json")
    as_items = lambda text: [{"text": s} for s in sentences(text)]
    decision = {"executive_summary": score(
        ctx, "executive summary sentences", as_items(t_dec.get("executive_summary", "")),
        as_items(g_dec.get("executive_summary", "")), ("text",))}
    for field, keys in (
        ("opportunities", ("title", "description")),
        ("risks", ("title", "description")),
        ("recommended_actions", ("title", "description")),
        ("options", ("title", "thesis")),
    ):
        decision[field] = score(ctx, f"decision {field}", t_dec.get(field, []), g_dec.get(field, []), keys)
    report["decision"] = decision

    orchestration = orchestration_score(
        load(gt / "context" / "plan.json"),
        load(bundle / "context" / "plan.json"),
    )
    report["overview"] = coverage_overview(report, orchestration)
    return report


# ----------------------------------------------------------------------------- output
def print_summary(report: dict) -> None:
    overview = report["overview"]
    print(f"\n=== {report['run_bundle']} ===")
    print(f"Method: {report['method']}")
    print(
        f"Mean recall {overview['mean_recall']} | mean alignment {overview['mean_alignment']} "
        f"| mean F1 {overview['mean_f1']} | pooled recall {overview['pooled_recall']}"
    )
    print(f"\n{'stage':<34}{'match':>8}{'recall':>10}{'alignment':>12}{'f1':>8}{'volume':>10}{'missed':>9}")
    for label, r in flat_stages(report).items():
        print(
            f"{label:<34}{str(r['matched']) + '/' + str(r['truth_items']):>8}"
            f"{str(r['recall']):>10}{str(r['alignment']):>12}{str(r['f1']):>8}"
            f"{str(r['volume_ratio']):>10}{len(r['not_covered']):>9}"
        )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("name", help="ground-truth folder under golden/ground_truth")
    p.add_argument("bundles", type=Path, nargs="+", help="one or more EDRAK run bundles")
    p.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-5.1"))
    p.add_argument("--consistency", action="store_true", help="judge twice with reversed EDRAK order")
    p.add_argument("--out", type=Path, help="write the dashboard comparison JSON report here")
    args = p.parse_args()
    if args.out and len(args.bundles) > 1:
        p.error("--out accepts one run bundle; omit it to print multiple comparisons")

    judge = Judge(args.model)
    reports = [compare(args.name, b, judge, args) for b in args.bundles]
    for r in reports:
        print_summary(r)
    final = reports[0]
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(final, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Dashboard report: {args.out}")


if __name__ == "__main__":
    sys.exit(main())