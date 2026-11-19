#!/usr/bin/env python3
"""Compare two Edrak run_evaluation_report.json files and pick the better version.

Usage:
  python compare.py optimized.json graphrag.json --name-a Optimized --name-b GraphRAG

Writes three files:
  comparison_report.md    - readable summary
  comparison_report.json  - all numbers
  comparison_report.html  - results page, open it in a browser (works offline)

How the winner is picked:
  1. If the reports can't be compared (same run, or different golden dataset) -> NO DECISION
  2. If one version has critical failures and the other doesn't -> the other one wins
  3. Otherwise the version with the higher overall score wins,
     unless the difference is TIE_MARGIN or less -> TIE

Score of one worker/stage = correctness 30% + faithfulness 30% + completeness 25% + coverage 15%.
Overall score = simple average of all workers and stages.
Workers with zero findings in either version are left out of the overall score:
there is nothing to compare, the judge would only be grading an empty answer.
"""
import argparse
import json
from pathlib import Path

WEIGHTS = {"correctness": 0.30, "faithfulness": 0.30, "completeness": 0.25, "coverage": 0.15}
TIE_MARGIN = 0.01

METRIC_LABELS = {
    "correctness": ("Correctness", "Are the facts right?"),
    "faithfulness": ("Faithfulness", "Does it stick to the evidence, with nothing made up?"),
    "completeness": ("Completeness", "Was anything important left out?"),
    "coverage": ("Coverage", "How many of the expected points it answered"),
}


# --------------------------------------------------------------------------- reading the reports

def score_value(value):
    """Return a metric as a float, or None if it is missing / not evaluable."""
    if isinstance(value, dict):
        value = value.get("score")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def real_error(value):
    """Reports sometimes store the text "none" instead of null - that is not an error."""
    if value is None or str(value).strip().lower() in ("", "none", "null"):
        return None
    return value


def metrics_from(evaluation):
    evaluation = evaluation or {}
    quality = (evaluation.get("worker_result_quality") or {}).get("metrics") or {}
    return {
        "correctness": score_value(quality.get("correctness")),
        "faithfulness": score_value(quality.get("faithfulness")),
        "completeness": score_value(quality.get("completeness")),
        "coverage": score_value(evaluation.get("coverage_score")),
    }


def weighted_score(metrics):
    """Weighted average of the available metrics (missing ones are skipped, weights rescaled)."""
    available = [(metrics[k], w) for k, w in WEIGHTS.items() if metrics.get(k) is not None]
    if not available:
        return None
    return sum(v * w for v, w in available) / sum(w for _, w in available)


def extract(report):
    """One entry per worker and per stage: its metrics, score and any problems."""
    result = {}
    for worker in report.get("workers") or []:
        ev = worker.get("evaluation") or {}
        result[f"worker:{worker.get('worker', 'unknown')}"] = {
            "metrics": metrics_from(ev),
            "status": worker.get("status"),
            "error": real_error(worker.get("error")),
            "scope_failed": (ev.get("scope_compliance") or {}).get("scope_policy_pass") is False,
            "finding_count": worker.get("finding_count"),
            "empty": (worker.get("finding_count") or 0) == 0,
        }
    for name, stage in (report.get("stages") or {}).items():
        ev = stage.get("benchmark_evaluation") or {}
        result[f"stage:{name}"] = {
            "metrics": metrics_from(ev),
            "status": stage.get("status"),
            "error": None,
            "scope_failed": (ev.get("scope_compliance") or {}).get("scope_policy_pass") is False,
            "finding_count": None,
            "empty": False,  # stages don't report findings the same way
        }
    for item in result.values():
        item["score"] = weighted_score(item["metrics"])
    return result


# --------------------------------------------------------------------------- checks

