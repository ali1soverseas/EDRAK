import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PageChrome } from "../components/shell/AppShell";
import { Chip, StatusChip } from "../components/ui/Chip";
import { Segmented } from "../components/ui/Controls";
import { Icon } from "../components/ui/Icon";
import { useI18n } from "../i18n";
import { useAsync } from "../lib/hooks";
import { USE_CASE_META } from "../lib/usecases";
import { USE_CASES } from "../lib/form";
import { api } from "../services/api";
import type { AnalysisStatus, AnalysisSummary } from "../types/app";

type Filter = "all" | "active" | "done";

const ACTIVE: ReadonlySet<AnalysisStatus> = new Set(["awaiting_approval", "running"]);
const DONE: ReadonlySet<AnalysisStatus> = new Set(["completed", "partial"]);

export const briefNumber = (n: number) => String(n).padStart(4, "0");

/** Where a row leads, and what its button says. */
function destination(row: AnalysisSummary): string {
  switch (row.status) {
    case "awaiting_approval":
      return `/analyses/${row.analysis_id}/plan`;
    case "running":
    case "cancelled":
    case "failed":
      return `/analyses/${row.analysis_id}/run`;
    case "completed":
    case "partial":
      return `/briefs/${row.brief_id}`;
    case "draft":
      return `/analyses/new?draft=${row.analysis_id}`;
  }
}

function AnalysisRow({ row, onRerun, busy }: { row: AnalysisSummary; onRerun: () => void; busy: boolean }) {
  const { t, tn, formatWhen, formatClock } = useI18n();
  const meta = USE_CASE_META[row.use_case];

  let when: string;
  let result: React.ReactNode;
  let action: React.ReactNode;
  const go = destination(row);

  switch (row.status) {
    case "awaiting_approval":
      when = t("home.when.drafted", { when: formatWhen(row.updated_at, { inline: true }) });
      result = t("home.result.plan", { tasks: tn("plural.tasks", row.tasks_total), workers: tn("plural.workers", row.tasks_total) });
      action = (
        <Link to={go} className="btn sm pri">
          {t("home.action.review")}
        </Link>
      );
      break;
    case "running":
      when = t("home.when.started", { time: formatClock(row.updated_at) });
      result = t("home.result.running", { done: row.tasks_done + row.tasks_failed, total: row.tasks_total });
      action = (
        <Link to={go} className="btn sm">
          {t("home.action.watch")}
        </Link>
      );
      break;
    case "partial":
      when = formatWhen(row.updated_at);
      result = t("home.result.partial", { no: briefNumber(row.brief_no ?? 0), failed: tn("plural.failedWorkers", row.tasks_failed) });
      action = (
        <Link to={go} className="btn sm">
          {t("home.action.brief")}
        </Link>
      );
      break;
    case "completed":
      when = formatWhen(row.updated_at);
      result = (
        <>
          {t("home.result.brief", { no: briefNumber(row.brief_no ?? 0) })}{" "}
          {row.repeat_weekly && (
            <Chip tone="line" icon="refresh" iconSize={12} style={{ marginInlineEnd: 0 }}>
              {t("home.weekly")}
            </Chip>
          )}
        </>
      );
      action = (
        <Link to={go} className="btn sm">
          {t("home.action.brief")}
        </Link>
      );
      break;
    case "failed":
      when = formatWhen(row.updated_at);
      result = t("home.result.failed");
      action = (
        <button type="button" className="btn sm" disabled={busy} onClick={onRerun}>
          {t("home.action.rerun")}
        </button>
      );
      break;
    case "cancelled":
      when = formatWhen(row.updated_at);
      result = t("home.result.cancelled");
      action = (
        <button type="button" className="btn sm" disabled={busy} onClick={onRerun}>
          {t("home.action.rerun")}
        </button>
      );
      break;
    case "draft":
      when = t("home.when.saved", { when: formatWhen(row.updated_at, { inline: true }) });
      result = t("home.result.draft");
      action = (
        <Link to={go} className="btn sm">
          {t("home.action.continue")}
        </Link>
      );
      break;
  }

  return (
    <tr>
      <td style={{ width: "42%" }}>
        <div className="b6 s14" dir="auto">
          {row.status === "failed" || row.status === "cancelled" ? (
            row.title
          ) : (
            <Link to={go} className="row-link">
              {row.title}
            </Link>
          )}
        </div>
        <div className="s12 t3" style={{ marginTop: 2 }}>
          {t(meta.short)}
        </div>
      </td>
      <td>
        <StatusChip status={row.status} />
      </td>
      <td className="s13 t2">{when}</td>
      <td className="s13 t2">{result}</td>
      <td style={{ textAlign: "end" }}>{action}</td>
    </tr>
  );
}

