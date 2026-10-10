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
  const count = brief.lenses.length;

  let titleText: string;
  if (reported < count) {
    titleText = t("brief.sec.2.titlePartial", { reported, total: count });
  } else {
    const specificKey = `brief.sec.2.title.${count}` as TKey;
    const localized = t(specificKey);
    titleText = localized && localized !== specificKey ? localized : t("brief.sec.2.title", { n: count });
  }

  const statement = typeof synthesis?.statement === "string" ? synthesis.statement : "";
  const highlightTerm = typeof synthesis?.highlight === "string" ? synthesis.highlight : "";
  const highlight = highlightTerm && statement.includes(highlightTerm) ? statement.indexOf(highlightTerm) : -1;

  const patterns = synthesis?.patterns || [];
  const signals = synthesis?.signals || [];
  const impacts = synthesis?.impacts || [];

  return (
    <div className="col bc-fill">
      <div className="row as jb bc-head">
        <div className="col g8">
          <div className="eyebrow tb">{t("brief.sec.2.eyebrow")}</div>
          <h2 className="disp" style={{ fontSize: 34, margin: 0, maxWidth: 1000 }}>
            {titleText}
          </h2>
        </div>
        <div className="col g6" style={{ alignItems: "flex-end", paddingTop: 6 }}>
          <span className="s13 t3">{t("brief.sec.2.note")}</span>
        </div>
      </div>
      <div className="row lens-row">
        {brief.lenses.map((lens) => (
          <LensCard key={lens.worker} lens={lens} analysisId={brief.analysis_id} />
        ))}
      </div>
      {synthesis && (
        <div className="synthesis-card">
          <div className="synthesis-head">
            <div className="synthesis-badge">
              <span className="synthesis-badge-icon">
                <Icon name="sparkle" size={13} />
              </span>
              <span>{t("brief.synthesis.badge")}</span>
            </div>
            <div className="synthesis-meta-tags">
              {signals.length > 0 && (
                <span className="synthesis-pill">
                  <Icon name="layers" size={13} />
                  <span>{signals.length} {t("brief.synthesis.signals")}</span>
                </span>
              )}
              {synthesis.support && (
                <span className="synthesis-pill">
                  <Icon name="shield" size={13} />
                  <span>{synthesis.support}</span>
                </span>
              )}
            </div>
          </div>

          <div className="synthesis-statement-wrap">
            <div className="serif synthesis-main-text" dir="auto">
              {highlight < 0 ? (
                statement
              ) : (
                <>
                  {statement.slice(0, highlight)}
                  <span className="synth-mark">{highlightTerm}</span>
                  {statement.slice(highlight + highlightTerm.length)}
                </>
              )}
            </div>
          </div>

          {(patterns.length > 0 || signals.length > 0 || impacts.length > 0) && (
            <div className="synthesis-grid">
              {patterns.length > 0 && (
                <div className="synthesis-item-box" style={{ gridColumn: patterns.length > 0 && signals.length === 0 ? "1 / -1" : undefined }}>
                  <div className="synthesis-subheading">
                    <Icon name="trend" size={13} />
                    <span>{t("brief.synthesis.patterns")}</span>
                  </div>
                  <div className="synthesis-patterns-list">
                    {patterns.slice(0, 4).map((p, idx) => (
                      <div key={idx} className="synthesis-pattern-row" dir="auto">
                        <span className="synthesis-pattern-dot" />
                        <span>{p}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {signals.length > 0 && (
                <div className="synthesis-item-box">
                  <div className="synthesis-subheading">
                    <Icon name="activity" size={13} />
                    <span>{t("brief.synthesis.signals")}</span>
                  </div>
                  <div className="synthesis-patterns-list">
                    {signals.slice(0, 3).map((sig, idx) => (
                      <div key={sig.signal_id || idx} className="col g4" style={{ padding: "6px 0", borderBottom: idx < Math.min(signals.length, 3) - 1 ? "1px solid rgba(255, 255, 255, 0.06)" : undefined }}>
                        <div className="row jb ac g8 wrap">
                          <span className="b6 s13" style={{ color: "#F1F5F9" }} dir="auto">{sig.title}</span>
                          {sig.tension_or_alignment && (
                            <Chip
                              tone={sig.tension_or_alignment.toLowerCase().includes("align") ? "ok" : "warn"}
                              style={{ height: 18, fontSize: 10, padding: "0 6px" }}
                            >
                              {sig.tension_or_alignment.toLowerCase().includes("align")
                                ? t("brief.synthesis.alignment")
                                : t("brief.synthesis.tension")}
                            </Chip>
                          )}
                        </div>
                        {sig.description && (
                          <div className="s12 lh14" style={{ color: "rgba(226, 232, 240, 0.75)" }} dir="auto">
                            {sig.description}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {impacts.length > 0 && (
                <div className="synthesis-item-box" style={{ gridColumn: "1 / -1" }}>
                  <div className="synthesis-subheading">
                    <Icon name="target" size={13} />
                    <span>{t("brief.synthesis.impacts")}</span>
                  </div>
                  <div className="synthesis-chips-row">
                    {impacts.slice(0, 6).map((imp, idx) => (
                      <span key={idx} className="synthesis-impact-chip" dir="auto">
                        <Icon name="check" size={12} stroke={2.2} />
                        <span>{imp}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