def compatibility_issues(a_report, b_report, common):
    """Reasons the two reports can't be compared fairly."""
    issues = []
    if a_report.get("run_id") and a_report.get("run_id") == b_report.get("run_id"):
        issues.append(f"Both reports have the same run ID ({a_report['run_id']}) - this is the same run twice.")
    for field in ("ground_truth", "evaluation_mode"):
        if a_report.get(field) != b_report.get(field):
            issues.append(f"Different {field}: {a_report.get(field)!r} vs {b_report.get(field)!r}")
    wa = {w.get("worker"): w for w in a_report.get("workers") or []}
    wb = {w.get("worker"): w for w in b_report.get("workers") or []}
    for worker in sorted(set(wa) & set(wb)):
        va = (wa[worker].get("evaluation") or {}).get("ground_truth_dataset_version")
        vb = (wb[worker].get("evaluation") or {}).get("ground_truth_dataset_version")
        if va != vb:
            issues.append(f"Different golden dataset version for {worker}: {va!r} vs {vb!r}")
    if not common:
        issues.append("No matching workers or stages found.")
    return issues


def blockers(report, components, label):
    """Critical failures that disqualify a version."""
    found = []
    if report.get("errors"):
        found.append(f"{label}: run contains errors")
    for name, item in components.items():
        if item["error"]:
            found.append(f"{label}: {name} has an error ({item['error']})")
        if item["scope_failed"]:
            found.append(f"{label}: {name} failed scope compliance")
        if item["status"] in ("failed", "error"):
            found.append(f"{label}: {name} status is {item['status']}")
    return found


