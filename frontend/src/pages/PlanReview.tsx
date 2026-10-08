import { useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { PageChrome } from "../components/shell/AppShell";
import { StatusChip } from "../components/ui/Chip";
import { Icon } from "../components/ui/Icon";
import { Overlay } from "../components/ui/Overlay";
import { WorkerBadge } from "../components/ui/WorkerBadge";
import { useI18n, type TKey } from "../i18n";
import { useAsync } from "../lib/hooks";
import { workerKey } from "../lib/workers";
import { api } from "../services/api";
import type { AnalysisDetail, SourceToggle } from "../types/app";
import type { ResearchTask, WorkerType } from "../types/contracts";

const PARALLEL: Record<number, TKey> = {
  1: "plan.parallel.1",
  2: "plan.parallel.2",
  3: "plan.parallel.3",
  4: "plan.parallel.4",
};

const WORKER_FILL: Record<WorkerType, { fill: string; stroke: string }> = {
  internal_intelligence: { fill: "#E4E3EC", stroke: "#0B0D38" },
  competitor_intelligence: { fill: "#E3EAFD", stroke: "#0742ED" },
  market_intelligence: { fill: "#ECE6FB", stroke: "#6A3DDB" },
  customer_trends: { fill: "#DDF0EE", stroke: "#08807A" },
};

/** The source tags under each task, following the sources the person allowed. */
function tagsFor(worker: WorkerType, allowed: SourceToggle[]): TKey[] {
  switch (worker) {
    case "internal_intelligence":
      return ["plan.tag.internal"];
    case "competitor_intelligence":
      return ["plan.tag.competitorSites", "plan.tag.publicWeb"];
    case "market_intelligence":
      return ["plan.tag.news", "plan.tag.openData"];
    case "customer_trends":
      return allowed.includes("internal_files")
        ? ["plan.tag.reviews", "plan.tag.forums", "plan.tag.internal"]
        : ["plan.tag.reviews", "plan.tag.forums"];
  }
}

/** The part of the question to highlight: the first thing the person named that it contains. */
function highlightPhrase(detail: AnalysisDetail): string | null {
  const goal = detail.request.goal.toLowerCase();
  const form = detail.form;
  const candidates = [detail.title, form.offering, form.market, ...form.competitors];
  return candidates.find((phrase) => phrase.trim() && goal.includes(phrase.trim().toLowerCase()))?.trim() ?? null;
}

function Question({ detail }: { detail: AnalysisDetail }) {
  const goal = detail.request.goal;
  const phrase = highlightPhrase(detail);
  if (!phrase) return <>{goal}</>;
  const start = goal.toLowerCase().indexOf(phrase.toLowerCase());
  return (
    <>
      {goal.slice(0, start)}
      <span className="mk">{goal.slice(start, start + phrase.length)}</span>
      {goal.slice(start + phrase.length)}
    </>
  );
}

/** The "order of work" diagram: workers in parallel, then verify, synthesize, report. */
function OrderOfWork({ tasks }: { tasks: ResearchTask[] }) {
  const { t } = useI18n();
  const centre = 59;
  const rows = tasks.map((task, index) => ({ task, y: centre + (index - (tasks.length - 1) / 2) * 26 }));
  return (
    <svg viewBox="0 0 320 128" width="100%" role="img" aria-label={t("plan.order.label")}>
      <g fill="none" stroke="#0B0D38" strokeWidth="1.6" strokeLinecap="round">
        {rows.map(({ task, y }) => (
          <g key={task.task_id}>
            <path d={`M46 ${y}h56`} />
            <path d={`M102 ${y}C124 ${y} 124 ${centre} 146 ${centre}`} />
          </g>
        ))}
        <path d={`M170 ${centre}h34M228 ${centre}h34`} />
      </g>
      <g>
        {rows.map(({ task, y }) => (
          <rect key={task.task_id} x="22" y={y - 12} width="24" height="24" rx="7" fill={WORKER_FILL[task.worker].fill} stroke={WORKER_FILL[task.worker].stroke} />
        ))}
      </g>
      <rect x="146" y="45" width="24" height="28" rx="8" fill="#FBEBD0" stroke="#A85F00" />
      <rect x="204" y="45" width="24" height="28" rx="8" fill="#0B0D38" />
      <rect x="262" y="45" width="30" height="28" rx="8" fill="#0742ED" />
      <g fontFamily="JetBrains Mono" fontSize="8.5" fill="#66647F" textAnchor="middle">
        <text x="158" y="92">{t("plan.order.verify")}</text>
        <text x="216" y="92">{t("plan.order.synth")}</text>
        <text x="277" y="92">{t("plan.order.report")}</text>
        <text x="34" y="124">{t("plan.order.parallel")}</text>
      </g>
    </svg>
  );
}

function RejectDialog({ onSend, onClose, busy }: { onSend: (reason: string) => void; onClose: () => void; busy: boolean }) {
  const { t } = useI18n();
  const [reason, setReason] = useState("");
  return (
    <Overlay variant="modal" label={t("plan.reject.title")} onClose={onClose}>
      {(close) => (
        <form
          className="col"
          style={{ padding: 24, gap: 16 }}
          onSubmit={(event) => {
            event.preventDefault();
            onSend(reason);
          }}
        >
          <div>
            <div className="disp s20">{t("plan.reject.title")}</div>
            <p className="t2 s13 lh14" style={{ margin: "6px 0 0" }}>
              {t("plan.reject.body")}
            </p>
          </div>
          <div>
            <label className="lbl" htmlFor="reject-reason">
              {t("plan.reject.label")}
            </label>
            <textarea
              id="reject-reason"
              data-autofocus
              className="inp"
              rows={4}
              value={reason}
              placeholder={t("plan.reject.placeholder")}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          <div className="row g8" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn" onClick={close} disabled={busy}>
              {t("common.cancel")}
            </button>
            <button type="submit" className="btn pri" disabled={busy} aria-busy={busy}>
              {t("plan.reject.send")}
            </button>
          </div>
        </form>
      )}
    </Overlay>
  );
}

export function PlanReview() {
  const { t, tn } = useI18n();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const analysis = useAsync(() => api.getAnalysis(id), [id]);
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [error, setError] = useState(false);

  const detail = analysis.data;
  const crumbs = [
    { label: t("nav.analyses"), to: "/" },
    { label: detail?.title ?? "…" },
    { label: t("plan.crumb") },
  ];
  const chrome = <PageChrome crumbs={crumbs} title={t("plan.crumb")} />;

  if (analysis.error) {
    return (
      <>
        {chrome}
        <div className="form-error" role="alert">
          <Icon name="warning" size={16} />
          <span>{t("plan.notFound")}</span>
          <button type="button" className="btn sm" onClick={() => navigate("/")}>
            {t("plan.backToList")}
          </button>
        </div>
      </>
    );
  }

  if (!detail || !detail.plan) {
    if (detail && !detail.plan) return <Navigate to={`/analyses/new?draft=${detail.analysis_id}`} replace />;
    return (
      <>
        {chrome}
        <div className="analysis-grid" aria-busy="true">
          <div className="col g16 grow">
            <div className="shim" style={{ height: 52, width: 520 }} />
            <div className="shim" style={{ height: 70 }} />
            {[0, 1, 2, 3].map((key) => (
              <div key={key} className="shim" style={{ height: 96 }} />
            ))}
          </div>
          <div className="shim" style={{ width: 360, height: 420 }} />
        </div>
      </>
    );
  }

  // Only a plan that is waiting for approval belongs on this screen.
  if (detail.status === "running" || detail.status === "cancelled" || detail.status === "failed") {
    return <Navigate to={`/analyses/${detail.analysis_id}/run`} replace />;
  }
  if (detail.status === "draft") return <Navigate to={`/analyses/new?draft=${detail.analysis_id}`} replace />;

  const plan = detail.plan;
  const taskCount = plan.tasks.length;
  const workerCount = new Set(plan.tasks.map((task) => task.worker)).size;

  const approve = async () => {
    setBusy("approve");
    setError(false);
    try {
      await api.approvePlan(detail.analysis_id);
      navigate(`/analyses/${detail.analysis_id}/run`);
    } catch {
      setError(true);
      setBusy(null);
    }
  };

  const reject = async (reason: string) => {
    setBusy("reject");
    try {
      await api.rejectPlan(detail.analysis_id, reason);
      navigate(`/analyses/new?draft=${detail.analysis_id}`);
    } catch {
      setError(true);
      setBusy(null);
      setRejecting(false);
    }
  };

  return (
    <>
      {chrome}
      <div className="analysis-grid">
        <div className="col grow" style={{ minWidth: 0, gap: 18 }}>
          <div className="row jb ae">
            <div>
              <div className="eyebrow">{t("plan.eyebrow")}</div>
              <h1 className="disp plan-title" style={{ fontSize: 32, margin: "8px 0 0" }}>
                {t("plan.title")}
              </h1>
            </div>
            <span style={{ flex: "none" }}>
              <StatusChip status="awaiting_approval" />
            </span>
          </div>

          <div className="card" style={{ padding: "14px 20px", background: "var(--paper2)" }}>
            <div className="eyebrow">{t("plan.question")}</div>
            <div className="serif s18 lh14" dir="auto" style={{ marginTop: 6, maxWidth: 820 }}>
              <Question detail={detail} />
            </div>
          </div>

          <div className="col" style={{ gap: 8, minHeight: 0 }}>
            <div className="row jb">
              <div className="row g10">
                <span className="eyebrow">{t("plan.stage")}</span>
                <span className="b6 s14">{PARALLEL[workerCount] ? t(PARALLEL[workerCount]) : t("plan.parallel.n", { n: workerCount })}</span>
              </div>
              <span className="s12 t3">{t("plan.writtenBy")}</span>
            </div>
            {plan.tasks.map((task, index) => (
              <article key={task.task_id} className="card row as g14" style={{ padding: "14px 18px" }}>
                <WorkerBadge worker={task.worker} large />
                <div className="grow col" style={{ gap: 8, minWidth: 0 }}>
                  <div className="row g8">
                    <span className="mono t3">{`1.${index + 1}`}</span>
                    <span className="b6 s15" dir="auto">{task.goal}</span>
                    <span className="grow" />
                    <span className="s12 t3">{t(`worker.label.${workerKey(task.worker)}` as TKey)}</span>
                  </div>
                  <div className="s13 t2 lh14" dir="auto" style={{ maxWidth: 640 }}>
                    {task.focus}
                  </div>
                  <div className="row g6 wrap">
                    {tagsFor(task.worker, detail.allowed_sources).map((key) => (
                      <span key={key} className="tag tag-sm">
                        {t(key)}
                      </span>
                    ))}
                  </div>
                </div>
              </article>
            ))}
          </div>
        </div>

        <aside className="col plan-side">
          <section className="card inkc col" style={{ padding: 22, gap: 14 }}>
            <div className="eyebrow" style={{ color: "#8D8FC0" }}>
              {t("plan.approval")}
            </div>
            <div className="row g24">
              <div>
                <div className="disp s40">{taskCount}</div>
                <div className="s12" style={{ color: "#B9B7D6" }}>
                  {tn("plural.taskUnit", taskCount)}
                </div>
              </div>
              <div>
                <div className="disp s40">{workerCount}</div>
                <div className="s12" style={{ color: "#B9B7D6" }}>
                  {tn("plural.workerUnit", workerCount)}
                </div>
              </div>
            </div>
            {error && (
              <div className="form-error" role="alert">
                <Icon name="warning" size={16} />
                <span>{t("plan.error")}</span>
              </div>
            )}
            <button type="button" className="btn pri lg" style={{ width: "100%" }} disabled={busy !== null || taskCount === 0} aria-busy={busy === "approve"} onClick={() => void approve()}>
              <Icon name="play" size={16} />
              {t("plan.approve")}
            </button>
            <button
              type="button"
              className="btn"
              style={{ background: "transparent", color: "#F4F3EE", borderColor: "#3A3C6E", width: "100%" }}
              disabled={busy !== null}
              onClick={() => setRejecting(true)}
            >
              {t("plan.reject")}
            </button>
            <div className="s12" style={{ color: "#8D8FC0", lineHeight: 1.5 }}>
              {t("plan.approvalHint")}
            </div>
          </section>

          <section className="card pad col g12">
            <div className="eyebrow">{t("plan.order")}</div>
            <OrderOfWork tasks={plan.tasks} />
            <div className="col" style={{ gap: 0 }}>
              {(
                [
                  ["shield", "plan.order.verification"],
                  ["layers", "plan.order.synthesis"],
                  ["fileLines", "plan.order.brief"],
                ] as const
              ).map(([icon, key]) => (
                <div key={key} className="row as g10" style={{ padding: "9px 0", borderTop: "1px solid var(--hair)" }}>
                  <Icon name={icon} size={16} className="t2" />
                  <div className="s12 t2 lh14">
                    <b style={{ color: "var(--ink)" }}>{t(`${key}.name` as TKey)}</b> {t(`${key}.text` as TKey)}
                  </div>
                </div>
              ))}
            </div>
            <div className="s12 t3 lh14">{t("plan.order.note")}</div>
          </section>

          <div className="card sunk row as g10" style={{ padding: "14px 16px" }}>
            <Icon name="info" size={16} className="t2" />
            <div className="s12 t2 lh14">{t("plan.readOnly")}</div>
          </div>
        </aside>
      </div>
      {rejecting && <RejectDialog busy={busy === "reject"} onSend={(reason) => void reject(reason)} onClose={() => setRejecting(false)} />}
    </>
  );
}
