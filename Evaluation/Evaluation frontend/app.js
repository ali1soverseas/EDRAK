(function () {
  "use strict";

  const params = new URLSearchParams(location.search);
  // Static snapshots in ./data by default; pass ?api=<backend url> to read the live API instead.
  const LIVE = params.get("api");
  const API = LIVE ? LIVE.replace(/\/$/, "") + "/api/evaluation" : null;
  const uploadedRuns = new Map();
  const uploadedComparisons = new Map();
  let availableComparisons = [];
  const UPLOAD_DB = "edrak-evaluation-uploads";
  let uploadDbPromise;

  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isNum = (v) => typeof v === "number" && !Number.isNaN(v);
  const num = (v) => (isNum(v) ? v : 0);
  document.querySelectorAll(".files dl > div").forEach((metric) => {
    if (metric.querySelector("dt")?.textContent.trim().toLowerCase() === "warnings") metric.remove();
  });
  const pct = (v) => (isNum(v) ? Math.round(v * 1000) / 10 + "%" : "–");
  // "context_relevancy" -> "Context relevancy"
  const humanize = (s) => {
    const t = String(s ?? "").replace(/_/g, " ").trim();
    return (t.charAt(0).toUpperCase() + t.slice(1)).replace(/^Rag\b/, "RAG");
  };
  const metricDisplayValue = (key, value) => {
    if (value == null) return /(^|_)error($|_)/i.test(key) ? "None" : "Not available";
    if (key === "execution_quality_status") return humanize(value);
    return String(value);
  };
  // "workers/competitor_intelligence" -> "Workers · Competitor intelligence"
  const stageName = (n) => String(n).split("/").map(humanize).join(" · ");

  function openUploadDb() {
    if (!("indexedDB" in window)) return Promise.reject(new Error("This browser does not support persistent uploaded runs."));
    if (!uploadDbPromise) {
      uploadDbPromise = new Promise((resolve, reject) => {
        const request = indexedDB.open(UPLOAD_DB, 1);
        request.onupgradeneeded = () => {
          if (!request.result.objectStoreNames.contains("runs")) {
            request.result.createObjectStore("runs", { keyPath: "id" });
          }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("Could not open uploaded-run storage."));
        request.onblocked = () => reject(new Error("Uploaded-run storage is blocked by another open dashboard tab."));
      });
    }
    return uploadDbPromise;
  }

  function renderSidebarSavedRuns() {
    const list = $("sidebar-saved-runs");
    if (!list) return;
    list.replaceChildren();
    if (!uploadedRuns.size) {
      const empty = document.createElement("li");
      empty.className = "saved-runs-empty";
      empty.textContent = "No uploaded runs yet";
      list.append(empty);
      return;
    }
    const selectedId = $("run-select").value;
    uploadedRuns.forEach((run, id) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "saved-run-button";
      button.dataset.runId = id;
      button.textContent = run.label || run.run_id || id;
      button.title = button.textContent;
      if (id === selectedId) button.setAttribute("aria-current", "true");
      item.append(button);
      list.append(item);
    });
  }

  async function readSavedUploads() {
    const db = await openUploadDb();
    return new Promise((resolve, reject) => {
      const request = db.transaction("runs", "readonly").objectStore("runs").getAll();
      request.onsuccess = () => resolve(request.result || []);
      request.onerror = () => reject(request.error || new Error("Could not load saved uploaded runs."));
    });
  }

  async function saveUploadedRun(record) {
    const db = await openUploadDb();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction("runs", "readwrite");
      transaction.objectStore("runs").put(record);
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error || new Error("Could not save the uploaded run."));
      transaction.onabort = () => reject(transaction.error || new Error("Saving the uploaded run was cancelled."));
    });
  }

  async function deleteSavedUpload(id) {
    const db = await openUploadDb();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction("runs", "readwrite");
      transaction.objectStore("runs").delete(id);
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error || new Error("Could not delete the uploaded run."));
      transaction.onabort = () => reject(transaction.error || new Error("Deleting the uploaded run was cancelled."));
    });
  }

  function addUploadedRun(record, selectRun = false) {
    uploadedRuns.set(record.id, record.run);
    const select = $("run-select");
    select.disabled = false;
    if (![...select.options].some((option) => option.value === record.id)) {
      const savedAt = record.saved_at_utc ? new Date(record.saved_at_utc).toLocaleString() : "";
      select.add(new Option(`Uploaded · ${record.run.label}${savedAt ? ` · ${savedAt}` : ""}`, record.id));
    }
    record.comparisons.forEach((comparison, index) => {
      uploadedComparisons.set(`${record.id}-comparison-${index + 1}`, comparison);
    });
    addUploadedComparisonOptions();
    renderSidebarSavedRuns();
    updateDeleteUploadButton();
    if (selectRun) {
      select.value = record.id;
      updateDeleteUploadButton();
      showRun(record.id);
    }
  }

  function addUploadedComparisonOptions() {
    const compareSelect = $("compare-select");
    uploadedComparisons.forEach((comparison, id) => {
      if (![...compareSelect.options].some((option) => option.value === id)) {
        compareSelect.add(new Option(
          `${comparison.ground_truth || "Ground truth"} vs ${comparison.run_bundle || "uploaded run"}`,
          id
        ));
      }
    });
  }

  function normalizedBundleName(value) {
    return String(value || "")
      .replace(/[\\/]+$/, "")
      .split(/[\\/]/)
      .pop()
      .replace(/\.json$/i, "")
      .toLowerCase();
  }

  function comparisonForRun(runId) {
    const uploaded = [...uploadedComparisons.entries()].find(([id]) =>
      id.startsWith(`${runId}-comparison-`));
    if (uploaded) return uploaded[0];

    const selected = normalizedBundleName(runId);
    if (!selected) return "";
    const candidates = availableComparisons
      .map((comparison) => ({
        id: comparison.id,
        bundle: normalizedBundleName(comparison.run_bundle),
      }))
      .filter((comparison) => comparison.bundle);
    const exact = candidates.find((comparison) => comparison.bundle === selected);
    if (exact) return exact.id;
    const related = candidates
      .filter(({ bundle }) => selected.startsWith(bundle) || bundle.startsWith(selected))
      .sort((a, b) => b.bundle.length - a.bundle.length);
    return related[0]?.id || "";
  }

  function selectComparisonForRun(runId) {
    const comparisonId = comparisonForRun(runId);
    const select = $("compare-select");
    if (!comparisonId || ![...select.options].some((option) => option.value === comparisonId)) {
      if (!runId) return false;
      select.value = "";
      clearCompare();
      setStatus($("compare-status"), "info", "No ground-truth comparison file matches the selected run.");
      return true;
    }
    select.value = comparisonId;
    showCompare(comparisonId);
    return true;
  }

  function updateDeleteUploadButton() {
    const button = $("delete-uploaded-run");
    if (button) button.disabled = !uploadedRuns.has($("run-select").value);
  }

  const KPI_STATUS = { good: "Good", mid: "Needs attention", bad: "Problem" };
  const kpi = (label, value, note, cls) =>
    `<div class="kpi ${cls || ""}"><div class="label">${esc(label)}</div>` +
    `<div class="value">${esc(value)}</div><div class="note">${esc(note || "")}</div>` +
    (KPI_STATUS[cls] ? `<span class="sr">Status: ${KPI_STATUS[cls]}</span>` : "") + `</div>`;

  // Small tile used for per-metric values (replaces plain "label: value" lists)
  const kv = (label, value, note) =>
    `<div class="kv"><span>${esc(label)}</span><b>${esc(value)}</b>${note ? `<small>${esc(note)}</small>` : ""}</div>`;

  // Thin proportional bar used inside table cells
  const meter = (v, cls) =>
    isNum(v) ? `<span class="meter ${cls || ""}"><i style="width:${Math.max(0, Math.min(100, v * 100))}%"></i></span>` : "";

  // ---------- Pipeline stage bar (shown on Overview and Pipeline) ----------
  // Order here is the order of the bar: Orchestrator, Workers, RAG, Verification, Cross signal, Decision.
  const PIPELINE_STAGES = [
    ["orchestration", "Orchestrator", /^orchestrat/i],
    ["workers", "Workers", /^(workers?|competitor_intelligence|internal_intelligence|market_intelligence|customer_trends)$/i],
    ["rag", "RAG", /^rag/i],
    ["verification", "Verification", /^verif/i],
    ["cross_signal", "Cross signal", /^cross/i],
    ["decision", "Decision", /^decision/i],
  ];
  const isVisibleEvaluationAgent = (agent) =>
    !["metrics", "telemetry"].includes(String(agent || "").toLowerCase());
  const visibleChecks = (checks) =>
    checks.filter((check) => check.passed === true || check.severity === "error");
  // Order of the report cards on the Pipeline page: Orchestrator and RAG sit next to each other.
  const AGENT_ORDER = ["orchestration", "rag", "workers", "verification", "cross_signal", "decision"];
  function agentRank(agent) {
    const stage = PIPELINE_STAGES.find(([, , test]) => test.test(String(agent.agent || "")));
    const rank = stage ? AGENT_ORDER.indexOf(stage[0]) : -1;
    return rank < 0 ? AGENT_ORDER.length : rank;
  }

  // Colours each stage from the integrity checks already in the run (no new metrics).
  function updateStageBar(agents) {
    document.querySelectorAll(".sb-item").forEach((item) => {
      const stage = PIPELINE_STAGES.find(([key]) => key === item.dataset.stage);
      const matched = stage ? agents.filter((a) => stage[2].test(String(a.agent || ""))) : [];
      const checks = visibleChecks(matched.flatMap((a) => (a.file_details || []).flatMap((f) => f.checks || [])));
      const failedN = checks.filter((c) => c.passed !== true && c.severity === "error").length;
      const tone = !matched.length ? "" : failedN ? "err" : "ok";
      const text = !matched.length ? (agents.length ? "Not in this run" : "Awaiting run")
        : failedN ? "Failed checks" : "All checks passed";
      item.classList.remove("ok", "warn", "err");
      if (tone) item.classList.add(tone);
      const state = item.querySelector(".sb-state");
      if (state) state.textContent = text;
    });
  }

  // Every stage opens the Pipeline page (the router handles the #pipeline link) and then
  // jumps to that stage's cards when the run has them.
  function initStageBar() {
    const items = [...document.querySelectorAll(".sb-item")];
    document.addEventListener("click", (e) => {
      const item = e.target.closest(".sb-item");
      if (!item) {
        if (e.target.closest(".tabs a")) items.forEach((i) => i.removeAttribute("aria-current"));
        return;
      }
      items.forEach((i) => (i === item ? i.setAttribute("aria-current", "location") : i.removeAttribute("aria-current")));
      const stage = PIPELINE_STAGES.find(([key]) => key === item.dataset.stage);
      requestAnimationFrame(() => {
        const target = [...document.querySelectorAll("#pipeline-agent-results [data-agent]")]
          .find((el) => stage && stage[2].test(el.dataset.agent));
        if (!target) return;
        if (target.tagName === "DETAILS") target.open = true;
        target.scrollIntoView({ block: "start" });
      });
    });
  }

  // ---------- Data access ----------
  async function get(path) {
    const comparisonMatch = path.match(/^\/comparisons\/([^/]+)$/);
    if (comparisonMatch && uploadedComparisons.has(decodeURIComponent(comparisonMatch[1]))) {
      return uploadedComparisons.get(decodeURIComponent(comparisonMatch[1]));
    }
    const url = API ? API + path : "data" + path + ".json";
    let r;
    try {
      r = await fetch(url, API ? { credentials: "include" } : undefined);
    } catch (e) {
      throw new Error(
        API
          ? "Couldn't reach the backend at " + LIVE + ". Check the URL and that the server is running."
          : "Couldn't load the data files. Open this page through a web server (not file://) and make sure the data folder exists."
      );
    }
    if (r.status === 401) throw new Error("Not signed in to the backend. Sign in to the EDRAK app in this browser, then reload.");
    if (r.status === 404 && !API) throw new Error("No snapshot data found (data" + path + ".json).");
    if (!r.ok) throw new Error("Request failed (" + r.status + ").");
    return r.json();
  }

  // ---------- UI helpers ----------
  // kind: "loading" | "error" | "info" | "ok" | "" (clears)
  function setStatus(el, kind, text) {
    el.className = "status" + (kind ? " " + kind : "");
    el.textContent = text || "";
  }

  function fill(select, items, label, emptyText) {
    select.innerHTML = items.length
      ? items.map((i) => `<option value="${esc(i.value)}">${esc(label(i))}</option>`).join("")
      : `<option value="">${esc(emptyText || "Nothing available")}</option>`;
    select.disabled = items.length === 0;
  }

  const tableBox = (tableId) => $(tableId).parentElement;

  function clearRun() {
    $("results-kpis").innerHTML = "";
    $("overview-attention").textContent = "";
    $("pipeline-agent-results").innerHTML = "";
    $("snapshot-generated").textContent = "–";
    $("snapshot-model").textContent = "–";
    $("snapshot-run-id").textContent = "–";
    $("snapshot-scope").textContent = "–";
    $("sidebar-run-id").textContent = "No run selected";
    $("top-snapshot").textContent = "No snapshot loaded";
    $("top-judge").textContent = "Judge model unavailable";
    $("overview-lede").textContent = "Run-level quality and integrity summary for the selected evaluation.";
    $("run-goal").hidden = true;
    $("run-goal").innerHTML = "";
    updateStageBar([]);
  }

  function clearCompare() {
    $("compare-kpis").innerHTML = "";
    $("compare-meta").innerHTML = "";
    $("compare-meta").hidden = true;
    $("compare-table").innerHTML = "";
    tableBox("compare-table").hidden = true;
  }

  // ---------- Evaluation results ----------
  const LLM_METRICS = new Set(["faithfulness", "relevancy", "context_relevancy", "specificity", "insight", "actionability"]);
  const DISPLAY_METRICS = [
    ["faithfulness", "Faithfulness"],
    ["relevancy", "Relevancy"],
    ["context_relevancy", "Context relevancy"],
    ["specificity", "Specificity"],
    ["insight", "Insight"],
    ["actionability", "Actionability"],
  ];
  const DOMAIN_ONLY_AGENTS = new Set(["orchestration", "rag", "telemetry"]);
  let runToken = 0; // ignore out-of-date responses when the user switches runs quickly

  function uploadedDomainMetrics(agent, files) {
    const metrics = files.map((file) => file.metrics || {});
    const values = (name) => metrics.map((item) => item[name]).filter(isNum);
    const mean = (name) => {
      const items = values(name);
      return items.length ? Math.round(items.reduce((sum, value) => sum + value, 0) / items.length * 1000) / 1000 : null;
    };
    const total = (name) => metrics.reduce((sum, item) => sum + num(item[name]), 0);

    if (agent === "orchestration") {
      return {
        task_completion_pct: mean("task_completion_pct"),
        workflow_success_pct: mean("workflow_success_pct"),
        dependency_integrity_pct: mean("dependency_integrity_pct"),
        failure_rate_pct: mean("failure_rate_pct"),
        retry_rate_pct: mean("retry_rate_pct"),
        execution_duration_seconds: mean("execution_duration_seconds"),
        planned_tasks: total("planned_tasks"),
        failed_tasks: total("failed_tasks"),
        dependency_edges: total("dependency_edges"),
        dependency_violations: total("dependency_violations"),
        missing_tasks: total("missing_tasks"),
        missing_dependencies: total("missing_dependencies"),
      };
    }

    if (agent === "rag") {
      const ragMetric = (name) => {
        const scores = values(`${name}_score`);
        const explanations = [...new Set(metrics.flatMap((item) => {
          const explanation = item[`${name}_reason`] || item[`${name}_error`];
          return typeof explanation === "string" && explanation.trim() ? [explanation.trim()] : [];
        }))];
        return {
          score: scores.length
            ? Math.round(scores.reduce((sum, score) => sum + score, 0) / scores.length * 1000) / 1000
            : null,
          reason: explanations.join("; "),
        };
      };
      const latencyPairs = metrics
        .filter((item) => isNum(item.retrieval_latency_ms))
        .map((item) => [item.retrieval_latency_ms, num(item.retrieval_latency_calls)])
        .filter(([, weight]) => weight > 0);
      const latencyWeight = latencyPairs.reduce((sum, [, weight]) => sum + weight, 0);
      const retrievalScores = metrics
        .filter((item) => isNum(item.mean_retrieval_score))
        .map((item) => [item.mean_retrieval_score, num(item.retrieved_chunks)]);
      const retrievedChunks = total("retrieved_chunks");
      const answerSources = [...new Set(metrics.map((item) => item.answer_source).filter(Boolean))].sort();
      return {
        context_precision: ragMetric("context_precision"),
        context_recall: ragMetric("context_recall"),
        context_relevancy: ragMetric("context_relevancy"),
        retrieval_latency_ms: latencyWeight
          ? Math.round(latencyPairs.reduce((sum, [latency, weight]) => sum + latency * weight, 0) / latencyWeight * 1000) / 1000
          : mean("retrieval_latency_ms"),
        queries: total("queries"),
        retrieved_chunks: retrievedChunks,
        mean_retrieval_score: retrievedChunks
          ? Math.round(retrievalScores.reduce((sum, [score, count]) => sum + score * count, 0) / retrievedChunks * 1000) / 1000
          : null,
        answer_source: answerSources.join(", ") || null,
        expected_output_source: [...new Set(metrics.map((item) => item.expected_output_source).filter(Boolean))].join(", ") || null,
      };
    }

    return {};
  }

  function uploadedAgentSummary(report) {
    const files = Array.isArray(report.files) ? report.files : [];
    const checks = visibleChecks(files.flatMap((file) => (file.checks || []).map((check) => ({
      file: file.file, check: check.check, passed: check.passed,
      detail: check.detail, severity: check.severity,
    }))));
    const flaggedUnits = files.flatMap((file) => (file.flagged_units || []).map((unit) => ({
      ...unit, file: file.file,
    })));
    const values = {};
    files.forEach((file) => {
      const metrics = file.metrics || {};
      Object.entries(metrics).forEach(([key, value]) => {
        if (key.endsWith("_avg") && isNum(value)) {
          const name = key.slice(0, -4);
          (values[name] ||= []).push(value);
        }
      });
      [["context_relevancy_score", "context_relevancy"]].forEach(([key, name]) => {
        if (isNum(metrics[key])) (values[name] ||= []).push(metrics[key]);
      });
    });
    const failedChecks = checks.filter((check) => check.passed !== true && check.severity === "error");
    const passedChecks = checks.filter((check) => check.passed === true);
    return {
      agent: report.agent,
      model: report.model,
      generated_at_utc: report.generated_at_utc,
      dry_run: report.dry_run,
      thresholds: report.thresholds || {},
      files: files.length,
      checks: checks.length,
      checks_passed: passedChecks.length,
      passed_checks: passedChecks,
      failed_checks: failedChecks,
      units: files.reduce((total, file) => total + num((file.metrics || {}).units), 0),
      units_flagged: files.reduce((total, file) => total + num((file.metrics || {}).units_flagged), 0),
      flagged_units: flaggedUnits,
      file_details: files.map((file) => ({
        file: file.file,
        metrics: file.metrics || {},
        checks: visibleChecks(file.checks || []),
        flagged_units: file.flagged_units || [],
        execution_quality: file.execution_quality || null,
      })),
      judged_avg: Object.fromEntries(Object.entries(values).map(([key, items]) =>
        [key, Math.round(items.reduce((total, value) => total + value, 0) / items.length * 1000) / 1000])),
      metrics_used: [...new Set(files.flatMap((file) => file.metrics_used || []))],
      domain_metrics: uploadedDomainMetrics(report.agent, files),
    };
  }

  function uploadedRunFromReports(entries, label) {
    const reports = entries.map((entry) => entry.report);
    const agentReports = reports.filter((report) =>
      report && typeof report.agent === "string" && isVisibleEvaluationAgent(report.agent) && Array.isArray(report.files));
    if (!agentReports.length) throw new Error("Select at least one evaluator agent *_report.json file.");
    const names = agentReports.map((report) => report.agent);
    if (new Set(names).size !== names.length) {
      throw new Error("Each uploaded run must contain at most one report per agent.");
    }
    const input = entries.find((entry) => entry.name.toLowerCase() === "initial_input.json")?.report || {};
    const manifest = entries.find((entry) => entry.name.toLowerCase() === "manifest.json")?.report || {};
    const observability = entries.find((entry) => entry.name.toLowerCase() === "observability.json")?.report || {};
    const pipelineReport = entries.find((entry) => entry.name.toLowerCase() === "pipeline_report.json")?.report || null;
    const comparison = reports.find((report) => report && report.ground_truth && report.overview);
    const useCase = input.business_context?.use_case || input.use_case || "";
    const runId = manifest.analysis_id || comparison?.run_bundle || "";
    const reportTimes = agentReports.map((report) => report.generated_at_utc).filter(Boolean).sort();
    const fileCount = agentReports.reduce((total, report) => total + report.files.length, 0);
    const runLabel = useCase ? humanize(useCase) : label;
    const id = `upload-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
    return {
      id,
      run: {
        run_id: runId || id,
        label: runLabel,
        use_case: useCase,
        goal: typeof input.goal === "string" ? input.goal : "",
        status: manifest.status || "completed",
        generated_at_utc: reportTimes[reportTimes.length - 1] || manifest.started_at_utc || new Date().toISOString(),
        uploaded_at_utc: new Date().toISOString(),
        file_count: fileCount,
        judge: observability.judge || null,
        pipeline_report: pipelineReport,
        agents: agentReports.map(uploadedAgentSummary),
      },
      comparisons: reports.filter((report) => report && report.ground_truth && report.overview),
    };
  }

  // ---------- Per-agent report (grouped, instead of a raw key/value dump) ----------
  const fmtNum = (v) => (Number.isInteger(v) ? String(v) : String(Math.round(v * 1000) / 1000));
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

  // "convergence=1, dependency=1" / {a: 1} / [..]  ->  [[label, value], ...]
  function distPairs(v) {
    if (Array.isArray(v)) return v.map((x) => [typeof x === "object" && x !== null ? JSON.stringify(x) : String(x), null]);
    if (v && typeof v === "object") {
      return Object.entries(v).map(([k, x]) => [k, typeof x === "object" && x !== null ? JSON.stringify(x) : x]);
    }
    if (typeof v === "string" && /^[^=,]+=[^=,]+(,\s*[^=,]+=[^=,]+)*$/.test(v)) {
      return v.split(/,\s*/).map((p) => p.split("="));
    }
    return null;
  }

  const chips = (pairs) => `<div class="chips">${pairs.map(([k, v]) =>
    `<span class="chip2${v == null ? " solo" : ""}"><b>${esc(humanize(k))}</b>${v == null ? "" : `<i>${esc(isNum(v) ? fmtNum(v) : v)}</i>`}</span>`).join("")}</div>`;

  function groupMetrics(m, thresholds) {
    const g = { status: null, judge: [], judgeMisc: [], rates: [], scores: [], avgs: [], dists: [] };
    const used = new Set();
    const fam = {};
    Object.keys(m).forEach((k) => {
      const x = k.match(/^(.+)_(tested|avg|pass_pct)$/);
      if (x) (fam[x[1]] ||= {})[x[2]] = k;
    });
    Object.entries(fam).forEach(([base, f]) => {
      if (Object.keys(f).length < 2) return;
      Object.values(f).forEach((k) => used.add(k));
      g.judge.push({ base, avg: m[f.avg], pass: m[f["pass_pct"]], tested: m[f.tested], threshold: thresholds[base] });
    });
    Object.entries(m).forEach(([k, v]) => {
      if (used.has(k)) return;
      if (k === "status") g.status = v;
      else if (/^judge_/.test(k)) g.judgeMisc.push([k, v]);
      else if (distPairs(v)) g.dists.push([k, distPairs(v)]);
      else if (isNum(v) && /_pct$/.test(k)) g.rates.push([k.replace(/_pct$/, ""), v]);
      else if (isNum(v) && /_score$/.test(k)) g.scores.push([k.replace(/_score$/, ""), v]);
      else if (isNum(v) && (/^avg_/.test(k) || /_avg$/.test(k))) g.avgs.push([k.replace(/^avg_|_avg$/g, ""), v]);
    });
    return g;
  }

  function judgeTable(rows) {
    const body = rows.map((r) => {
      const tone = isNum(r.threshold) && isNum(r.avg) ? (r.avg < r.threshold ? "err" : "ok") : "";
      const pTone = !isNum(r.pass) ? "" : r.pass >= 100 ? "ok" : r.pass >= 50 ? "warn" : "err";
      return `<tr><td>${esc(humanize(r.base))}${isNum(r.threshold) ? `<small>threshold ${esc(fmtNum(r.threshold))}</small>` : ""}</td>` +
        `<td><div class="mcell2"><b>${isNum(r.avg) ? esc(fmtNum(r.avg)) : "–"}</b>${meter(r.avg, tone)}</div></td>` +
        `<td><div class="mcell2"><b>${isNum(r.pass) ? esc(fmtNum(r.pass)) + "%" : "–"}</b>${isNum(r.pass) ? meter(r.pass / 100, pTone) : ""}</div></td>` +
        `<td class="num">${isNum(r.tested) ? esc(r.tested) : "–"}</td></tr>`;
    }).join("");
    return `<div class="tbl"><table class="jt"><thead><tr><th scope="col">Metric</th><th scope="col">Average</th>` +
      `<th scope="col">Pass rate</th><th scope="col" class="num">Tested</th></tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function metricsPanel(m, agent) {
    const g = groupMetrics(m, agent.thresholds || {});
    const sec = (title, body) => (body ? `<section class="msec"><h4>${title}</h4>${body}</section>` : "");
    const tile = (label, text, mv, tone) =>
      `<div class="kv"><span>${esc(humanize(label))}</span><b>${esc(text)}</b>${isNum(mv) ? meter(mv, tone) : ""}</div>`;
    const judgeBody = (g.judge.length ? judgeTable(g.judge) : "") + (g.judgeMisc.length ? chips(g.judgeMisc) : "");
    const orchestrationBody = String(agent.agent || "").toLowerCase() === "orchestration" &&
      Object.prototype.hasOwnProperty.call(m, "planned_tasks")
      ? `<section class="msec"><h4>Orchestration metrics</h4><div class="kvs">` +
        tile("Tasks planned", fmtNum(m.planned_tasks)) +
        tile("Tasks with results", `${fmtNum(m.result_tasks)} / ${fmtNum(m.planned_tasks)}`) +
        tile("Successful tasks", `${fmtNum(m.successful_tasks)} / ${fmtNum(m.planned_tasks)}`) +
        tile("Task completion", isNum(m.task_completion_pct) ? `${fmtNum(m.task_completion_pct)}%` : "Not assessable",
          isNum(m.task_completion_pct) ? m.task_completion_pct / 100 : null) +
        tile("Workflow success", isNum(m.workflow_success_pct) ? `${fmtNum(m.workflow_success_pct)}%` : "Not assessable",
          isNum(m.workflow_success_pct) ? m.workflow_success_pct / 100 : null) +
        `</div></section>`
      : "";
    const ragMetrics = [
      ["context_relevancy_score", "Context relevancy"],
      ["context_precision_score", "Context precision"],
      ["context_recall_score", "Context recall"],
    ].filter(([key]) => Object.prototype.hasOwnProperty.call(m, key) ||
      Object.prototype.hasOwnProperty.call(m, key.replace("_score", "_error")));
    const ragBody = String(agent.agent || "").toLowerCase() === "rag" && ragMetrics.length
      ? `<section class="msec"><h4>RAG quality</h4><div class="kvs">` +
        ragMetrics.map(([key, label]) => tile(label,
          isNum(m[key]) ? pct(m[key]) : "Not assessable", m[key])).join("") +
        `</div></section>`
      : "";
    const html = [
      orchestrationBody,
      ragBody,
      sec("LLM judge scores", judgeBody),
      sec("Breakdowns", g.dists.map(([k, p]) => `<div class="dist"><span>${esc(humanize(k))}</span>${chips(p)}</div>`).join("")),
    ].join("");
    return { html: html || `<p class="hint">No metrics were recorded for this file.</p>`, status: g.status };
  }

  function toolExecutionPanel(execution, agentName) {
    const metrics = execution?.metrics || execution || {};
    const calls = Array.isArray(execution?.call_details)
      ? execution.call_details
      : Array.isArray(metrics.call_details) ? metrics.call_details : [];
    const failures = calls.filter((call) =>
      call.error_code || !/^(ok|success|succeeded|completed)$/i.test(String(call.status || ""))
    );
    const errorCount = Math.max(num(metrics.errors), failures.length);
    const fallbacks = calls.filter((call) => call.fallback_used === true).length;
    const summary = `<div class="tool-run-summary"><span>Calls <b>${num(metrics.calls) || calls.length}</b></span>` +
      `<span>Failures <b>${errorCount}</b></span><span>Repeated <b>${num(metrics.repeated_calls)}</b></span>` +
      `<span>Fallbacks <b>${fallbacks}</b></span></div>`;
    const resultSummary = (call) => {
      const result = call.result_summary || {};
      const count = call.result_count ?? result.count;
      if (count != null) return `${count} item${count === 1 ? "" : "s"}${result.type ? ` · ${result.type}` : ""}`;
      if (result.type === "object" && isNum(result.field_count)) return `object · ${result.field_count} fields`;
      if (result.type === "string" && isNum(result.length)) return `${result.length} chars · string`;
      return result.type || "Not recorded";
    };
    const formatArgumentValue = (value) => {
      if (typeof value === "string") return value;
      const serialized = JSON.stringify(value);
      return serialized === undefined ? String(value) : serialized;
    };
    const formatToolResult = (call) => {
      const result = call.result ?? call.result_payload;
      if (result == null) return "";
      if (result && typeof result === "object" && result.truncated === true) {
        const length = isNum(result.serialized_length)
          ? ` (${fmtNum(result.serialized_length)} characters)` : "";
        return `<div class="tool-call-result"><span>The result exceeded the telemetry capture limit${length}; the full payload was not retained.</span></div>`;
      }
      let text = typeof result === "string" ? result : JSON.stringify(result, null, 2);
      const maxLength = 8000;
      if (text.length > maxLength) text = `${text.slice(0, maxLength)}\n[Result display truncated]`;
      return `<div class="tool-call-result"><span>Result text</span><pre>${esc(text)}</pre></div>`;
    };
    const callList = calls.length
      ? `<div class="tool-call-list">${calls.map((call) => {
          const argumentTypes = call.argument_types || {};
          const argumentValues = call.argument_values && typeof call.argument_values === "object"
            ? call.argument_values : {};
          const argumentNames = [...new Set([
            ...(call.argument_names || Object.keys(argumentTypes)),
            ...Object.keys(argumentValues),
          ])].filter((name) => {
            const type = String(argumentTypes[name] || "").trim().toLowerCase();
            const hasValue = Object.prototype.hasOwnProperty.call(argumentValues, name);
            const value = hasValue ? argumentValues[name] : undefined;
            const isNullValue = hasValue && value == null;
            const isNullType = type === "nonetype";
            const isCompetitorDomainsNull = String(call.worker || "").toLowerCase() ===
              "competitor_intelligence" && name.toLowerCase() === "domains" &&
              (isNullType || isNullValue ||
                (typeof value === "string" && value.trim().toLowerCase() === "null"));
            return !isNullType && !isNullValue && !isCompetitorDomainsNull;
          });
          const argumentDetails = argumentNames.length
            ? argumentNames.map((name) => `<div class="tool-argument-value-row">` +
              `<b>${esc(name)}${argumentTypes[name] ? `<small>${esc(argumentTypes[name])}</small>` : ""}</b>` +
              `<code>${esc(Object.prototype.hasOwnProperty.call(argumentValues, name)
                ? formatArgumentValue(argumentValues[name]) : "Value not recorded")}</code></div>`
            ).join("")
            : `<span class="hint">No argument details recorded.</span>`;
          const query = Object.prototype.hasOwnProperty.call(argumentValues, "query")
            ? `<div class="tool-query"><span>Query</span><code>${esc(formatArgumentValue(argumentValues.query))}</code></div>`
            : "";
          const resultMissing = call.result == null && call.result_payload == null &&
            call.result_summary == null;
          const reportedSuccess = /^(ok|success|succeeded|completed)$/i.test(String(call.status || "")) &&
            !call.error_code;
          const status = `${call.status || "Not recorded"}${call.error_code ? ` · ${call.error_code}` : ""}` +
            `${resultMissing && reportedSuccess ? " · result not recorded" : ""}`;
          const provider = call.provider || (call.error_code === "provider_exhausted"
            ? "No provider succeeded" : "Provider not reported");
          const badgeClass = !reportedSuccess ? "err" : resultMissing ? "warn" : "ok";
          return `<details class="tool-call"><summary>` +
            `<span class="tool-call-name">${esc(call.tool || "Unknown tool")}</span>` +
            `<span class="tool-call-meta">${esc(provider)}</span>` +
            `<span class="b ${badgeClass}">${esc(status)}</span>` +
            `<span class="tool-call-duration">${esc(isNum(call.latency_ms) ? `${call.latency_ms} ms` : "–")}</span>` +
            `</summary><div class="tool-call-details">` +
            `<div><span>Worker</span><b>${esc(call.worker || "Not recorded")}</b></div>` +
            `<div><span>Task</span><b>${esc(call.task_id || "Not recorded")}</b></div>` +
            `<div><span>Result</span><b>${esc(resultSummary(call))}</b></div>` +
            `${formatToolResult(call)}` +
            `${query}<div class="tool-call-arguments"><span>Arguments</span><div class="tool-argument-values">${argumentDetails}</div></div>` +
            `${call.fallback_used ? `<p class="tool-call-fallback">Fallback was used for this call.</p>` : ""}` +
            `</div></details>`;
        }).join("")}</div>`
      : `<p class="hint">${execution ? "No tool calls were recorded for this file." : "No per-file tool execution trace was recorded."}</p>`;
    return `<section class="msec tool-execution"><h4>Tool execution</h4>${summary}${callList}</section>`;
  }

  function sidePanel(checks, flags) {
    const li = (c, tone) =>
      `<li class="${tone}"><div><b>${esc(c.check || "Unnamed check")}</b>` +
      `${c.detail ? `<small>Why: ${esc(c.detail)}</small>` : ""}` +
      `${tone !== "p" && c.severity ? `<small>Severity: ${esc(c.severity)}</small>` : ""}</div></li>`;
    const failed = checks.filter((c) => c.passed !== true && c.severity === "error");
    const visible = visibleChecks(checks);
    const passed = checks.filter((c) => c.passed === true);
    const checkBadges =
      (failed.length ? `<span class="b err">${failed.length} failed</span>` : "") +
      (passed.length ? `<span class="b ok">${passed.length} passed</span>` : "");
    const checkHtml = visible.length
      ? (failed.length ? `<ul class="checks">${failed.map((c) => li(c, "f")).join("")}</ul>` : "") +
        (passed.length ? `<details class="passed-checks"${failed.length ? "" : " open"}><summary>Passed checks (${passed.length})</summary>` +
          `<ul class="checks">${passed.map((c) => li(c, "p")).join("")}</ul></details>` : "")
      : `<p class="hint">No integrity checks were recorded for this file.</p>`;
    const flagHtml = flags.length
      ? `<ul class="checks">${flags.map((u) => {
          const reasons = (u.flags || []).map((r) => [r, null]);
          return `<li class="w"><div><b>${esc(humanize(u.unit_type || "Unit"))}${u.unit_id ? ` · ${esc(u.unit_id)}` : ""}</b>` +
            `${u.statement ? `<small>${esc(u.statement)}</small>` : ""}` +
            `${chips(reasons.length ? reasons : [["Flagged by evaluator", null]])}</div></li>`;
        }).join("")}</ul>`
      : `<p class="hint">No units were flagged in this file.</p>`;
    return `<section class="msec"><h4>Integrity checks ${checkBadges}</h4>${checkHtml}</section>` +
      `<section class="msec"><h4>Flagged units ${flags.length ? `<span class="b warn">${flags.length}</span>` : `<span class="b ok">0</span>`}</h4>${flagHtml}</section>`;
  }

  function agentDetailsCard(agent) {
    const files = agent.file_details || [];
    const fileHtml = files.map((file) => {
      const checks = file.checks || [];
      const flags = file.flagged_units || [];
      const failedN = checks.filter((c) => c.passed !== true && c.severity === "error").length;
      const mp = metricsPanel(file.metrics || {}, agent);
      const statusTone = mp.status == null ? "" : /^(completed|success|succeeded)$/i.test(String(mp.status)) ? "ok" : "warn";
      const open = files.length === 1 || failedN > 0 ? " open" : "";
      return `<details class="report-file"${open}><summary><span class="fname">${esc(file.file || "Evaluation file")}</span>` +
        `${mp.status != null ? `<span class="ar-badges"><span class="b ${statusTone}">${esc(humanize(mp.status))}</span></span>` : ""}</summary>` +
        `<section class="rep-body"><div class="rep-cols"><div class="rep-metrics">${mp.html}</div>` +
        `<div class="rep-side">${sidePanel(checks, flags)}</div></div>` +
        toolExecutionPanel(file.execution_quality, agent.agent) + `</section></details>`;
    }).join("");
    const cp = agent.checks_passed ?? 0, ct = agent.checks ?? 0, uf = agent.units_flagged ?? 0;
    const agentOpen = (agent.failed_checks || []).length > 0 ? " open" : "";
    return `<details class="card agent-report" data-agent="${esc(agent.agent)}"${agentOpen}><summary>` +
      `<span class="ar-title"><b>${esc(humanize(agent.agent))}</b>` +
      `<small>${plural(files.length, "file")}${agent.model ? ` · evaluator ${esc(agent.model)}` : ""}${agent.dry_run ? " · dry run" : ""}</small></span>` +
      `<span class="ar-badges"><span class="b ${cp === ct ? "ok" : "err"}">${esc(cp)}/${esc(ct)} checks passed</span>` +
      `<span class="b ${uf ? "warn" : "ok"}">${plural(num(uf), "flagged unit")}</span></span></summary>` +
      `<section class="ar-body">${fileHtml || `<p class="hint">This agent report contains no file-level details.</p>`}</section></details>`;
  }

  function executionCard(a) {
    const execution = a.execution_quality;
    const dimensionLabels = {
      tool_selection: "Tool selection",
      tool_arguments: "Tool arguments",
      tool_result_use: "Result use",
      workflow_sequence: "Workflow sequence",
      error_handling: "Error handling",
      efficiency: "Efficiency",
    };
    const tiles = Object.entries(dimensionLabels).map(([key, label]) => {
      const item = (execution.dimensions || {})[key] || {};
      const parts = [];
      if (num(item.assessable_files)) parts.push(`${item.assessable_files} assessable`);
      if (num(item.partial_files)) parts.push(`${item.partial_files} partial`);
      if (num(item.pending_judge_files)) parts.push(`${item.pending_judge_files} pending judge`);
      const status = parts.join(", ") || "Not assessable";
      const tone = num(item.partial_files) || num(item.pending_judge_files) ? "partial" : num(item.assessable_files) ? "ok" : "";
      const badge = tone === "ok" ? "ok" : tone === "partial" ? "warn" : "na";
      const reasons = (item.reasons || []).join("; ");
      return `<div class="dim ${tone}"><b>${label}</b><span class="b ${badge}">${esc(status)}${isNum(item.score) ? ` · ${pct(item.score)}` : ""}</span>` +
        `${reasons ? `<small>${esc(reasons)}</small>` : ""}</div>`;
    });
    return `<div class="card" data-agent="${esc(a.agent)}"><h3>${esc(humanize(a.agent))} execution quality</h3>` +
      `<div class="dims">${tiles.join("")}</div></div>`;
  }

  function ragCard(d) {
    const labels = [
      ["context_precision", "Context precision"],
      ["context_recall", "Context recall"],
      ["context_relevancy", "Context relevancy"],
    ];
    const tiles = labels.map(([key, label]) => {
      const item = d[key] || {};
      const reason = item.reason || "";
      const skipped = /\bskipped\b|rate.limit/i.test(reason);
      const notRequested = /not requested/i.test(reason);
      const value = isNum(item.score)
        ? pct(item.score)
        : skipped ? "Skipped"
          : notRequested ? "Not requested"
            : "Not assessable";
      const explanation = reason
        ? `<details class="kv-reason"><summary>Why this score?</summary><small>${esc(reason)}</small></details>`
        : "";
      return `<div class="kv"><span>${esc(label)}</span><b>${esc(value)}</b>${explanation}</div>`;
    });
    tiles.push(kv("Retrieval latency", isNum(d.retrieval_latency_ms) ? `${d.retrieval_latency_ms} ms` : "Not available",
      isNum(d.retrieval_latency_ms) ? "" : "No retrieval latency was recorded."));
    tiles.push(kv("Retrieval volume", `${d.queries ?? "–"} queries`, `${d.retrieved_chunks ?? "–"} chunks`));
    if (d.answer_source) tiles.push(kv("Answer source", humanize(d.answer_source)));
    const expectedSource = d.expected_output_source || "";
    const referenceNote = expectedSource
      ? `<details class="kv-reason"><summary>Source</summary><small>${esc(expectedSource)}</small></details>`
      : `<small>Context precision and recall require an expected answer.</small>`;
    tiles.push(`<div class="kv"><span>Reference answer</span><b>${expectedSource ? "Golden dataset" : "Not provided"}</b>${referenceNote}</div>`);
    return `<div class="card" data-agent="rag"><h3>RAG metrics</h3><div class="kvs">${tiles.join("")}</div></div>`;
  }

  function orchestrationCard(d) {
    const labels = [
      ["task_completion_pct", "Task completion rate", true],
      ["workflow_success_pct", "Workflow success rate", true],
      ["failure_rate_pct", "Failure rate", true],
      ["retry_rate_pct", "Retry rate", true],
      ["execution_duration_seconds", "Execution duration", false],
    ];
    const tiles = labels.map(([key, label, percentage]) => {
      const value = d[key];
      let note = "";
      if (!isNum(value)) {
           if (["task_completion_pct", "workflow_success_pct", "failure_rate_pct", "retry_rate_pct"].includes(key)
          && d.planned_tasks === 0) {
          note = "No planned tasks were recorded for this run.";
        } else {
          note = key === "execution_duration_seconds"
            ? "Execution duration was not recorded."
            : "The evaluator did not record this metric.";
        }
      }
      return kv(label, isNum(value) ? (percentage ? pct(value / 100) : `${value} s`) : "Not assessable", note);
    });
    return `<div class="card" data-agent="orchestration"><h3>Orchestration metrics</h3><div class="kvs">${tiles.join("")}</div></div>`;
  }

  async function showRun(runId) {
    const token = ++runToken;
    const status = $("results-status");
    clearRun();
    renderSidebarSavedRuns();
    setStatus(status, "loading", "Loading run…");
    try {
      const run = uploadedRuns.has(runId)
        ? uploadedRuns.get(runId)
        : await get("/runs/" + encodeURIComponent(runId));
      if (token !== runToken) return;

      const agents = (run.agents || []).filter((agent) => isVisibleEvaluationAgent(agent.agent));
      updateStageBar(agents);
      const fileDetails = agents.flatMap((agent) => agent.file_details || []);
      const checks = visibleChecks(fileDetails.flatMap((file) => file.checks || []));
      const passed = checks.filter((check) => check.passed === true).length;
      const failed = checks.filter((check) => check.passed !== true && check.severity === "error");
      const totalUnits = agents.reduce((sum, agent) => sum + num(agent.units), 0);
      const flaggedUnits = agents.reduce((sum, agent) => sum +
        (Array.isArray(agent.flagged_units) ? agent.flagged_units.length : num(agent.units_flagged)), 0);
      const reportDates = agents.map((agent) => agent.generated_at_utc).filter(Boolean).sort();
      const generatedAt = run.generated_at_utc || reportDates[reportDates.length - 1] || run.uploaded_at_utc;
      const reportModels = [...new Set(agents.map((agent) => agent.model).filter(Boolean))];
      const judge = run.judge || agents.find((agent) => agent.judge)?.judge || null;
      const judgeModel = judge?.model || (reportModels.length === 1 ? reportModels[0] : reportModels.length ? "Multiple models" : "Not recorded");
      const reportFileCount = run.file_count ?? fileDetails.length;
      const displayRunId = run.run_id || runId;
      const generatedDate = generatedAt ? new Date(generatedAt) : null;
      const formattedDate = generatedDate && !Number.isNaN(generatedDate.getTime())
        ? generatedDate.toLocaleString()
        : "Not recorded";
      $("snapshot-generated").textContent = formattedDate;
      $("snapshot-model").textContent = judgeModel;
      $("snapshot-run-id").textContent = displayRunId;
      $("snapshot-scope").textContent = `${agents.length} agents, ${reportFileCount} files`;
      $("sidebar-run-id").textContent = displayRunId;
      $("top-snapshot").textContent = generatedDate && !Number.isNaN(generatedDate.getTime())
        ? `Snapshot ${generatedDate.toLocaleDateString()}`
        : "Snapshot date unavailable";
      $("top-judge").textContent = judgeModel === "Not recorded" ? "Judge model unavailable" : `${judgeModel} judge`;
      $("overview-lede").textContent = run.use_case
        ? `${humanize(run.use_case)} · Run-level quality and integrity summary.`
        : "Run-level quality and integrity summary for the selected evaluation.";
      const runGoal = typeof run.goal === "string" ? run.goal.trim() : "";
      $("run-goal").hidden = !runGoal;
      $("run-goal").innerHTML = runGoal ? `<b>Run goal</b><p>${esc(runGoal)}</p>` : "";

      if (!agents.length) {
        $("results-kpis").innerHTML = kpi("Agents evaluated", "0", "No agent reports in this run");
        setStatus(status, "info", "This run has no agent results yet.");
        setStatus($("overview-attention"), "info", "No agent evaluation records were included in this run.");
        $("pipeline-agent-results").innerHTML = `<p class="hint">No agent metrics are available for this run.</p>`;
        return;
      }

      const checkCount = checks.length;

      $("results-kpis").innerHTML =
        kpi("Integrity checks passed", `${passed} / ${checkCount}`, `${failed.length} failed`, failed.length ? "mid" : "good") +
        kpi("Agents evaluated", agents.length, `${reportFileCount} evaluation files`) +
        kpi("Flagged units", `${flaggedUnits} / ${totalUnits}`, "across all agents", flaggedUnits ? "mid" : "good");

      const orderedAgents = agents.slice().sort((x, y) => agentRank(x) - agentRank(y));
      const execRows = [];
      orderedAgents.forEach((a) => {
        if (a.execution_quality) execRows.push(executionCard(a));
      });
      $("pipeline-agent-results").innerHTML =
        (execRows.length ? `<div class="grid g2">${execRows.join("")}</div>` : "") +
        orderedAgents.map(agentDetailsCard).join("");
      setStatus($("overview-attention"), failed.length ? "error" : "",
        failed.length ? `${failed.length} failed integrity checks need review in Pipeline.` : "");

      const incomplete = run.status && !["completed", "success", "succeeded"].includes(String(run.status).toLowerCase());
      setStatus(
        status,
        incomplete ? "info" : failed.length ? "" : "ok",
        incomplete
          ? `Run status: ${humanize(run.status)}. Showing the evaluation records available for this run.`
          : failed.length ? "" : "Run data loaded."
      );
    } catch (e) {
      if (token !== runToken) return;
      clearRun();
      setStatus(status, "error", e.message);
    }
  }

  // ---------- Ground-truth comparison ----------
  let compareToken = 0;

  async function showCompare(id) {
    const token = ++compareToken;
    const status = $("compare-status");
    clearCompare();
    setStatus(status, "loading", "Loading comparison…");
    try {
      const c = await get("/comparisons/" + encodeURIComponent(id));
      if (token !== compareToken) return;

      const o = c.overview || {};
      const zero = o.stages_with_zero_recall || [];
      $("compare-kpis").innerHTML =
        kpi("Mean recall", pct(o.mean_recall), "ground-truth items found", isNum(o.mean_recall) ? (o.mean_recall >= 0.7 ? "good" : "mid") : "") +
        kpi("Mean alignment", pct(o.mean_alignment), "same category and meaning") +
        kpi("Mean F1", pct(o.mean_f1), "recall and alignment");

      const meta = [["Ground truth", c.ground_truth], ["Run", c.run_bundle], ["Method", c.method]].filter(([, v]) => v != null && v !== "");
      $("compare-meta").innerHTML = meta.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
      $("compare-meta").hidden = !meta.length;

      const rows = [];
      Object.entries(c.workers || {}).forEach(([k, v]) => rows.push(["workers/" + k, v]));
      ["verification", "cross_signal"].forEach((k) => c[k] && rows.push([k, c[k]]));
      Object.entries(c.decision || {}).forEach(([k, v]) => rows.push(["decision/" + k, v]));

      if (!rows.length) {
        setStatus(status, "info", "This comparison has no per-stage results.");
        return;
      }

      const head =
        `<thead><tr><th scope="col">Stage</th><th scope="col" class="num">Matched</th><th scope="col" class="num">Recall</th>` +
        `<th scope="col" class="num">Alignment</th><th scope="col" class="num">F1</th>` +
        `<th scope="col">Not covered</th></tr></thead>`;

      const body = rows.map(([name, v]) => {
        const notCovered = v.not_covered || [];
        const missed = notCovered.length;
        const recallCls = !isNum(v.recall) ? "na" : v.recall >= 0.7 ? "ok" : v.recall > 0 ? "warn" : "err";
        const cell = (val) => `<td class="num"><span class="mcell"><span>${pct(val)}</span>${meter(val)}</span></td>`;
        return `<tr><td>${esc(stageName(name))}</td>` +
          `<td class="num">${esc(v.matched ?? "–")} / ${esc(v.truth_items ?? "–")}</td>` +
          `<td class="num"><span class="mcell"><span class="b ${recallCls}">${pct(v.recall)}</span>${meter(v.recall, recallCls)}</span></td>` +
          cell(v.alignment) + cell(v.f1) +
          `<td>${missed
            ? `<details><summary>${missed} item${missed === 1 ? "" : "s"}</summary>${notCovered.map((t) =>
                `<div>${esc(typeof t === "string" ? t : JSON.stringify(t))}</div>`).join("")}</details>`
            : "–"}</td></tr>`;
      }).join("");

      $("compare-table").innerHTML = head + `<tbody>${body}</tbody>`;
      tableBox("compare-table").hidden = false;
      setStatus(status, "");
    } catch (e) {
      if (token !== compareToken) return;
      clearCompare();
      setStatus(status, "error", e.message);
    }
  }

  // ---------- Router: top-bar tabs, pipeline stage switcher, deep links ----------
  function initRouter() {
    const panels = [...document.querySelectorAll(".panel")];
    const stages = [...document.querySelectorAll(".stage")];
    const tabs = [...document.querySelectorAll(".tabs a")];
    const stageButtons = [...document.querySelectorAll("#pipeline .node[data-stage]")];
    const panelIds = new Set(panels.map((p) => p.id));
    const stageIds = new Set(stages.map((s) => s.id));
    const healthIds = new Set([...document.querySelectorAll("#health .hsec")].map((s) => s.id));
    let stage = stages[0] ? stages[0].id : null;

    function go(id, opts) {
      const { scroll = true, push = true } = opts || {};
      let panel = "overview", target = null;
      if (panelIds.has(id)) panel = id;
      else if (stageIds.has(id)) { panel = "pipeline"; stage = id; }
      else if (healthIds.has(id)) { panel = "health"; target = $(id); }
      else if (id === "live-results") { panel = "overview"; target = $(id); }

      const before = (panels.find((p) => !p.hidden) || {}).id;
      panels.forEach((p) => { p.hidden = p.id !== panel; });
      document.body.dataset.panel = panel;
      stages.forEach((s) => { s.hidden = s.id !== stage; });
      stageButtons.forEach((b) => {
        const on = b.dataset.stage === stage;
        b.setAttribute("aria-selected", String(on));
        b.tabIndex = on ? 0 : -1;
      });
      tabs.forEach((a) => {
        if (a.getAttribute("href") === "#" + panel) a.setAttribute("aria-current", "true");
        else a.removeAttribute("aria-current");
      });
      if (push && history.replaceState) history.replaceState(null, "", "#" + (target || stageIds.has(id) ? id : panel));
      if (!scroll) return;
      if (target) target.scrollIntoView({ block: "start" });
      else if (before !== panel) window.scrollTo(0, 0);
    }

    document.addEventListener("click", (e) => {
      const btn = e.target.closest("#pipeline .node[data-stage]");
      if (btn) { go(btn.dataset.stage); return; }
      const a = e.target.closest('a[href^="#"]');
      if (!a || a.classList.contains("skip")) return;
      const id = a.getAttribute("href").slice(1);
      if (!id || !$(id)) return;
      e.preventDefault();
      go(id);
    });

    // Arrow keys move along the stage switcher
    const rail = document.querySelector("#pipeline .path");
    if (rail) {
      rail.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        const i = stageButtons.findIndex((b) => b.dataset.stage === stage);
        const next = stageButtons[(i + (e.key === "ArrowRight" ? 1 : -1) + stageButtons.length) % stageButtons.length];
        go(next.dataset.stage, { scroll: false });
        next.focus();
        e.preventDefault();
      });
    }

    window.addEventListener("hashchange", () => go(location.hash.slice(1) || "overview", { push: false }));
    go(location.hash.slice(1) || "overview", { scroll: false, push: false });
  }

  // ---------- Startup ----------
  async function initRuns() {
    const status = $("results-status"), select = $("run-select");
    setStatus(status, "loading", "Loading runs…");
    let savedUploads = [];
    try {
      savedUploads = await readSavedUploads();
      savedUploads.forEach((record) => addUploadedRun(record));
    } catch (error) {
      setStatus($("run-upload-status"), "error", `Saved uploads could not be restored: ${error.message}`);
    }
    try {
      const { runs = [] } = await get("/runs");
      fill(select, runs.map((r) => ({ value: r.run_id, label: r.label, agents: r.agents || [], status: r.status })),
        (r) => `${r.label || `${String(r.value).slice(0, 8)}…`}${r.status ? ` · ${humanize(r.status)}` : ""} (${r.agents.length} agent${r.agents.length === 1 ? "" : "s"})`, "No runs found");
      savedUploads.forEach((record) => addUploadedRun(record));
      updateDeleteUploadButton();
      if (runs.length) {
        showRun(runs[0].run_id);
      } else if (savedUploads.length) {
        select.value = savedUploads[0].id;
        updateDeleteUploadButton();
        showRun(savedUploads[0].id);
        setStatus(status, "ok", `Restored ${savedUploads.length} uploaded run${savedUploads.length === 1 ? "" : "s"}.`);
      } else {
        setStatus(status, "info", "No evaluation reports found yet. Run the evaluation agent first.");
      }
    } catch (e) {
      fill(select, [], null, "No runs available");
      savedUploads.forEach((record) => addUploadedRun(record));
      if (savedUploads.length) {
        select.value = savedUploads[0].id;
        updateDeleteUploadButton();
        showRun(savedUploads[0].id);
        setStatus(status, "info", `Backend runs could not be loaded (${e.message}); restored ${savedUploads.length} uploaded run${savedUploads.length === 1 ? "" : "s"}.`);
      } else {
        setStatus(status, "info", `${e.message} You can still upload evaluator *_report.json files below.`);
      }
    }
  }

  async function initComparisons() {
    const status = $("compare-status"), select = $("compare-select");
    setStatus(status, "loading", "Loading comparisons…");
    try {
      const { comparisons = [] } = await get("/comparisons");
      availableComparisons = comparisons;
      fill(select, comparisons.map((c) => ({ value: c.id, gt: c.ground_truth, run: c.run_bundle })),
        (c) => `${c.gt} vs ${String(c.run).slice(0, 8)}…`, "No comparisons found");
      addUploadedComparisonOptions();
      const uploadedComparisonId = [...uploadedComparisons.keys()][0];
      if ($("run-select").value && selectComparisonForRun($("run-select").value)) {
        return;
      } else if (comparisons.length) {
        showCompare(comparisons[0].id);
      } else if (uploadedComparisonId) {
        select.value = uploadedComparisonId;
        showCompare(uploadedComparisonId);
      } else {
        setStatus(status, "info", "No comparison reports found yet. Run evaluation_Compare/compare.py.");
      }
    } catch (e) {
      availableComparisons = [];
      fill(select, [], null, "No comparisons available");
      addUploadedComparisonOptions();
      const selectedRunId = $("run-select").value;
      const uploadedComparisonId = comparisonForRun(selectedRunId);
      if (uploadedComparisonId) {
        select.value = uploadedComparisonId;
        showCompare(uploadedComparisonId);
      } else {
        clearCompare();
        setStatus(status, "error", e.message);
      }
    }
  }

  async function uploadRunReports(event) {
    const input = event.target;
    const status = $("run-upload-status");
    const files = [...(input.files || [])];
    input.value = "";
    if (!files.length) return;
    setStatus(status, "loading", "Reading selected JSON reports…");
    try {
      if (files.length > 100) throw new Error("Select no more than 100 report files at once.");
      const entries = await Promise.all(files.map(async (file) => {
        if (file.size > 25 * 1024 * 1024) throw new Error(`${file.name} is larger than the 25 MB per-file limit.`);
        try {
          return { name: file.name, report: JSON.parse(await file.text()) };
        } catch (error) {
          throw new Error(`${file.name} is not valid JSON: ${error.message}`);
        }
      }));
      const reports = entries.map((entry) => entry.report);
      const evaluationReports = reports.filter((report) =>
        report && typeof report.agent === "string" && isVisibleEvaluationAgent(report.agent) && Array.isArray(report.files));
      const runData = uploadedRunFromReports(entries, `Uploaded run · ${evaluationReports.length} agent reports`);
      const record = { ...runData, saved_at_utc: new Date().toISOString() };
      await saveUploadedRun(record);
      addUploadedRun(record, true);
      if (runData.comparisons.length) {
        const select = $("compare-select");
        select.value = `${runData.id}-comparison-${runData.comparisons.length}`;
        showCompare(select.value);
      }
      showRun(runData.id);
      const recognizedCount = evaluationReports.length + runData.comparisons.length +
        entries.filter((entry) => ["initial_input.json", "manifest.json", "observability.json", "pipeline_report.json"].includes(entry.name.toLowerCase())).length;
      const skipped = files.length - recognizedCount;
      setStatus(status, "ok",
        `Saved ${evaluationReports.length} agent reports in this browser${runData.comparisons.length ? ` and ${runData.comparisons.length} comparison report${runData.comparisons.length === 1 ? "" : "s"}` : ""}.` +
        `${skipped > 0 ? ` Ignored ${skipped} unrecognized file${skipped === 1 ? "" : "s"}.` : ""}`);
    } catch (error) {
      setStatus(status, "error", error.message);
    }
  }

  async function deleteSelectedUpload() {
    const id = $("run-select").value;
    const run = uploadedRuns.get(id);
    if (!run || !window.confirm(`Delete uploaded run "${run.label}" from this browser? This cannot be undone.`)) return;
    const status = $("results-status");
    try {
      await deleteSavedUpload(id);
      uploadedRuns.delete(id);
      renderSidebarSavedRuns();
      for (const comparisonId of uploadedComparisons.keys()) {
        if (comparisonId.startsWith(`${id}-comparison-`)) uploadedComparisons.delete(comparisonId);
      }
      const runSelect = $("run-select");
      [...runSelect.options].filter((option) => option.value === id).forEach((option) => option.remove());
      const compareSelect = $("compare-select");
      [...compareSelect.options].filter((option) => option.value.startsWith(`${id}-comparison-`))
        .forEach((option) => option.remove());
      const nextRun = [...runSelect.options].find((option) => option.value);
      if (nextRun) {
        runSelect.value = nextRun.value;
        runSelect.disabled = false;
        updateDeleteUploadButton();
        showRun(nextRun.value);
        selectComparisonForRun(nextRun.value);
      } else {
        runSelect.disabled = true;
        updateDeleteUploadButton();
        if (!runSelect.options.length) runSelect.add(new Option("No runs available", ""));
        clearRun();
        setStatus(status, "info", "Uploaded run deleted. No runs are available.");
      }
      const nextComparison = !nextRun && [...compareSelect.options].find((option) => option.value);
      if (nextComparison) {
        compareSelect.value = nextComparison.value;
        showCompare(nextComparison.value);
      } else if (!nextRun) {
        if (!compareSelect.options.length) compareSelect.add(new Option("No comparisons available", ""));
        clearCompare();
      }
      setStatus($("run-upload-status"), "ok", "Uploaded run deleted.");
    } catch (error) {
      setStatus(status, "error", `Could not delete the uploaded run: ${error.message}`);
    }
  }

  function init() {
    $("run-select").addEventListener("change", (e) => {
      updateDeleteUploadButton();
      renderSidebarSavedRuns();
      if (e.target.value) {
        showRun(e.target.value);
        selectComparisonForRun(e.target.value);
      }
    });
    $("sidebar-saved-runs").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-run-id]");
      if (!button || !uploadedRuns.has(button.dataset.runId)) return;
      $("run-select").value = button.dataset.runId;
      $("run-select").dispatchEvent(new Event("change"));
    });
    $("compare-select").addEventListener("change", (e) => e.target.value && showCompare(e.target.value));
    $("run-upload").addEventListener("change", uploadRunReports);
    $("delete-uploaded-run").addEventListener("click", deleteSelectedUpload);
    $("overview").querySelector(".hero .path")?.remove();
    const overviewStack = $("overview").querySelector(".body > .stack");
    [...overviewStack.children]
      .filter((element) => element.id !== "live-results")
      .forEach((element) => element.remove());
    document.querySelectorAll("#pipeline .path.on-paper, #pipeline .stage").forEach((element) => element.remove());
    initRouter();
    initStageBar();
    initRuns().then(initComparisons);
  }

  init();
})();