def average(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


# --------------------------------------------------------------------------- comparison

def compare(file_a, file_b, name_a, name_b):
    a_report = json.loads(Path(file_a).read_text(encoding="utf-8"))
    b_report = json.loads(Path(file_b).read_text(encoding="utf-8"))
    a, b = extract(a_report), extract(b_report)

    common = sorted(set(a) & set(b), key=lambda k: (not k.startswith("worker"), k))
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    issues = compatibility_issues(a_report, b_report, common)

    # workers with no findings in either version carry no signal -> leave them out
    skipped = [k for k in common if a[k]["empty"] or b[k]["empty"]]
    scored = [k for k in common if k not in skipped]

    overall_a = average(a[k]["score"] for k in scored)
    overall_b = average(b[k]["score"] for k in scored)
    bad_a, bad_b = blockers(a_report, a, name_a), blockers(b_report, b, name_b)

    if issues:
        verdict, reason = "NO DECISION", " ".join(issues)
    elif bad_a and not bad_b:
        verdict, reason = name_b, f"{name_a} has critical failures: " + "; ".join(bad_a)
    elif bad_b and not bad_a:
        verdict, reason = name_a, f"{name_b} has critical failures: " + "; ".join(bad_b)
    elif bad_a and bad_b:
        verdict, reason = "NO CLEAR WINNER", "Both versions have critical failures."
    elif overall_a is None or overall_b is None:
        verdict, reason = "NO DECISION", "No usable scores were found."
    elif abs(overall_b - overall_a) <= TIE_MARGIN:
        verdict, reason = "TIE", f"Overall scores differ by {abs(overall_b - overall_a):.3f}, which is within the tie margin of {TIE_MARGIN:.2f}."
    else:
        verdict = name_b if overall_b > overall_a else name_a
        reason = (f"{verdict} has the higher overall score ({max(overall_a, overall_b):.3f} vs "
                  f"{min(overall_a, overall_b):.3f}) across {len(scored)} workers and stages.")

    # per-metric winner (average over the scored components)
    metric_summary = {}
    for m in WEIGHTS:
        ma = average(a[k]["metrics"][m] for k in scored)
        mb = average(b[k]["metrics"][m] for k in scored)
        if ma is None or mb is None:
            best = None
        elif abs(mb - ma) <= TIE_MARGIN:
            best = "TIE"
        else:
            best = name_b if mb > ma else name_a
        metric_summary[m] = {"a": ma, "b": mb, "best": best}

    rows = []
    for key in common:
        sa, sb = a[key]["score"], b[key]["score"]
        rows.append({
            "component": key, "score_a": sa, "score_b": sb,
            "delta": sb - sa if sa is not None and sb is not None else None,
            "metrics_a": a[key]["metrics"], "metrics_b": b[key]["metrics"],
            "findings_a": a[key]["finding_count"], "findings_b": b[key]["finding_count"],
            "skipped": key in skipped,
        })

    return {
        "name_a": name_a, "name_b": name_b,
        "run_id_a": a_report.get("run_id"), "run_id_b": b_report.get("run_id"),
        "verdict": verdict, "reason": reason,
        "overall_a": overall_a, "overall_b": overall_b,
        "metric_summary": metric_summary, "components": rows,
        "skipped": skipped, "only_in_a": only_a, "only_in_b": only_b,
        "blockers_a": bad_a, "blockers_b": bad_b, "compatibility_issues": issues,
    }


# --------------------------------------------------------------------------- markdown output

def fmt(value):
    return "—" if value is None else f"{value:.3f}"


def markdown(r):
    a, b = r["name_a"], r["name_b"]
    lines = [f"# Edrak comparison: {a} vs {b}", "",
             f"- {a}: `{r['run_id_a']}`", f"- {b}: `{r['run_id_b']}`", "",
             f"## Verdict: {r['verdict']}", "", r["reason"], "",
             f"| | {a} | {b} |", "|---|---:|---:|",
             f"| **Overall score** | {fmt(r['overall_a'])} | {fmt(r['overall_b'])} |", "",
             "## Best version per metric", "", f"| Metric | {a} | {b} | Best |", "|---|---:|---:|---|"]
    for m, s in r["metric_summary"].items():
        lines.append(f"| {m} | {fmt(s['a'])} | {fmt(s['b'])} | {s['best'] or '—'} |")
    lines += ["", "## Worker and stage scores", "", f"| Component | {a} | {b} | Difference (B - A) |", "|---|---:|---:|---:|"]
    for row in r["components"]:
        note = " (skipped: no findings)" if row["skipped"] else ""
        lines.append(f"| {row['component']}{note} | {fmt(row['score_a'])} | {fmt(row['score_b'])} | {fmt(row['delta'])} |")
    lines += ["", "## Metric breakdown", ""]
    for row in r["components"]:
        lines += [f"### {row['component']}", "", f"| Metric | {a} | {b} |", "|---|---:|---:|"]
        for m in WEIGHTS:
            lines.append(f"| {m} | {fmt(row['metrics_a'].get(m))} | {fmt(row['metrics_b'].get(m))} |")
        lines.append("")
    sections = [
        ("Compatibility issues", r["compatibility_issues"]),
        (f"{a} critical issues", r["blockers_a"]),
        (f"{b} critical issues", r["blockers_b"]),
        ("Left out of the overall score (no findings)", r["skipped"]),
        (f"Only in {a}", r["only_in_a"]),
        (f"Only in {b}", r["only_in_b"]),
    ]
    for heading, items in sections:
        if items:
            lines += [f"## {heading}", ""] + [f"- {x}" for x in items] + [""]
    lines += ["## Scoring", "",
              "Correctness 30%, faithfulness 30%, completeness 25%, coverage 15%; missing metrics are skipped and the weights rescaled.",
              f"Overall score = simple average of workers and stages. A difference of {TIE_MARGIN} or less is a tie.",
              "This is a practical comparison, not a statistical test.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- html output

def html_page(r):
    data = json.dumps({**r, "weights": WEIGHTS, "labels": METRIC_LABELS, "tie_margin": TIE_MARGIN})
    return HTML_TEMPLATE.replace("__DATA__", data.replace("</", "<\\/"))


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Edrak comparison</title>
<style>
  :root {
    --page:#f5f4f1; --card:#fcfcfb; --soft:#f0efec; --line:#e2e1dc;
    --text:#0b0b0b; --text2:#52514e; --muted:#7a7974;
    --a:#2a78d6; --b:#eb6834;
    --good:#0ca30c; --good-text:#006300; --good-bg:#e6f4e6;
    --bad:#d03b3b; --bad-text:#a92b2b; --bad-bg:#fbe9e9;
    --tie-bg:#ecebe7;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --page:#111110; --card:#1a1a19; --soft:#232322; --line:#33332f;
      --text:#f0efec; --text2:#c3c2b7; --muted:#8f8e86;
      --a:#3987e5; --b:#d95926;
      --good-text:#5fd35f; --good-bg:#16301a; --bad-text:#f08a8a; --bad-bg:#3a1c1c; --tie-bg:#2a2a27;
    }
  }
  * { box-sizing:border-box }
  body { margin:0; background:var(--page); color:var(--text);
         font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif }
  .wrap { max-width:900px; margin:0 auto; padding:32px 20px 60px }
  h1 { font-size:22px; margin:0 0 4px }
  h2 { font-size:16px; margin:0 0 4px }
  .sub { color:var(--text2); font-size:13px }
  .mono { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12px }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin-top:16px }
  .dot { width:10px; height:10px; border-radius:3px; display:inline-block; vertical-align:middle; margin-right:6px }

  .verdict { display:flex; gap:16px; align-items:center; border-radius:12px; padding:20px; margin-top:20px }
  .verdict .icon { width:44px; height:44px; border-radius:50%; display:grid; place-items:center;
                   font-size:22px; font-weight:700; color:#fff; flex:none }
  .verdict .small { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--text2) }
  .verdict .big { font-size:24px; font-weight:700 }
  .win { background:var(--good-bg) } .win .icon { background:var(--good) }
  .tie { background:var(--tie-bg) }  .tie .icon { background:var(--muted) }
  .stop { background:var(--bad-bg) } .stop .icon { background:var(--bad) }

  .scores { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:16px }
  .score { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 20px; position:relative }
  .score .name { color:var(--text2); font-size:14px }
  .score .num { font-size:34px; font-weight:700; font-variant-numeric:tabular-nums }
  .score.best { border:2px solid var(--good) }
  .score .crown { position:absolute; top:14px; right:16px; font-size:12px; font-weight:600;
                  background:var(--good-bg); color:var(--good-text); padding:2px 10px; border-radius:999px }

  table { width:100%; border-collapse:collapse; font-size:14px; margin-top:10px }
  th, td { padding:10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:middle }
  th { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--text2); background:var(--soft) }
  td.num, th.num { text-align:right; font-variant-numeric:tabular-nums }
  .help { display:block; font-size:12px; color:var(--muted) }
  .bold { font-weight:700 }
  .badge { display:inline-block; font-size:13px; font-weight:600; padding:3px 10px; border-radius:999px; white-space:nowrap }
  .badge.wa { background:color-mix(in srgb,var(--a) 18%,transparent) }
  .badge.wb { background:color-mix(in srgb,var(--b) 20%,transparent) }
  .badge.t  { background:var(--tie-bg); color:var(--text2) }
  .up { color:var(--good-text); font-weight:600 } .down { color:var(--bad-text); font-weight:600 } .flat { color:var(--muted) }
  tr.total td { background:var(--soft); font-weight:600; border-top:2px solid var(--line) }

  .bars { display:grid; grid-template-columns:170px 1fr 96px; gap:10px 14px; align-items:center; margin-top:12px }
  .bars .label { font-size:13px }
  .bars .label small { display:block; color:var(--muted); font-size:11px }
  .track { display:flex; flex-direction:column; gap:3px }
  .bar { height:12px; border-radius:0 4px 4px 0; min-width:2px }
  .bar.a { background:var(--a) } .bar.b { background:var(--b) }
  .legend { font-size:13px; color:var(--text2); display:flex; gap:16px; margin-top:6px }
  .grid { width:100%; border-collapse:separate; border-spacing:4px; margin-top:12px; table-layout:fixed }
  .grid th { background:none; text-align:center; padding:4px; font-size:11.5px }
  .grid th:first-child { text-align:left; width:180px }
  .grid td { border:none; padding:0; height:52px; text-align:center; border-radius:8px; font-variant-numeric:tabular-nums }
  .grid td.name { text-align:left; padding-left:4px; font-size:13.5px }
  .grid td.name small { display:block; color:var(--muted); font-size:11px }
  .grid .g { font-size:15px; font-weight:600 }
  .grid .w { font-size:11px; color:var(--text2); display:block }
  .grid td.tie { background:var(--soft); color:var(--muted) }
  .grid td.na { background:transparent; color:var(--muted); border:1px dashed var(--line) }
  .grid tr.skip td:not(.name) { opacity:.35 } .grid tr.skip td.name { color:var(--muted) }
  .grid td.total { outline:2px solid var(--line); outline-offset:-2px }
  .grid .exact { font-size:13px; line-height:1.35 } .grid .exact b { font-weight:700 }
  .gridhead { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap }
  .keys { display:flex; gap:14px; font-size:12.5px; color:var(--text2); align-items:center; margin-top:8px; flex-wrap:wrap }
  .keys .sw { width:26px; height:12px; border-radius:3px; display:inline-block; vertical-align:middle; margin-right:5px }
  .toggle { font-size:13px; color:var(--text2); display:inline-flex; gap:6px; align-items:center; cursor:pointer }
  ul { margin:8px 0 0; padding-left:20px } li { margin:4px 0; color:var(--text2); font-size:14px }
  @media (max-width:640px) { .scores { grid-template-columns:1fr } .bars { grid-template-columns:110px 1fr 84px } }
