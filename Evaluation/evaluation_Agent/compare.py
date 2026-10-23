"""Compare EDRAK runs against the golden ground truth (v2).

    python compare.py <ground_truth_name> <run_bundle> [<run_bundle> ...] [options]

What changed compared with v1
-----------------------------
Matching (gets more of the real matches, fairly)
  * graded verdicts per ground-truth item: full | partial | none | contradicts
  * one truth item can be backed by SEVERAL EDRAK items (list of indexes)
  * ground-truth items may carry `alternatives` (other acceptable wordings) and `importance`
    (must | should | nice); both are used by the judge
  * pass 1: one judge call per stage (all items); pass 2 ("rescue"): every truth item still
    unmatched is re-judged against its top-k nearest EDRAK candidates (embeddings, or word
    overlap fallback). This recovers matches lost to long lists / position bias
  * optional --consistency: pass 1 is repeated with the EDRAK list reversed; reports judge
    agreement and merges with --merge max|min
  * optional --atomize: long EDRAK findings are split into sentence-level claims before
    matching, so multi-claim findings are not penalised against one-line ground truth

Metrics added per stage
  recall (full+partial, backward compatible), full_recall, graded_recall (full=1, partial=.5),
  weighted_recall (by importance), must_recall, one_to_one_recall, contradiction_rate,
  coverage_concentration, alignment (strict precision), precision_adjusted (valid novel items
  count as correct), valid_novel / noise split of extras, duplicate_rate of EDRAK items,
  bootstrap 95% CI of recall, judge_agreement, attribute agreement with vocabulary normalisation.
Decision stance (optional rubric.json in the ground-truth folder):
  acceptable | different_but_defensible | unacceptable, plus required-caveat recall.
Several bundles (repeat runs): headline metrics are aggregated as mean/std in `aggregate`.

Ground-truth item schema (all new fields optional):
  {"statement": "...", "importance": "must", "alternatives": ["other wording"], "category": "pricing"}
rubric.json (optional, next to inputs/ or in context/):
  {"acceptable_stances": ["phased partner-led launch"], "unacceptable_stances": ["broad launch now"],
   "required_caveats": ["confirm supply capacity", "export-control status"]}

Judge requests and embeddings are fetched fresh on every run.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import statistics
import sys
from pathlib import Path

GROUND_TRUTH = Path(__file__).resolve().parents[2] / "golden" / "ground_truth"

CREDIT = {"full": 1.0, "partial": 0.5, "none": 0.0, "contradicts": 0.0}
IMPORTANCE_W = {"must": 3.0, "should": 2.0, "nice": 1.0}
NOISE_FLAGS = {"low_relevancy", "unfaithful", "generic_statement", "no_added_insight", "irrelevant_evidence"}

# Different vocabularies for the same idea (golden vs EDRAK) are mapped to one label before comparing.
NORMALIZE = {
    "capability_gap": "gap", "gap": "gap", "regulation": "regulatory", "regulatory": "regulatory",
    "momentum": "trend", "trend": "trend", "convergence": "trend", "pricing": "pricing",
    "positioning": "positioning", "timing": "timing", "dependency": "dependency", "risk": "risk",
    "market_signal": "market", "market": "market", "go_to_market": "go_to_market", "product": "product",
    "immediate": "immediate", "high": "high", "medium": "medium", "low": "low",
    "near_term": "near_term", "medium_term": "medium_term", "long_term": "long_term",
}

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

EXTRAS_PROMPT = """Business goal: {goal}
The items below were produced by a research system but are NOT in our reference answer. Label each:
  valid_novel  relevant to the goal and plausibly useful, even if the reference did not list it
  noise        off-topic, trivial, generic background, or not useful for the goal
Return JSON only: {{"items": [{{"index": <n>, "label": "valid_novel|noise", "reason": "<max 10 words>"}}]}}

ITEMS ({label}):
{items}
"""

STANCE_PROMPT = """Business goal: {goal}
Decide whether the system's recommendation is acceptable against this rubric.
ACCEPTABLE STANCES: {ok}
UNACCEPTABLE STANCES: {bad}
REQUIRED CAVEATS (numbered): {caveats}

