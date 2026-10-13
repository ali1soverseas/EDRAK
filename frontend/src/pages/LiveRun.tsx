import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { StageTracker } from "../components/RunStatus";
import { PageChrome } from "../components/shell/AppShell";
import { useL } from "../lib/useL";
import { edrak } from "../services/edrak";
import { Chip, StatusChip } from "../components/ui/Chip";
import { Icon } from "../components/ui/Icon";
import { Overlay } from "../components/ui/Overlay";
import { ProgressBar } from "../components/ui/Progress";
import { WorkerBadge } from "../components/ui/WorkerBadge";
import { useI18n, type TKey } from "../i18n";
import { useNow, usePolled } from "../lib/hooks";
import { useStickToBottom } from "../lib/useStickToBottom";
import { USE_CASE_META } from "../lib/usecases";
import { WORKER_META, workerKey } from "../lib/workers";
import { api } from "../services/api";
import { subscribeToRun } from "../services/sse";
import type { RunEvent, RunTaskView, RunView } from "../types/app";

const POLL_MS = 1500;

const RUNNING_TITLE: Record<number, TKey> = {
  1: "run.title.running.1",
  2: "run.title.running.2",
  3: "run.title.running.3",
  4: "run.title.running.4",
};

function titleKey(run: RunView): { key: TKey; params?: Record<string, number> } {
  const failed = run.tasks.filter((task) => task.state === "failed").length;
  switch (run.state) {
    case "completed":
      return { key: "run.title.completed" };
    case "partial":
      return { key: "run.title.partial" };
    case "failed":
      return { key: "run.title.failed" };
    case "cancelled":
      return { key: "run.title.cancelled" };
    case "awaiting_review":
      return { key: "run.title.review" };
    case "paused":
      return { key: "run.title.paused" };
    case "running":
      if (run.stage === "verify") return { key: "run.title.verify" };
      if (run.stage === "synthesize") return { key: "run.title.synthesize" };
      if (failed > 0) return failed === 1 ? { key: "run.title.failedOne" } : { key: "run.title.failedMany", params: { n: failed } };
      return { key: RUNNING_TITLE[run.tasks.length] ?? "run.title.running.n", params: { n: run.tasks.length } };
  }
}

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${pad(minutes)}:${pad(seconds)}`;
}

function TaskChip({ state }: { state: RunTaskView["state"] }) {
  const { t } = useI18n();
  switch (state) {
    case "done":
      return (
        <Chip tone="ok" icon="check">
          {t("run.task.done")}
        </Chip>
      );
    case "running":
      return <Chip tone="blue">{t("run.task.working")}</Chip>;
    case "failed":
      return (
        <Chip tone="bad" icon="x">
          {t("run.task.failed")}
        </Chip>
      );
    case "queued":
      return <Chip tone="draft">{t("run.task.queued")}</Chip>;
  }
}

function WorkerCard({ task, open, onToggle }: { task: RunTaskView; open: boolean; onToggle: () => void }) {
  const { t, tn, formatTimeSeconds, formatNumber } = useI18n();
  const logRef = useStickToBottom<HTMLDivElement>(task.log.length);
  const meta = WORKER_META[task.worker];
  const color = task.state === "failed" ? "var(--bad)" : meta.color;
  const panel = `task-log-${task.task_id}`;
  return (
    <article className="card" style={{ padding: "14px 18px" }}>
      <div className="row g14 task-row">
        <WorkerBadge worker={task.worker} large />
        <div className="grow col g6 task-main" style={{ minWidth: 0 }}>
          <div className="row g10">
            <span className="b6 s15">{t(`worker.name.${workerKey(task.worker)}` as TKey)}</span>
            <TaskChip state={task.state} />
          </div>
          <div className="s13 t2 ellip" dir="auto">
            {task.activity}
            {task.state === "running" && <span className="caret" aria-hidden="true" />}
          </div>
        </div>
        <div className="col g6 task-progress">
          <div className="row jb s12 t3">
            <span>{task.state === "running" && task.sources === 0 ? t("run.task.collecting") : tn("plural.sources", task.sources)}</span>
            <span className="num">{formatNumber(task.progress)}%</span>
          </div>
          <ProgressBar value={task.progress} color={color} growIn={task.state === "running"} />
        </div>
        <button
          type="button"
          className="btn sm ghost icon"
          aria-expanded={open}
          aria-controls={panel}
          aria-label={t("run.task.details", { name: t(`worker.name.${workerKey(task.worker)}` as TKey) })}
          onClick={onToggle}
        >
          <Icon name={open ? "chevronDown" : "chevron"} size={16} flip={!open} />
        </button>
      </div>
      {open && (
        <div id={panel} ref={logRef} className="col g6 s12 t2 task-log" aria-live="off">
          {task.log.length === 0 && !task.error && <div>{t("run.task.noLog")}</div>}
          {task.log.map((entry, index) => (
            <div key={`${index}-${entry.at}`} className="row g8 as">
              <span className="mono t3" style={{ width: 62, flex: "none" }}>
                {formatTimeSeconds(entry.at)}
              </span>
              <span dir="auto" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                {entry.text}
              </span>
            </div>
          ))}
          {task.error && (
            <div className="row g8" style={{ color: "#8F2110" }}>
              <Icon name="warning" size={14} />
              {task.error}
            </div>
          )}
        </div>
      )}
    </article>
  );
}

function VerificationCard({ run }: { run: RunView }) {
  const { t, formatTimeSeconds, formatNumber } = useI18n();
  const log = run.verification_log ?? [];
  const logRef = useStickToBottom<HTMLDivElement>(log.length);
  const showLog = run.verification === "running" && log.length > 0;
  const allIn = run.tasks.length > 0 && run.tasks.every((task) => task.state === "done" || task.state === "failed");
  const failed = run.tasks.filter((task) => task.state === "failed").length;
  const reported = run.tasks.length - failed;

  let body: string;
  if (run.verification === "running") body = run.verification_activity || t("run.verify.running");
  else if (run.verification === "done") body = t("run.verify.done");
  else if (allIn && failed > 0 && reported > 0) body = t("run.verify.next", { reported, total: run.tasks.length });
  else if (run.state === "failed" || run.state === "cancelled") body = t("run.verify.skipped");
  else body = t("run.verify.waiting", { n: run.tasks.length });

  return (
    <article className="card hatch" style={{ padding: "14px 18px", borderStyle: "dashed" }}>
      <div className="row g14 task-row">
      <span className="wk lg" style={{ background: "var(--warn-bg)", color: "#7A4400" }}>
        <Icon name="shield" size={19} />
      </span>
      <div className="grow task-main" style={{ minWidth: 0 }}>
        <div className="b6 s15">{t("run.verify.title")}</div>
        <div className="s13 t2" dir="auto">
          {body}
          {run.verification === "running" && <span className="caret" aria-hidden="true" />}
        </div>
      </div>
      {run.verification === "running" && (run.verification_progress ?? 0) > 0 && (
        <div className="col g6 task-progress">
          <div className="row jb s12 t3">
            <span>{t("run.verify.progress")}</span>
            <span className="num">{formatNumber(run.verification_progress ?? 0)}%</span>
          </div>
          <ProgressBar value={run.verification_progress ?? 0} color="#A85F00" />
        </div>
      )}
      {run.verification === "done" ? (
        <Chip tone="ok" icon="check">
          {t("run.task.done")}
        </Chip>
      ) : run.verification === "running" ? (
        <Chip tone="blue">{t("run.task.working")}</Chip>
      ) : (
        <Chip tone="draft">{t("run.task.queued")}</Chip>
      )}
      </div>
      {showLog && (
        <div ref={logRef} className="col g6 s12 t2 task-log" aria-live="off">
          {log.map((entry, index) => (
            <div key={`${index}-${entry.at}`} className="row g8 as">
              <span className="mono t3" style={{ width: 62, flex: "none" }}>
                {formatTimeSeconds(entry.at)}
              </span>
              <span dir="auto" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                {entry.text}
              </span>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}

function EventRow({ event }: { event: RunEvent }) {
  const { t, formatTime } = useI18n();
  let source = event.source;
  if (event.source === "supervisor") {
    source = t("run.log.supervisor");
  } else if (event.source === "verification") {
    source = "Verification Gate";
  } else if (event.source === "cross_signal") {
    source = "Cross-Signal Synthesis";
  } else if (event.source === "decision_analysis") {
    source = "Strategic Decision Analysis";
  } else if (event.source in WORKER_META) {
    source = t(`worker.label.${workerKey(event.source)}` as TKey);
  }
  const callout = event.callout;
  const isDecision = event.kind === "failure" || event.kind === "decision";

  const text = callout?.highlight && callout.text.includes(callout.highlight)
    ? (() => {
        const start = callout.text.indexOf(callout.highlight);
        return (
          <>
            {callout.text.slice(0, start)}
            <span className="mk">{callout.highlight}</span>
            {callout.text.slice(start + callout.highlight.length)}
          </>
        );
      })()
    : callout?.text;

  return (
    <div className="row as g12">
      <span className="mono t3" style={{ width: 40, paddingTop: 2, flex: "none" }}>
        {formatTime(event.at)}
      </span>
      <div className="grow">
        <div className="row g6 s12 t3">{source}</div>
        <div className="s13 lh14" dir="auto" style={{ marginTop: 2 }}>
          {event.message}
        </div>
        {callout && (
          <div
            className="s13 lh14"
            style={{
              marginTop: 8,
              padding: "10px 12px",
              borderRadius: 10,
              background: isDecision ? "var(--bad-bg)" : "var(--paper2)",
              color: isDecision ? "#8F2110" : undefined,
            }}
          >
            <div className="row g6 b6 s12" style={{ marginBottom: 3 }}>
              <Icon name={isDecision ? "warning" : "branch"} size={14} />
              {t(isDecision ? "run.log.decision" : "run.log.replan")}
            </div>
            <div dir="auto">{text}</div>
          </div>
        )}
      </div>
    </div>
  );
}

function CancelDialog({ onConfirm, onClose, busy }: { onConfirm: () => void; onClose: () => void; busy: boolean }) {
  const { t } = useI18n();
  return (
    <Overlay variant="modal" label={t("run.cancel.title")} onClose={onClose}>
      {(close) => (
        <div className="col" style={{ padding: 24, gap: 16 }}>
          <div>
            <div className="disp s20">{t("run.cancel.title")}</div>
            <p className="t2 s13 lh14" style={{ margin: "6px 0 0" }}>
              {t("run.cancel.body")}
            </p>
          </div>
          <div className="row g8" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn" data-autofocus onClick={close} disabled={busy}>
              {t("run.cancel.keep")}
            </button>
            <button type="button" className="btn danger" onClick={onConfirm} disabled={busy} aria-busy={busy}>
              {t("run.cancel.confirm")}
            </button>
          </div>
        </div>
      )}
    </Overlay>
  );
}

/** Checkpoint 2: what verification concluded, and the person's decision. */
function ReviewPanel({ run, onDone }: { run: RunView; onDone: () => void }) {
  const L = useL();
  const review = run.review!;
  const [tasks, setTasks] = useState(review.proposed_tasks);
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const decide = async (decision: "continue" | "replan" | "stop") => {
    setBusy(true);
    setError(null);
    try {
      await edrak.review(run.analysis_id, decision, decision === "replan" ? tasks : [], note || undefined);
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  const recommended = (d: string) => (review.recommended === d ? " · " + L("suggested", "مقترح") : "");
  return (
    <section className="panel ink stack gap12" aria-label={L("Review", "مراجعة")}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <span className="eyebrow" style={{ color: "#9aa0d0" }}>{L(`Checkpoint 2 of 2 · round ${review.round}`, `نقطة المراجعة 2 من 2 · الجولة ${review.round}`)}</span>
        <span className="s13" style={{ opacity: 0.8 }}>{L(`${review.findings_verified} of ${review.findings_total} findings verified`, `${review.findings_verified} من ${review.findings_total} نتيجة موثقة`)}</span>
      </div>
      <h2 className="disp" style={{ margin: 0, fontSize: 24 }}>{L("Review what was found before the brief is written.", "راجع ما وُجد قبل كتابة الموجز.")}</h2>
      <p style={{ margin: 0, opacity: 0.85 }} dir="auto">{review.summary}</p>
      {((review as { missing_metrics?: { metric_id: string; entity: string; reason: string | null }[] }).missing_metrics ?? []).length > 0 && (
        <div className="stack gap4">
          <b className="s13">{L("Required figures still missing", "أرقام مطلوبة ما زالت ناقصة")}</b>
          <div className="row g4 wrap">
            {((review as { missing_metrics?: { metric_id: string; entity: string; reason: string | null }[] }).missing_metrics ?? []).slice(0, 12).map((m) => (
              <span key={`${m.metric_id}-${m.entity}`} className="chip c-draft" title={(m.reason ?? "not_found").replace(/_/g, " ")}>{m.metric_id.replace(/_/g, " ")} · {m.entity}</span>
            ))}
          </div>
        </div>
      )}
      {(review.conflicts.length > 0 || review.missing_information.length > 0) && (
        <div className="grid2" style={{ gridTemplateColumns: "1fr 1fr" }}>
          {review.conflicts.length > 0 && <div className="stack gap4"><b className="s13">{L("Conflicts", "تعارضات")}</b>{review.conflicts.slice(0, 5).map((c) => <span key={c} className="s13" style={{ opacity: 0.85 }} dir="auto">· {c}</span>)}</div>}
          {review.missing_information.length > 0 && <div className="stack gap4"><b className="s13">{L("Still missing", "ما زال ناقصًا")}</b>{review.missing_information.slice(0, 5).map((c) => <span key={c} className="s13" style={{ opacity: 0.85 }} dir="auto">· {c}</span>)}</div>}
        </div>
      )}
      {/* Finding Cards */}
      {review.findings && review.findings.length > 0 && (
        <div className="stack gap8" style={{ marginTop: 4 }}>
          <div className="row jb" style={{ alignItems: "center" }}>
            <b className="s13" style={{ color: "#fff" }}>{L("Findings discovered", "النتائج التي وُجدت")}</b>
            <span className="s12" style={{ opacity: 0.7 }}>{L(`${review.findings.length} findings`, `${review.findings.length} نتيجة`)}</span>
          </div>
          <div className="stack gap8" style={{ maxHeight: 380, overflowY: "auto", paddingInlineEnd: 4 }}>
            {review.findings.map((f) => (
              <div key={f.finding_id} style={{
                background: "rgba(255,255,255,0.06)",
                border: "1px solid rgba(255,255,255,0.12)",
                borderRadius: 8,
                padding: "10px 12px",
                display: "flex",
                flexDirection: "column",
                gap: 6
              }}>
                <div className="row jb" style={{ alignItems: "flex-start", gap: 8 }}>
                  <div className="row g6 wrap" style={{ alignItems: "center" }}>
                    <span className="chip" style={{ fontSize: 11, padding: "2px 6px", background: "rgba(255,255,255,0.12)", color: "#fff" }}>
                      {f.worker.replace("_", " ")}
                    </span>
                    <span className={`chip ${f.verification_status === "verified" ? "c-verified" : "c-draft"}`} style={{ fontSize: 11, padding: "2px 6px" }}>
                      {f.verification_status === "verified" ? L("Verified", "موثق") : L("Insufficient", "غير كافٍ")}
                    </span>
                  </div>
                  {/* Top right: Strategic goal impact */}
                  <span className="chip" style={{
                    fontSize: 11,
                    padding: "2px 8px",
                    fontWeight: 600,
                    background: f.strategic_impact === "high" ? "rgba(245,213,71,0.2)" : f.strategic_impact === "medium" ? "rgba(99,102,241,0.2)" : "rgba(255,255,255,0.08)",
                    color: f.strategic_impact === "high" ? "var(--gold, #f5d547)" : f.strategic_impact === "medium" ? "#a5b4fc" : "#94a3b8",
                    border: `1px solid ${f.strategic_impact === "high" ? "rgba(245,213,71,0.4)" : f.strategic_impact === "medium" ? "rgba(99,102,241,0.4)" : "rgba(255,255,255,0.12)"}`
                  }}>
                    {f.strategic_impact === "high" ? L("High strategic impact", "أثر استراتيجي مرتفع") :
                     f.strategic_impact === "medium" ? L("Medium strategic impact", "أثر استراتيجي متوسط") :
                     L("Low strategic impact", "أثر استراتيجي منخفض")}
                  </span>
                </div>

                <div className="s13 b6" style={{ color: "#fff", lineHeight: 1.35 }} dir="auto">
                  {f.claim}
                </div>

                {f.why_it_matters && (
                  <div className="s12" style={{ color: "#cbd5e1", lineHeight: 1.3 }} dir="auto">
                    <span style={{ opacity: 0.7, marginInlineEnd: 4 }}>{L("Why it matters:", "أهميته:")}</span>
                    {f.why_it_matters}
                  </div>
                )}

                {f.evidence && f.evidence.length > 0 && (
                  <div style={{ marginTop: 2, paddingTop: 6, borderTop: "1px dashed rgba(255,255,255,0.1)", display: "flex", flexDirection: "column", gap: 4 }}>
                    {f.evidence.slice(0, 1).map((ev, i) => (
                      <div key={i} className="s11" style={{ color: "#94a3b8", lineHeight: 1.3 }}>
                        <div className="row g6 wrap" style={{ alignItems: "center" }}>
                          {ev.source_url ? (
                            <a href={ev.source_url} target="_blank" rel="noopener noreferrer" style={{ color: "#60a5fa", textDecoration: "underline", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {ev.source_title || ev.source_url}
                            </a>
                          ) : (
                            <span>{ev.source_title || L("Internal document", "مستند داخلي")}</span>
                          )}
                          {ev.published_at && <span>· {ev.published_at.slice(0, 10)}</span>}
                          <span>· <span style={{ color: ev.verification_status === "verified" ? "#4ade80" : "#fbbf24" }}>{ev.verification_status}</span></span>
                        </div>
                        {ev.excerpt && (
                          <div style={{ marginTop: 2, color: "#cbd5e1", fontStyle: "italic", maxHeight: 36, overflow: "hidden", textOverflow: "ellipsis" }} dir="auto">
                            "{ev.excerpt.slice(0, 140)}..."
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {tasks.length > 0 && (
        <div className="stack gap8">
          <div className="row g8"><b className="s13">{L("Proposed re-plan", "إعادة التخطيط المقترحة")}</b>
            <button type="button" className="btn ghost" style={{ height: 28, color: "inherit" }} onClick={() => setEditing((v) => !v)}>{editing ? L("Done", "تم") : L("Edit tasks", "عدّل المهام")}</button></div>
          {tasks.map((task, i) => (
            <div key={task.task_id} className="stack gap4" style={{ background: "rgba(255,255,255,.06)", borderRadius: 10, padding: 10 }}>
              <span className="s12" style={{ opacity: 0.7 }}>{task.worker.replace("_", " ")}</span>
              {editing ? (
                <textarea className="textarea" style={{ minHeight: 60 }} value={task.goal} onChange={(e) => setTasks(tasks.map((x, j) => (j === i ? { ...x, goal: e.target.value } : x)))} dir="auto" />
              ) : <span className="s13" dir="auto">{task.goal}</span>}
              {editing && <button type="button" className="btn ghost" style={{ alignSelf: "flex-start", color: "inherit" }} onClick={() => setTasks(tasks.filter((_, j) => j !== i))}>{L("Remove task", "احذف المهمة")}</button>}
            </div>
          ))}
        </div>
      )}
      <input className="input" style={{ color: "var(--ink, #111)", background: "#fff" }} placeholder={L("Note (optional)", "ملاحظة (اختياري)")} value={note} onChange={(e) => setNote(e.target.value)} dir="auto" />
      {error && <span className="s13" style={{ color: "#ffb4a8" }}>{error}</span>}
      <div className="row g8 wrap">
        <button type="button" className="btn" style={{ background: "var(--gold, #f5d547)", color: "var(--ink)" }} disabled={busy} onClick={() => void decide("continue")}>{L("Continue to the brief", "تابع إلى الموجز") + recommended("continue")}</button>
        {review.can_replan && <button type="button" className="btn" disabled={busy} onClick={() => void decide("replan")}>{L("Send workers out again", "أرسل العمال مجددًا") + recommended("replan")}</button>}
      </div>
    </section>
  );
}

export function LiveRun() {
  const { t } = useI18n();
  const L = useL();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const run = usePolled(() => api.getRun(id), [id], {
    intervalMs: POLL_MS,
    stopWhen: (value) => value.state !== "running",
  });
  const data = run.data;
  const eventsRef = useStickToBottom<HTMLDivElement>(data?.events.length ?? 0);
  const now = useNow(data?.state === "running");
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const [cancelling, setCancelling] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!id || (data && data.state !== "running")) return;
    const unsub = subscribeToRun(id, {
      onStageChange: () => run.reload(),
      onTaskUpdate: () => run.reload(),
      onVerification: () => run.reload(),
      onComplete: () => run.reload(),
    });
    return unsub;
  }, [id, data?.state]);

  useEffect(() => {
    if (data && (data.state === "completed" || data.state === "partial") && data.brief_id) {
      const timer = setTimeout(() => {
        navigate(`/briefs/${data.brief_id}`);
      }, 1200);
      return () => clearTimeout(timer);
    }
  }, [data?.state, data?.brief_id, navigate]);

  const crumbs = [
    { label: t("nav.analyses"), to: "/analyses" },
    { label: data?.title ?? "…" },
    { label: t("run.crumb") },
  ];
  const chrome = <PageChrome crumbs={crumbs} title={t("run.crumb")} />;

  if (run.error && !data) {
    return (
      <>
        {chrome}
        <div className="form-error" role="alert">
          <Icon name="warning" size={16} />
          <span>{t("run.notFound")}</span>
          <button type="button" className="btn sm" onClick={() => navigate("/")}>
            {t("plan.backToList")}
          </button>
        </div>
      </>
    );
  }

  if (!data) {
    return (
      <>
        {chrome}
        <div className="col g20" aria-busy="true">
          <div className="shim" style={{ height: 56, width: 520 }} />
          <div className="shim" style={{ height: 96 }} />
          <div className="row g24 as">
            <div className="col g10 grow">
              {[0, 1, 2, 3].map((key) => (
                <div key={key} className="shim" style={{ height: 78 }} />
              ))}
            </div>
            <div className="shim" style={{ width: 380, height: 380 }} />
          </div>
        </div>
      </>
    );
  }

  const title = titleKey(data);
  const elapsed = (data.finished_at ? Date.parse(data.finished_at) : now) - Date.parse(data.started_at);
  // Until the person opens or closes a card, show the log of every worker that is working or has
  // failed, so what each one is doing is visible without a click.
  const isOpen = (task: RunTaskView) =>
    opened[task.task_id] ?? ((task.state === "running" && task.log.length > 1) || task.state === "failed");

  const cancel = async () => {
    setBusy(true);
    try {
      await api.cancelRun(data.analysis_id);
      run.reload();
    } finally {
      setBusy(false);
      setCancelling(false);
    }
  };

  const again = async () => {
    setBusy(true);
    try {
      const { analysis_id } = await api.rerun(data.analysis_id);
      navigate(`/analyses/${analysis_id}/plan`);
    } catch {
      setBusy(false);
    }
  };

  return (
    <div className="col g20 pg">
      {chrome}
      <div className="row jb ae run-head">
        <div>
          <div className="eyebrow">{t("run.eyebrow", { useCase: t(USE_CASE_META[data.use_case].short) })}</div>
          <h1 className="disp" style={{ fontSize: 32, margin: "8px 0 0" }}>
            {t(title.key, title.params)}
          </h1>
        </div>
        <div className="row g16 run-controls">
          <div className="col" style={{ alignItems: "flex-end" }}>
            <div className="mono t3">{t("run.elapsed")}</div>
            <div className="disp s24 num" role="timer" aria-live="off">
              {formatElapsed(elapsed)}
            </div>
          </div>
          <StatusChip status={data.state} />
          {data.state === "running" && (
            <>
              <button type="button" className="btn ghost" onClick={() => void edrak.notifyInstead(data.analysis_id).then(() => navigate("/"))}>
                {L("Notify me instead", "أبلغني لاحقًا")}
              </button>
              <button type="button" className="btn ghost" onClick={() => void edrak.pause(data.analysis_id).then(() => run.reload())}>
                <Icon name="pause" size={14} /> {L("Pause", "إيقاف مؤقت")}
              </button>
              <button type="button" className="btn" onClick={() => setCancelling(true)}>
                {t("run.cancel")}
              </button>
            </>
          )}
          {data.state === "paused" && (
            <button type="button" className="btn pri" onClick={() => void edrak.resume(data.analysis_id).then(() => run.reload())}>
              <Icon name="play" size={14} /> {L("Resume", "استئناف")}
            </button>
          )}
          {data.brief_id && (
            <button type="button" className="btn pri" onClick={() => navigate(`/briefs/${data.brief_id}`)}>
              {t("run.openBrief")}
              <Icon name="arrow" size={16} flip />
            </button>
          )}
          {(data.state === "failed" || data.state === "cancelled") && (
            <button type="button" className="btn" disabled={busy} onClick={() => void again()}>
              {t("home.action.rerun")}
            </button>
          )}
        </div>
      </div>

      {data.state === "awaiting_review" && data.review && <ReviewPanel run={data} onDone={() => run.reload()} />}

      <StageTracker run={data} />

      <div className="run-body">
        <div className="col g10 grow" style={{ minWidth: 0 }}>
          {data.tasks.map((task) => (
            <WorkerCard
              key={task.task_id}
              task={task}
              open={isOpen(task)}
              onToggle={() => setOpened((current) => ({ ...current, [task.task_id]: !isOpen(task) }))}
            />
          ))}
          <VerificationCard run={data} />
        </div>

        <aside className="col g14 run-side">
          <section className="card col" style={{ padding: 20, gap: 16 }} aria-label={t("run.log.title")}>
            <div className="row jb">
              <span className="eyebrow">{t("run.log.title")}</span>
              <span className="s12 t3">{t("run.log.level")}</span>
            </div>
            <div ref={eventsRef} className="col event-list" style={{ gap: 16 }}>
              {data.events.map((event) => (
                <EventRow key={event.event_id} event={event} />
              ))}
            </div>
          </section>
          <div className="card sunk row as g10" style={{ padding: "14px 16px" }}>
            <Icon name="lock" size={16} className="t2" />
            <div className="s12 t2 lh14">{t("run.log.note")}</div>
          </div>
        </aside>
      </div>
      {cancelling && <CancelDialog busy={busy} onConfirm={() => void cancel()} onClose={() => setCancelling(false)} />}
    </div>
  );
}