</style>
</head>
<body>
<div class="wrap">
  <h1 id="title"></h1>
  <div class="sub mono" id="runs"></div>

  <div class="verdict" id="verdict"></div>
  <div class="scores" id="scores"></div>

  <div class="card">
    <h2>Which version is best on each metric?</h2>
    <div class="sub">Each metric is averaged over the workers and stages (workers with no findings are left out). A difference of <span id="tm"></span> or less counts as a tie.</div>
    <table id="metrics"></table>
  </div>

  <div class="card">
    <h2>Score for each worker and stage</h2>
    <div class="sub">Worker rows show how many findings each version produced (first / second). Faded rows had no findings, so they are not scored. On the right: the Optimized score, the GraphRAG score, and the difference (second minus first).</div>
    <div class="legend" id="legend"></div>
    <div class="bars" id="bars"></div>
  </div>

  <div class="card">
    <div class="gridhead">
      <div><h2>Who wins each metric, for each worker and stage</h2>
        <div class="sub">The color shows which version scored higher, and the number shows by how much.
          Stronger color means a bigger gap. Hover over a cell to see both scores.</div></div>
      <label class="toggle"><input type="checkbox" id="exact"> Show exact scores</label>
    </div>
    <div class="keys" id="keys"></div>
    <div class="scroll-x"><table class="grid" id="grid"></table></div>
  </div>

  <div class="card" id="problems"></div>

  <p class="sub">Score = correctness 30% + faithfulness 30% + completeness 25% + coverage 15%.
     The overall score is the simple average of the scored workers and stages.
     This is a practical comparison, not a statistical test.</p>