SYSTEM EXECUTIVE SUMMARY:
{summary}
SYSTEM OPTIONS / ACTIONS:
{extra}

Verdict "acceptable" = matches an acceptable stance; "different_but_defensible" = differs from the rubric but
is reasoned and evidence-based; "unacceptable" = matches an unacceptable stance or is unsupported.
Return JSON only: {{"stance": "...", "matched_stance": "<text>", "caveats": [{{"index": <n>, "covered": true|false}}],
"reason": "<max 25 words>"}}
"""


# ----------------------------------------------------------------------------- judge
class Judge:
    def __init__(self, model: str, enabled: bool, embed_model: str | None = None,
                 temperature: float | None = None) -> None:
        self.model, self.enabled, self.embed_model, self.temperature = model, enabled, embed_model, temperature
        self.calls = 0
        self.client = None
        if enabled:
            try:
                from dotenv import load_dotenv
                load_dotenv(".env")
            except ImportError:
                pass
            if not os.getenv("OPENAI_API_KEY"):
                raise SystemExit("Set OPENAI_API_KEY (or use --no-llm).")
            from openai import OpenAI
            self.client = OpenAI()

    @property
    def embeds(self) -> bool:
        return bool(self.enabled and self.embed_model)

    def _chat(self, prompt: str) -> dict:
        last = None
        for _ in range(3):
            try:
                kw = {"temperature": self.temperature} if self.temperature is not None else {}
                resp = self.client.chat.completions.create(
                    model=self.model, messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"}, **kw)
                data = json.loads(resp.choices[0].message.content)
                self.calls += 1
                return data
            except Exception as exc:  # transient API/JSON errors are retried, exhausted quota is not
                last = exc
                if "insufficient_quota" in str(exc):
                    break
        raise SystemExit(f"Judge call failed: {last}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=self.embed_model, input=texts)
        return [item.embedding for item in resp.data]

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

    def classify_extras(self, label: str, goal: str, extras: list[str]) -> list[dict]:
        result = []
        for start in range(0, len(extras), 40):
            chunk = extras[start:start + 40]
            items = "\n".join(f"[{i}] {t[:400]}" for i, t in enumerate(chunk))
            data = self._chat(EXTRAS_PROMPT.format(goal=goal, label=label, items=items))
            by = {m.get("index"): m for m in data.get("items", []) if isinstance(m, dict)}
            for i in range(len(chunk)):
                lab = by.get(i, {}).get("label")
                result.append({"label": lab if lab in ("valid_novel", "noise") else "noise",
                               "reason": str(by.get(i, {}).get("reason", ""))[:100]})
        return result

    def stance(self, goal: str, rubric: dict, summary: str, extra: str) -> dict:
        caveats = rubric.get("required_caveats", [])
        data = self._chat(STANCE_PROMPT.format(
            goal=goal, ok="; ".join(rubric.get("acceptable_stances", [])) or "n/a",
            bad="; ".join(rubric.get("unacceptable_stances", [])) or "n/a",
            caveats="; ".join(f"({i}) {c}" for i, c in enumerate(caveats)) or "none",
            summary=summary or "(empty)", extra=extra or "(none)"))
        covered = {c.get("index"): bool(c.get("covered")) for c in data.get("caveats", []) if isinstance(c, dict)}
        stance = data.get("stance") if data.get("stance") in ("acceptable", "different_but_defensible", "unacceptable") else "unacceptable"
        return {"stance": stance, "matched_stance": data.get("matched_stance", ""), "reason": data.get("reason", ""),
                "stance_credit": {"acceptable": 1.0, "different_but_defensible": 0.5, "unacceptable": 0.0}[stance],
                "caveats": [{"caveat": c, "covered": covered.get(i, False)} for i, c in enumerate(caveats)],
                "caveat_recall": round(sum(covered.get(i, False) for i in range(len(caveats))) / len(caveats), 3) if caveats else None}


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


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9$%.]+", (text or "").lower()) if len(t) > 3}


def overlap(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def containment(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0


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


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def mean(vals) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def bootstrap_ci(values: list[float], n: int = 1000, seed: int = 7) -> list[float] | None:
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(n))
    return [round(means[int(0.025 * n)], 3), round(means[int(0.975 * n) - 1], 3)]


def norm_attr(v) -> str:
    s = str(v or "").strip().lower().replace(" ", "_").replace("-", "_")
    return NORMALIZE.get(s, s)


def load_flagged(eval_dir: Path | None) -> list[str]:
    """Statements the evaluation agent already flagged as low quality (used to tag EDRAK extras as noise)."""
    out: list[str] = []
    if eval_dir and eval_dir.exists():
        for path in eval_dir.glob("*_report.json"):
            for f in load(path).get("files", []):
                for u in f.get("flagged_units", []):
                    if NOISE_FLAGS & set(u.get("flags", [])):
                        out.append(u.get("statement", ""))
    return out


def merge_verdicts(a: dict, b: dict, policy: str) -> dict:
    better, worse = (a, b) if CREDIT[a["verdict"]] >= CREDIT[b["verdict"]] else (b, a)
    if policy == "min":
        pick = worse
        return dict(pick)
    pick = dict(better)
    if a["verdict"] == b["verdict"]:
        pick["system"] = sorted(set(a["system"]) | set(b["system"]))
    return pick


# ----------------------------------------------------------------------------- scoring
def candidates_for(ctx: dict, t_text: str, g_claims: list[str], g_vecs, t_vec, k: int) -> list[int]:
    if not g_claims:
        return []
    if g_vecs is not None and t_vec is not None:
        scores = [cosine(t_vec, v) for v in g_vecs]
    else:
        scores = [max(overlap(t_text, g), containment(t_text, g) * 0.8) for g in g_claims]
    ranked = sorted(range(len(g_claims)), key=scores.__getitem__, reverse=True)[:k]
    return [i for i in ranked if scores[i] > 0]


def score(ctx: dict, label: str, truth: list[dict], got: list[dict], keys: tuple[str, ...],
          attrs: tuple[str, ...] = ()) -> dict:
    judge: Judge = ctx["judge"]
    t_text = [truth_text(x, keys) for x in truth]
    g_text = [text_of(x, keys) for x in got]

    # EDRAK items -> claims (optionally sentence-level), each claim remembers its parent item
    claims, parent = [], []
    for i, t in enumerate(g_text):
        parts = sentences(t) if ctx["atomize"] else []
        for p in parts or [t]:
            claims.append(p)
            parent.append(i)

    g_vecs = t_vecs = None
    if judge.embeds and claims and truth:
        g_vecs, t_vecs = judge.embed(claims), judge.embed(t_text)

    agreement = None
    if judge.enabled:
        verdicts = judge.match(label, t_text, claims)
        if ctx["consistency"]:
            second = judge.match(label, t_text, claims, reverse=True)
            agreement = round(sum(a["verdict"] == b["verdict"] for a, b in zip(verdicts, second)) / len(truth), 3) if truth else None
            verdicts = [merge_verdicts(a, b, ctx["merge"]) for a, b in zip(verdicts, second)]
        # pass 2: rescue unmatched truth items against their nearest candidates
        missed = [i for i, v in enumerate(verdicts) if v["verdict"] == "none"]
        cands = {i: candidates_for(ctx, t_text[i], claims, g_vecs, t_vecs[i] if t_vecs else None, ctx["topk"]) for i in missed}
        cands = {i: c for i, c in cands.items() if c}
        for i, v in judge.rescue(label, t_text, claims, cands).items():
            if CREDIT[v["verdict"]] > 0 or v["verdict"] == "contradicts":
                verdicts[i] = v | {"reason": "rescued: " + v["reason"]}
    else:  # free fallback: word overlap, graded by two thresholds
        verdicts = []
        for t in t_text:
            scores = [max(overlap(t, g), containment(t, g) * 0.8) for g in claims]
            top = max(range(len(scores)), key=scores.__getitem__) if scores else None
            if top is not None and scores[top] >= ctx["threshold"]:
                verdicts.append({"verdict": "full", "system": [top], "reason": "word overlap"})
            elif top is not None and scores[top] >= ctx["threshold"] / 2:
                verdicts.append({"verdict": "partial", "system": [top], "reason": "weak word overlap"})
            else:
                verdicts.append({"verdict": "none", "system": [], "reason": "word overlap"})

    # claim indexes -> parent EDRAK item indexes
    parents = [sorted({parent[s] for s in v["system"]}) for v in verdicts]
    n = len(truth)
    credit = [CREDIT[v["verdict"]] for v in verdicts]
    weights = [IMPORTANCE_W.get(str(x.get("importance", "should")).lower(), 2.0) for x in truth]
    supported = [CREDIT[v["verdict"]] > 0 for v in verdicts]
    matched = sum(supported)
    used = {p for ps, ok in zip(parents, supported) if ok for p in ps}
    contradicted = [i for i, v in enumerate(verdicts) if v["verdict"] == "contradicts"]

    # extras: EDRAK items not backing any truth item -> valid_novel vs noise
    extra_idx = [i for i in range(len(got)) if i not in used]
    extras = []
    if extra_idx:
        flagged = ctx["flagged"]
        pre_noise = {i: any(containment(g_text[i], f) >= 0.6 and overlap(g_text[i], f) >= 0.4 for f in flagged) for i in extra_idx}
        todo = [i for i in extra_idx if not pre_noise[i]]
        if judge.enabled and todo:
            labels = dict(zip(todo, judge.classify_extras(label, ctx["goal"], [g_text[i] for i in todo])))
        else:
            labels = {i: {"label": "valid_novel" if overlap(g_text[i], ctx["goal"]) >= 0.05 else "noise",
                          "reason": "goal word overlap"} for i in todo}
        for i in extra_idx:
            lab = {"label": "noise", "reason": "flagged by evaluation agent"} if pre_noise[i] else labels[i]
            extras.append({"item": g_text[i], "label": lab["label"], "reason": lab["reason"]})
    valid_novel = sum(e["label"] == "valid_novel" for e in extras)
    noise = len(extras) - valid_novel

    # one-to-one recall (greedy, most constrained truth items first)
    taken, assigned = set(), 0
    for i in sorted((i for i in range(n) if supported[i]), key=lambda i: len(parents[i])):
        free = [p for p in parents[i] if p not in taken]
        if free:
            taken.add(free[0])
            assigned += 1
    uses: dict[int, int] = {}
    for ps, ok in zip(parents, supported):
        for p in ps if ok else []:
            uses[p] = uses.get(p, 0) + 1

    dup = sum(any(overlap(g_text[i], g_text[j]) >= 0.8 for j in range(i)) for i in range(len(got)))

    recall = matched / n if n else None
    alignment = len(used) / len(got) if got else None
    prec_adj = (len(used) + valid_novel) / len(got) if got else None
    wsum = sum(weights)
    must = [c for c, x in zip(credit, truth) if str(x.get("importance", "")).lower() == "must"]

    result = {
        "truth_items": n, "edrak_items": len(got), "matched": matched,
        "full_matches": sum(v["verdict"] == "full" for v in verdicts),
        "partial_matches": sum(v["verdict"] == "partial" for v in verdicts),
        "contradictions": len(contradicted),
        "recall": round(recall, 3) if recall is not None else None,
        "full_recall": round(sum(v["verdict"] == "full" for v in verdicts) / n, 3) if n else None,
        "graded_recall": round(sum(credit) / n, 3) if n else None,
        "weighted_recall": round(sum(w * c for w, c in zip(weights, credit)) / wsum, 3) if n else None,
        "must_recall": round(sum(must) / len(must), 3) if must else None,
        "one_to_one_recall": round(assigned / n, 3) if n else None,
        "recall_ci95": bootstrap_ci(credit),
        "contradiction_rate": round(len(contradicted) / n, 3) if n else None,
        "coverage_concentration": max(uses.values()) if uses else 0,
        "alignment": round(alignment, 3) if alignment is not None else None,
        "precision_adjusted": round(prec_adj, 3) if prec_adj is not None else None,
        "valid_novel": valid_novel, "noise": noise,
        "noise_rate": round(noise / len(got), 3) if got else None,
        "duplicate_rate": round(dup / len(got), 3) if got else None,
        "f1": round(2 * recall * alignment / (recall + alignment), 3) if recall and alignment else 0.0 if truth and got else None,
        "f1_adjusted": round(2 * recall * prec_adj / (recall + prec_adj), 3) if recall and prec_adj else 0.0 if truth and got else None,
        "volume_ratio": round(len(got) / n, 2) if n else None,
        "judge_agreement": agreement,
        "matches": [{"truth": t_text[i], "importance": truth[i].get("importance", "should"),
                     "verdict": verdicts[i]["verdict"], "reason": verdicts[i]["reason"],
                     "edrak": [g_text[p] for p in parents[i]]} for i in range(n)],
        "not_covered": [{"item": t_text[i], "reason": verdicts[i]["reason"], "verdict": verdicts[i]["verdict"]}
                        for i in range(n) if not supported[i]],
        "extra_edrak_items": extras,
    }
    for attr in attrs:
        pairs = [(norm_attr(truth[i].get(attr)), norm_attr(got[ps[0]].get(attr)))
                 for i, ps in enumerate(parents) if supported[i] and ps]
        pairs = [(a, b) for a, b in pairs if a and b]
        result[f"{attr}_agreement"] = round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None
    if attrs:
        groups: dict[str, list[float]] = {}
        for item, c in zip(truth, credit):
            groups.setdefault(norm_attr(item.get(attrs[0])) or "unspecified", []).append(1.0 if c > 0 else 0.0)
        result[f"recall_by_{attrs[0]}"] = {k: round(sum(v) / len(v), 3) for k, v in sorted(groups.items())}
    return result


# ----------------------------------------------------------------------------- non-LLM stages
def orchestrator(truth: dict, got: dict) -> dict:
    t_tasks, g_tasks = truth.get("tasks", []), got.get("tasks", [])
    t_workers, g_workers = {t.get("worker") for t in t_tasks}, {t.get("worker") for t in g_tasks}
    goals = {t.get("worker"): t.get("goal", "") for t in g_tasks}
    shared = t_workers & g_workers
    matched = len(shared)
    recall = matched / len(t_workers) if t_workers else None
    alignment = matched / len(g_workers) if g_workers else None
    f1 = 2 * recall * alignment / (recall + alignment) if recall and alignment else 0.0 if t_workers and g_workers else None
    return {
        "workers_expected": sorted(t_workers),
        "workers_missing_in_edrak": sorted(t_workers - g_workers),
        "workers_extra_in_edrak": sorted(g_workers - t_workers),
        "worker_recall": round(recall, 3) if recall is not None else None,
        "worker_jaccard": round(len(shared) / len(t_workers | g_workers), 3) if t_workers | g_workers else None,
        "task_count": [len(t_tasks), len(g_tasks)],
        "task_goal_word_overlap": {t.get("worker"): round(overlap(t.get("goal", ""), goals.get(t.get("worker"), "")), 3) for t in t_tasks},
        "truth_items": len(t_workers),
        "edrak_items": len(g_workers),
        "matched": matched,
        "full_matches": matched,
        "partial_matches": 0,
        "contradictions": 0,
        "recall": round(recall, 3) if recall is not None else None,
        "full_recall": round(recall, 3) if recall is not None else None,
        "graded_recall": round(recall, 3) if recall is not None else None,
        "weighted_recall": round(recall, 3) if recall is not None else None,
        "must_recall": None,
        "one_to_one_recall": round(recall, 3) if recall is not None else None,
        "contradiction_rate": 0.0 if t_workers else None,
        "coverage_concentration": 1 if matched else 0,
        "alignment": round(alignment, 3) if alignment is not None else None,
        "precision_adjusted": round(alignment, 3) if alignment is not None else None,
        "valid_novel": 0,
        "noise": len(g_workers - t_workers),
        "noise_rate": round(len(g_workers - t_workers) / len(g_workers), 3) if g_workers else None,
        "duplicate_rate": 0.0 if g_workers else None,
        "f1": round(f1, 3) if f1 is not None else None,
        "f1_adjusted": round(f1, 3) if f1 is not None else None,
        "volume_ratio": round(len(g_workers) / len(t_workers), 2) if t_workers else None,
        "judge_agreement": None,
    }


def query_alignment(truth: dict, got: dict) -> dict:
    pick = lambda d: {"goal": d.get("goal", ""),
                      "company": (d.get("company_profile") or {}).get("name", ""),
                      "company_aliases": (d.get("company_profile") or {}).get("aliases", []),
                      "targets": sorted(str(t) for t in (d.get("business_context") or {}).get("targets", [])),
                      "markets": sorted(str(m) for m in (d.get("business_context") or {}).get("markets", []))}
    t, g = pick(truth), pick(got)
    t_names = [t["company"], *t["company_aliases"]]
    g_names = [g["company"], *g["company_aliases"]]
    return {"goal_word_overlap": round(overlap(t["goal"], g["goal"]), 3),
            "same_company": any(overlap(t_name, g_name) >= 0.5 for t_name in t_names for g_name in g_names),
            "same_targets": t["targets"] == g["targets"], "same_markets": t["markets"] == g["markets"],
            "ground_truth": t, "edrak": g}


def flat_stages(report: dict) -> dict[str, dict]:
    flat = {f"workers/{w}": v for w, v in report["workers"].items()}
    flat |= {"orchestrator": report["orchestrator"],
             "verification": report["verification"], "cross_signal": report["cross_signal"]}
    flat |= {f"decision/{k}": v for k, v in report["decision"].items()}
    return flat


def coverage_overview(report: dict) -> dict:
    flat = flat_stages(report)
    stages = list(flat.values())
    truth_total = sum(s["truth_items"] for s in stages)
    got_total = sum(s["edrak_items"] for s in stages)
    pooled = lambda k: round(sum((s[k] or 0) * s["truth_items"] for s in stages) / truth_total, 3) if truth_total else None
    ov = {
        "mean_recall": mean(s["recall"] for s in stages),
        "mean_alignment": mean(s["alignment"] for s in stages),
        "mean_f1": mean(s["f1"] for s in stages),
        "pooled_recall": round(sum(s["matched"] for s in stages) / truth_total, 3) if truth_total else None,
        "mean_graded_recall": mean(s["graded_recall"] for s in stages),
        "pooled_graded_recall": pooled("graded_recall"),
        "mean_weighted_recall": mean(s["weighted_recall"] for s in stages),
        "pooled_weighted_recall": pooled("weighted_recall"),
        "mean_full_recall": mean(s["full_recall"] for s in stages),
        "mean_one_to_one_recall": mean(s["one_to_one_recall"] for s in stages),
        "must_recall": mean(s["must_recall"] for s in stages),
        "mean_precision_adjusted": mean(s["precision_adjusted"] for s in stages),
        "mean_f1_adjusted": mean(s["f1_adjusted"] for s in stages),
        "contradiction_rate": round(sum(s["contradictions"] for s in stages) / truth_total, 3) if truth_total else None,
        "noise_rate": round(sum(s["noise"] for s in stages) / got_total, 3) if got_total else None,
        "valid_novel_total": sum(s["valid_novel"] for s in stages),
        "duplicate_rate": mean(s["duplicate_rate"] for s in stages),
        "judge_agreement": mean(s["judge_agreement"] for s in stages),
        "stages_with_zero_recall": [k for k, s in flat.items() if s["recall"] == 0],
        "stages_with_contradictions": [k for k, s in flat.items() if s["contradictions"]],
        "weakest_stages": [k for k, _ in sorted(((k, s["graded_recall"]) for k, s in flat.items() if s["graded_recall"] is not None),
                                                key=lambda kv: kv[1])[:3]],
    }
    if report.get("stance"):
        ov["stance"] = report["stance"]["stance"]
        ov["stance_credit"] = report["stance"]["stance_credit"]
        ov["caveat_recall"] = report["stance"]["caveat_recall"]
    return ov


# ----------------------------------------------------------------------------- compare one bundle
def compare(name: str, bundle: Path, judge: Judge, opts: argparse.Namespace) -> dict:
    gt, gt_in, run_in = GROUND_TRUTH / name, GROUND_TRUTH / name / "inputs", bundle / "inputs"
    if not gt_in.exists():
        raise SystemExit(f"No ground truth '{name}' in {GROUND_TRUTH}")
    run_query = load(run_in / "initial_input.json")
    ctx = {"judge": judge, "threshold": opts.threshold, "atomize": opts.atomize, "consistency": opts.consistency,
           "merge": opts.merge, "topk": opts.topk, "flagged": load_flagged(opts.eval_dir),
           "goal": run_query.get("goal") or load(gt_in / "initial_input.json").get("goal", "")}
    report: dict = {"ground_truth": name, "run_bundle": bundle.name,
                    "method": (f"llm judge ({judge.model})" if judge.enabled else f"word overlap >= {opts.threshold}")
                              + ("; embeddings " + judge.embed_model if judge.embeds else "")
                              + ("; atomized" if opts.atomize else "") + ("; consistency x2" if opts.consistency else "")}

    report["query"] = query_alignment(load(gt_in / "initial_input.json"), run_query)
    report["orchestrator"] = orchestrator(load(gt / "context" / "plan.json"), load(bundle / "context" / "plan.json"))

    g_workers = final_workers(run_in)
    report["workers"] = {
        w: score(ctx, f"{w} research findings", t.get("findings", []), g_workers.get(w, {}).get("findings", []),
                 ("statement",), ("category",)) | {"in_edrak": w in g_workers}
        for w, t in sorted(final_workers(gt_in).items())}

    g_ver = final_verification(run_in)
    report["verification"] = score(
        ctx, "verified findings across all workers", final_verification(gt_in).get("findings", []),
        [f for f in g_ver.get("findings", []) if f.get("verification_status") == "verified"],
        ("statement",), ("worker",))

    report["cross_signal"] = score(
        ctx, "cross-domain signals", load(gt_in / "cross_signal.json").get("signals", []),
        load(run_in / "cross_signal.json").get("signals", []),
        ("title", "signal", "statement", "interpretation"), ("signal_type", "urgency"))

    t_dec, g_dec = load(gt_in / "decision_analysis.json"), load(run_in / "decision_analysis.json")
    as_items = lambda text: [{"text": s} for s in sentences(text)]
    decision = {"executive_summary": score(
        ctx, "executive summary sentences", as_items(t_dec.get("executive_summary", "")),
        as_items(g_dec.get("executive_summary", "")), ("text",))}
    for field, keys, attrs in (
        ("opportunities", ("title", "description"), ("urgency", "strategic_fit")),
        ("risks", ("title", "description"), ("likelihood", "impact")),
        ("recommended_actions", ("title", "description"), ("priority", "time_horizon")),
        ("options", ("title", "thesis"), ()),
    ):
        decision[field] = score(ctx, f"decision {field}", t_dec.get(field, []), g_dec.get(field, []), keys, attrs)
    report["decision"] = decision

    rubric = load(gt_in / "rubric.json") or load(gt / "rubric.json") or load(gt / "context" / "rubric.json")
    if rubric and judge.enabled:
        extra = "\n".join(f"- {o.get('title', '')}: {o.get('thesis', '')}" for o in g_dec.get("options", [])) + "\n" + \
                "\n".join(f"- {a.get('title', '')}" for a in g_dec.get("recommended_actions", []))
        report["stance"] = judge.stance(ctx["goal"], rubric, g_dec.get("executive_summary", ""), extra)
    report["overview"] = coverage_overview(report)
    return report


AGG_KEYS = ("mean_recall", "pooled_recall", "mean_graded_recall", "mean_weighted_recall", "must_recall",
            "mean_alignment", "mean_precision_adjusted", "mean_f1", "mean_f1_adjusted", "contradiction_rate",
            "noise_rate", "duplicate_rate", "stance_credit")


def aggregate(reports: list[dict]) -> dict:
    out = {}
    for k in AGG_KEYS:
        vals = [r["overview"].get(k) for r in reports if r["overview"].get(k) is not None]
        if vals:
            out[k] = {"mean": round(statistics.mean(vals), 3),
                      "std": round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0, "n": len(vals)}
    return out


# ----------------------------------------------------------------------------- output
def print_summary(report: dict) -> None:
    q, o, ov = report["query"], report["orchestrator"], report["overview"]
    print(f"\n=== {report['run_bundle']} ===")
    print(f"Method: {report['method']}")
    print(f"Query: goal overlap {q['goal_word_overlap']}, same company {q['same_company']}, "
          f"same targets {q['same_targets']}, same markets {q['same_markets']}")
    print(f"Orchestrator: worker recall {o['worker_recall']}, missing {o['workers_missing_in_edrak']}, "
          f"extra {o['workers_extra_in_edrak']}, tasks {o['task_count']}")
    cols = ("recall", "graded_recall", "weighted_recall", "alignment", "precision_adjusted", "noise_rate", "contradictions", "f1_adjusted")
    print(f"\n{'stage':<34}{'match':>8}" + "".join(f"{c[:9]:>11}" for c in cols))
    for label, r in flat_stages(report).items():
        print(f"{label:<34}{str(r['matched']) + '/' + str(r['truth_items']):>8}" + "".join(f"{r[c]!s:>11}" for c in cols))
    print(f"\nRecall: mean {ov['mean_recall']} | graded {ov['mean_graded_recall']} | weighted {ov['mean_weighted_recall']} "
          f"| must {ov['must_recall']} | pooled {ov['pooled_recall']} | one-to-one {ov['mean_one_to_one_recall']}")
    print(f"Precision: strict {ov['mean_alignment']} | adjusted {ov['mean_precision_adjusted']} | noise {ov['noise_rate']} "
          f"| valid novel items {ov['valid_novel_total']} | duplicates {ov['duplicate_rate']}")
    print(f"Quality: contradiction rate {ov['contradiction_rate']} | f1 {ov['mean_f1']} / adjusted {ov['mean_f1_adjusted']} "
          f"| judge agreement {ov['judge_agreement']}")
    if "stance" in ov:
        print(f"Decision stance: {ov['stance']} (credit {ov['stance_credit']}, caveat recall {ov['caveat_recall']})")
    if ov["stages_with_zero_recall"]:
        print(f"Zero recall: {', '.join(ov['stages_with_zero_recall'])}")
    if ov["stages_with_contradictions"]:
        print(f"Contradictions in: {', '.join(ov['stages_with_contradictions'])}")
    print(f"Weakest stages: {', '.join(ov['weakest_stages'])}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("name", help="ground-truth folder under golden/ground_truth")
    p.add_argument("bundles", type=Path, nargs="+", help="one or more EDRAK run bundles (repeat runs give mean/std)")
    p.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-5.1"))
    p.add_argument("--embed-model", default=None, help="e.g. text-embedding-3-small: enables embedding candidate retrieval")
    p.add_argument("--temperature", type=float, default=None, help="judge temperature (omit for models that fix it)")
    p.add_argument("--no-llm", action="store_true", help="word-overlap matching, free")
    p.add_argument("--threshold", type=float, default=0.3, help="word overlap for --no-llm (half of it = partial)")
    p.add_argument("--topk", type=int, default=5, help="candidates per unmatched truth item in the rescue pass")
    p.add_argument("--atomize", action="store_true", help="split EDRAK items into sentence-level claims before matching")
    p.add_argument("--consistency", action="store_true", help="judge twice (EDRAK list reversed) and report agreement")
    p.add_argument("--merge", choices=("max", "min"), default="max", help="how to merge the two consistency passes")
    p.add_argument("--eval-dir", type=Path, help="folder with *_report.json from the evaluation agent (tags noise)")
    p.add_argument("--out", type=Path, help="write the full JSON report here")
    args = p.parse_args()

    judge = Judge(args.model, enabled=not args.no_llm, embed_model=args.embed_model, temperature=args.temperature)
    reports = [compare(args.name, b, judge, args) for b in args.bundles]
    for r in reports:
        print_summary(r)
    final = reports[0]
    if len(reports) > 1:
        final = final | {"aggregate": aggregate(reports), "runs": [r["run_bundle"] for r in reports],
                         "run_overviews": [r["overview"] for r in reports]}
        print("\n=== Aggregate over runs (mean ± std) ===")
        for k, v in final["aggregate"].items():
            print(f"{k:<26}{v['mean']:>8} ± {v['std']}  (n={v['n']})")
    if judge.enabled:
        print(f"\nJudge calls: {judge.calls}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(final, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Full report: {args.out}")


if __name__ == "__main__":
    sys.exit(main())