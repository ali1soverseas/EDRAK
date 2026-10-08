import { useNavigate } from "react-router-dom";
import { useState } from "react";
import { useI18n, type TKey } from "../../i18n";
import { api } from "../../services/api";
import { workerKey } from "../../lib/workers";
import type { Brief, BriefFinding, BriefLens } from "../../types/app";
import { Chip } from "../ui/Chip";
import { Icon } from "../ui/Icon";
import { Pips } from "../ui/Progress";
import { WorkerBadge } from "../ui/WorkerBadge";
import { EvChip } from "./shared";

function FindingRow({ finding }: { finding: BriefFinding }) {
  const { t } = useI18n();
  return (
    <div className="finding">
      <div className="serif finding-text" dir="auto">{finding.finding.statement}</div>
      <div className="row g8 wrap" style={{ gap: 6 }}>
        {finding.refs.length === 0 ? (
          <span className="s12 t3">{t("brief.noSource")}</span>
        ) : (
          finding.refs.map((ref) => <EvChip key={ref.key} evKey={ref.key} no={ref.display_no} />)
        )}
        <span className="grow" />
        <Pips value={finding.reliability} />
        {finding.status === "verified" && (
          <Chip tone="ok" icon="check" iconSize={11} style={{ height: 20, fontSize: 11, padding: "0 8px" }}>
            {t("status.verified")}
          </Chip>
        )}
        {finding.status === "insufficient" && (
          <Chip tone="ins" style={{ height: 20, fontSize: 11, padding: "0 8px" }}>
            {t("brief.insufficient")}
          </Chip>
        )}
        {finding.status === "unchecked" && (
          <Chip tone="draft" style={{ height: 20, fontSize: 11, padding: "0 8px" }}>
            {t("brief.unchecked")}
          </Chip>
        )}
      </div>
    </div>
  );
}

function LensCard({ lens, analysisId }: { lens: BriefLens; analysisId: string }) {
  const { t, tn } = useI18n();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const key = workerKey(lens.worker);

  const rerun = async () => {
    setBusy(true);
    try {
      const { analysis_id } = await api.rerun(analysisId);
      navigate(`/analyses/${analysis_id}/plan`);
    } catch {
      setBusy(false);
    }
  };

  if (lens.failed) {
    return (
      <section className="card hatch lens failed">
        <div className="row g10">
          <WorkerBadge worker={lens.worker} large iconSize={17} />
          <div className="grow">
            <div className="b6 s14">{t(`worker.name.${key}` as TKey)}</div>
            <div className="s12 t3">{t("brief.lens.workerFailed")}</div>
          </div>
          <Chip tone="bad" icon="x" iconSize={11} style={{ height: 20, fontSize: 11, padding: "0 8px" }}>
            {t("run.task.failed")}
          </Chip>
        </div>
        <div className="col g10 grow lens-failed-body">
          <div className="b6 s14">{t("brief.lens.noFindings")}</div>
          <div className="s13 t2 lh14" dir="auto">{lens.result?.error ?? t("brief.lens.noResult")}</div>
          <button type="button" className="btn sm" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={() => void rerun()}>
            <Icon name="refresh" size={14} />
            {t("home.action.rerun")}
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="card lens">
      <div className="row g10" style={{ marginBottom: 6 }}>
        <WorkerBadge worker={lens.worker} large iconSize={17} />
        <div className="grow">
          <div className="b6 s14">{t(`worker.name.${key}` as TKey)}</div>
          <div className="s12 t3">{t(`worker.sub.${key}` as TKey)}</div>
        </div>
      </div>
      <div className="lens-findings">
        {lens.findings.map((finding) => (
          <FindingRow key={finding.finding.finding_id} finding={finding} />
        ))}
        {lens.findings.length === 0 && <div className="finding s13 t3">{t("brief.lens.noFindings")}</div>}
      </div>
      <div className="row jb s12 t3 lens-foot">
        <span>{tn("plural.findings", lens.findings.length)}</span>
        <span>{tn("plural.sources", lens.source_count)}</span>
      </div>
    </section>
  );
}

export function FindingsCard({ brief }: { brief: Brief }) {
  const { t } = useI18n();
  const reported = brief.lenses.filter((lens) => !lens.failed).length;
  const synthesis = brief.synthesis;

  let titleKey: TKey;
  if (reported < brief.lenses.length) titleKey = "brief.sec.2.titlePartial";
  else titleKey = "brief.sec.2.title";

  const highlight = synthesis && synthesis.statement.includes(synthesis.highlight) ? synthesis.statement.indexOf(synthesis.highlight) : -1;

  return (
    <div className="col" style={{ height: "100%" }}>
      <div className="row as jb bc-a" style={{ gap: 24, marginBottom: 22 }}>
        <div className="col g8">
          <div className="eyebrow tb">{t("brief.sec.2.eyebrow")}</div>
          <h2 className="disp" style={{ fontSize: 34, margin: 0, maxWidth: 1000 }}>
            {titleKey === "brief.sec.2.titlePartial" ? t(titleKey, { reported, total: brief.lenses.length }) : t(titleKey)}
          </h2>
        </div>
        <div className="col g6" style={{ alignItems: "flex-end", paddingTop: 6 }}>
          <span className="s13 t3">{t("brief.sec.2.note")}</span>
        </div>
      </div>
      <div className="row bc-b lens-row">
        {brief.lenses.map((lens) => (
          <LensCard key={lens.worker} lens={lens} analysisId={brief.analysis_id} />
        ))}
      </div>
      {synthesis && (
        <div className="card inkc row g20 bc-c synthesis">
          <div className="col g8" style={{ width: 200, flex: "none" }}>
            <span className="row jc" style={{ width: 32, height: 32, borderRadius: 10, background: "var(--mark)", color: "var(--ink)" }}>
              <Icon name="sparkle" size={17} />
            </span>
            <span className="mono" style={{ color: "#8D8FC0" }}>
              {t("brief.synthesis")}
            </span>
          </div>
          <div className="serif grow" dir="auto" style={{ fontSize: 22, lineHeight: 1.4, color: "#F4F3EE" }}>
            {highlight < 0 ? (
              synthesis.statement
            ) : (
              <>
                {synthesis.statement.slice(0, highlight)}
                <span className="synth-mark">{synthesis.highlight}</span>
                {synthesis.statement.slice(highlight + synthesis.highlight.length)}
              </>
            )}
          </div>
          <div className="s13" dir="auto" style={{ width: 240, flex: "none", color: "#B9B7D6" }}>
            {synthesis.support}
          </div>
        </div>
      )}
    </div>
  );
}