</div>

<script>
const R = __DATA__;
const A = R.name_a, B = R.name_b, TM = R.tie_margin;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const f = v => v == null ? "—" : v.toFixed(3);
const pretty = k => k.replace(/^(worker|stage):/, "").replace(/_/g, " ");
const dot = side => `<span class="dot" style="background:var(--${side})"></span>`;

function diff(a, b) {
  if (a == null || b == null) return `<span class="flat">—</span>`;
  const d = b - a;
  if (Math.abs(d) <= TM) return `<span class="flat">${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(3)}</span>`;
  return d > 0 ? `<span class="up">▲ +${d.toFixed(3)}</span>` : `<span class="down">▼ −${Math.abs(d).toFixed(3)}</span>`;
}
function best(a, b) {
  if (a == null || b == null) return {html:`<span class="badge t">n/a</span>`, w:null};
  if (Math.abs(b - a) <= TM) return {html:`<span class="badge t">= Tie</span>`, w:"tie"};
  return b > a ? {html:`<span class="badge wb">${dot("b")}${esc(B)}</span>`, w:"b"}
               : {html:`<span class="badge wa">${dot("a")}${esc(A)}</span>`, w:"a"};
}

// header
$("title").textContent = `${A} vs ${B}`;
$("runs").textContent = `${A}: ${R.run_id_a ?? "?"}   ·   ${B}: ${R.run_id_b ?? "?"}`;
$("tm").textContent = TM.toFixed(2);
$("legend").innerHTML = `<span>${dot("a")}${esc(A)}</span><span>${dot("b")}${esc(B)}</span>`;