export function Dashboard() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const analyses = useAsync(() => api.listAnalyses(), []);
  const [filter, setFilter] = useState<Filter>("all");
  const [busyId, setBusyId] = useState<string | null>(null);

  const rows = analyses.data ?? [];
  const counts = useMemo(
    () => ({
      all: rows.length,
      active: rows.filter((row) => ACTIVE.has(row.status)).length,
      done: rows.filter((row) => DONE.has(row.status)).length,
    }),
    [rows],
  );
  const visible = rows.filter((row) => (filter === "all" ? true : filter === "active" ? ACTIVE.has(row.status) : DONE.has(row.status)));

  const rerun = async (row: AnalysisSummary) => {
    setBusyId(row.analysis_id);
    try {
      const { analysis_id } = await api.rerun(row.analysis_id);
      navigate(`/analyses/${analysis_id}/plan`);
    } catch {
      setBusyId(null);
    }
  };

  return (
    <div className="pg" style={{ gap: 26 }}>
      <PageChrome crumbs={[{ label: t("nav.analyses") }]} title={t("nav.analyses")} />

      <div className="row jb ae">
        <div>
          <div className="eyebrow">{t("nav.analyses")}</div>
          <h1 className="disp" style={{ fontSize: 44, margin: "10px 0 0" }}>
            {t("home.title")}
          </h1>
        </div>
        <Link to="/analyses/new" className="btn pri lg">
          <Icon name="plus" size={18} />
          {t("nav.newAnalysis")}
        </Link>
      </div>

      <div className="usecase-grid">
        {USE_CASES.map((useCase) => {
          const meta = USE_CASE_META[useCase];
          return (
            <Link key={useCase} to={`/analyses/new?use_case=${useCase}`} className="card row g14 usecase-card">
              <span className="uc-icon">
                <Icon name={meta.icon} size={19} />
              </span>
              <div className="grow">
                <div className="b7 s15 uc-title">{t(meta.title)}</div>
                <div className="t2 s13 lh14" style={{ marginTop: 2 }}>
                  {t(meta.description)}
                </div>
              </div>
              <Icon name="arrow" size={17} className="t3" flip />
            </Link>
          );
        })}
      </div>

      <section className="col" style={{ flex: 1, minHeight: 0 }}>
        <div className="row jb" style={{ marginBottom: 6 }}>
          <h2 className="disp s20" style={{ margin: 0 }}>
            {t("home.list")}
          </h2>
          <Segmented<Filter>
            label={t("home.filter")}
            value={filter}
            onChange={setFilter}
            options={[
              { value: "all", label: t("home.filter.all", { n: counts.all }) },
              { value: "active", label: t("home.filter.active", { n: counts.active }) },
              { value: "done", label: t("home.filter.done", { n: counts.done }) },
            ]}
          />
        </div>

        <div className="card" style={{ padding: "4px 10px 2px" }}>
          {analyses.error ? (
            <div className="form-error" role="alert" style={{ margin: 10 }}>
              <Icon name="warning" size={16} />
              <span>{t("common.error.load")}</span>
              <button type="button" className="btn sm" onClick={analyses.reload}>
                {t("common.retry")}
              </button>
            </div>
          ) : analyses.loading && !analyses.data ? (
            <div aria-busy="true" style={{ padding: "6px 4px" }}>
              {[0, 1, 2, 3].map((key) => (
                <div key={key} className="shim skel-row" />
              ))}
            </div>
          ) : visible.length === 0 ? (
            <div className="hatch empty-state">
              <div className="b6 s15">{t(rows.length === 0 ? "home.empty.title" : "home.empty.filtered")}</div>
              {rows.length === 0 && <div className="s13 t2">{t("home.empty.body")}</div>}
            </div>
          ) : (
            <table className="tbl roomy">
              <thead>
                <tr>
                  <th>{t("home.col.analysis")}</th>
                  <th>{t("home.col.status")}</th>
                  <th>{t("home.col.when")}</th>
                  <th>{t("home.col.result")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visible.map((row) => (
                  <AnalysisRow key={row.analysis_id} row={row} busy={busyId === row.analysis_id} onRerun={() => void rerun(row)} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
}