// verdict
(() => {
  const v = R.verdict;
  let cls = "tie", icon = "=", small = "Result", plain = "";
  if (v === A || v === B) {
    cls = "win"; icon = "✓"; small = "Winner";
    plain = `${v} is the better version.`;
  } else if (v === "TIE") {
    plain = "Both versions performed about the same.";
  } else {
    cls = "stop"; icon = "!"; small = "Can't decide";
    plain = "The script couldn't pick a winner. The reason is below.";
  }
  $("verdict").className = "verdict " + cls;
  $("verdict").innerHTML = `<div class="icon">${icon}</div>
    <div><div class="small">${small}</div><div class="big">${esc(v)}</div>
    <div>${esc(plain)}</div><div class="sub">${esc(R.reason)}</div></div>`;
})();

// overall score boxes
(() => {
  const ov = best(R.overall_a, R.overall_b);
  const box = (name, val, side, isBest) => `<div class="score ${isBest ? "best" : ""}">
      ${isBest ? `<span class="crown">Higher score</span>` : ""}
      <div class="name">${dot(side)}${esc(name)} — overall score</div>
      <div class="num">${f(val)}</div></div>`;
  $("scores").innerHTML = box(A, R.overall_a, "a", ov.w === "a") + box(B, R.overall_b, "b", ov.w === "b");
})();

// best by metric
(() => {
  let rows = "";
  for (const m of Object.keys(R.weights)) {
    const s = R.metric_summary[m] || {}, a = s.a ?? null, b = s.b ?? null, w = best(a, b);
    const [name, help] = R.labels[m] || [m, ""];
    rows += `<tr><td><b>${esc(name)}</b> <span class="sub">(${Math.round(R.weights[m] * 100)}% of score)</span>
        <span class="help">${esc(help)}</span></td>
      <td class="num ${w.w === "a" ? "bold" : ""}">${f(a)}</td>
      <td class="num ${w.w === "b" ? "bold" : ""}">${f(b)}</td>
      <td class="num">${diff(a, b)}</td><td>${w.html}</td></tr>`;
  }
  const ov = best(R.overall_a, R.overall_b);
  rows += `<tr class="total"><td>Overall<span class="help">All metrics combined</span></td>
    <td class="num">${f(R.overall_a)}</td><td class="num">${f(R.overall_b)}</td>
    <td class="num">${diff(R.overall_a, R.overall_b)}</td><td>${ov.html}</td></tr>`;
  $("metrics").innerHTML = `<thead><tr><th>Metric</th><th class="num">${dot("a")}${esc(A)}</th>
    <th class="num">${dot("b")}${esc(B)}</th><th class="num">Difference</th><th>Best</th></tr></thead><tbody>${rows}</tbody>`;
})();

// bars per worker / stage
$("bars").innerHTML = R.components.map(c => {
  const a = c.score_a ?? 0, b = c.score_b ?? 0, sk = c.skipped ? ' style="opacity:.4"' : "";
  const kind = c.component.startsWith("stage") ? "stage" : `worker · findings ${c.findings_a ?? "?"} / ${c.findings_b ?? "?"}`;
  return `<div class="label"${sk}>${esc(pretty(c.component))}<small>${kind}</small></div>
    <div class="track"${sk} title="${esc(A)}: ${f(c.score_a)}  |  ${esc(B)}: ${f(c.score_b)}">
      <div class="bar a" style="width:${a * 100}%"></div><div class="bar b" style="width:${b * 100}%"></div></div>
    <div class="num" style="font-size:13px;text-align:right;line-height:1.4">
      <div style="white-space:nowrap">${dot("a")}${f(c.score_a)}</div>
      <div style="white-space:nowrap">${dot("b")}${f(c.score_b)}</div>
      <div>${c.skipped ? '<span class="flat">not scored</span>' : diff(c.score_a, c.score_b)}</div></div>`;
}).join("");

// winner grid
(() => {
  const metrics = Object.keys(R.weights);
  const tint = (side, gap) => {
    const pct = Math.round(14 + Math.min(gap / 0.3, 1) * 46);   // 14% .. 60% color strength
    return `background:color-mix(in srgb,var(--${side}) ${pct}%,var(--card))`;
  };
  const cell = (a, b, extraCls = "") => {
    if (a == null || b == null) return `<td class="na ${extraCls}" title="Not measured">n/a</td>`;
    const tipTxt = `${A}: ${a.toFixed(3)}   |   ${B}: ${b.toFixed(3)}`;
    const exact = `<div class="exact">${dot("a")}${a.toFixed(2)}<br>${dot("b")}${b.toFixed(2)}</div>`;
    const gap = Math.abs(b - a);
    if (gap <= TM) return `<td class="tie ${extraCls}" title="${esc(tipTxt)}"><span class="sum"><span class="g">=</span><span class="w">tie</span></span>${exact}</td>`;
    const side = b > a ? "b" : "a", who = b > a ? B : A;
    return `<td class="${extraCls}" style="${tint(side, gap)}" title="${esc(tipTxt)}">
      <span class="sum"><span class="g">+${gap.toFixed(2)}</span><span class="w">${esc(who)}</span></span>${exact}</td>`;
  };
  let h = `<thead><tr><th></th>${metrics.map(m => `<th>${esc(R.labels[m]?.[0] ?? m)}</th>`).join("")}<th>Row score</th></tr></thead><tbody>`;
  for (const c of R.components) {
    const kind = c.component.startsWith("stage") ? "stage" : "worker";
    h += `<tr class="${c.skipped ? "skip" : ""}"><td class="name">${esc(pretty(c.component))}<small>${kind}${c.skipped ? " · not scored" : ""}</small></td>
      ${metrics.map(m => cell(c.metrics_a[m], c.metrics_b[m])).join("")}${cell(c.score_a, c.score_b, "total")}</tr>`;
  }
  $("grid").innerHTML = h + `</tbody>`;
  $("keys").innerHTML = `<span><i class="sw" style="${tint("a", .2)}"></i>${esc(A)} higher</span>
    <span><i class="sw" style="${tint("b", .2)}"></i>${esc(B)} higher</span>
    <span><i class="sw" style="background:var(--soft)"></i>tie (gap of ${TM.toFixed(2)} or less)</span>
    <span><i class="sw" style="border:1px dashed var(--line)"></i>n/a = couldn't be measured</span>`;
  const apply = () => {
    const on = $("exact").checked;
    document.querySelectorAll("#grid .exact").forEach(e => e.style.display = on ? "block" : "none");
    document.querySelectorAll("#grid .sum").forEach(e => e.style.display = on ? "none" : "inline");
  };
  $("exact").onchange = apply; apply();
})();

// problems
(() => {
  const groups = [
    ["Reports can't be compared", R.compatibility_issues],
    [`${A} problems`, R.blockers_a],
    [`${B} problems`, R.blockers_b],
    ["Left out of the overall score (no findings in at least one version)", (R.skipped || []).map(pretty)],
    [`Only in ${A}`, R.only_in_a],
    [`Only in ${B}`, R.only_in_b],
  ].filter(([, items]) => items && items.length);
  $("problems").innerHTML = `<h2>Problems found</h2>` + (groups.length
    ? groups.map(([t, items]) => `<p style="margin:12px 0 0"><b>${esc(t)}</b></p><ul>${items.map(i => `<li>${esc(i)}</li>`).join("")}</ul>`).join("")
    : `<div class="sub" style="margin-top:6px">✓ No problems — both reports are valid and comparable.</div>`);
})();
</script>
</body>
</html>
"""

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report_a", type=Path, help="first report, e.g. optimized.json")
    parser.add_argument("report_b", type=Path, help="second report, e.g. graphrag.json")
    parser.add_argument("--name-a", default="Version A")
    parser.add_argument("--name-b", default="Version B")
    parser.add_argument("--out", default="comparison_report.md")
    parser.add_argument("--json", default="comparison_report.json")
    parser.add_argument("--html", default="comparison_report.html")
    args = parser.parse_args()

    result = compare(args.report_a, args.report_b, args.name_a, args.name_b)
    report_md = markdown(result)
    Path(args.out).write_text(report_md, encoding="utf-8")
    Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
    Path(args.html).write_text(html_page(result), encoding="utf-8")
    print(report_md)
    print(f"\nSaved: {args.out}\nSaved: {args.json}\nSaved: {args.html}  <- open this in your browser")


if __name__ == "__main__":
    main()