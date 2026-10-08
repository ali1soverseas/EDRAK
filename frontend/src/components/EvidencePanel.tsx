/**
 * The source drawer. It opens from the inline-end side when an evidence number such as
 * E14 is clicked, and closes with Esc, the close button or a click on the backdrop.
 */
import { useState } from "react";
import { useI18n, type TKey } from "../i18n";
import { findingsUsing, hostOf } from "../lib/brief";
import { WORKER_META, workerKey } from "../lib/workers";
import type { Brief, BriefEvidence } from "../types/app";
import type { EvidenceRelation } from "../types/contracts";
import { NoteChip } from "./brief/shared";
import { Chip } from "./ui/Chip";
import { Icon } from "./ui/Icon";
import { Overlay } from "./ui/Overlay";
import { Pips } from "./ui/Progress";

const SOURCE_TYPE: Record<BriefEvidence["evidence"]["source_type"], TKey> = {
  web_page: "sourceType.web_page",
  search_result: "sourceType.search_result",
  official_documentation: "sourceType.official_documentation",
  pricing_page: "sourceType.pricing_page",
  release_notes: "sourceType.release_notes",
  announcement: "sourceType.announcement",
  news_article: "sourceType.news_article",
  review_site: "sourceType.review_site",
  market_report: "sourceType.market_report",
  regulatory: "sourceType.regulatory",
  economic: "sourceType.economic",
  internal_document: "sourceType.internal_document",
  synthetic_internal: "sourceType.synthetic_internal",
  other: "sourceType.other",
};

const RELATION: Record<EvidenceRelation, { tone: "ok" | "bad" | "line"; label: TKey }> = {
  supports: { tone: "ok", label: "drawer.relation.supports" },
  contradicts: { tone: "bad", label: "drawer.relation.contradicts" },
  contextualizes: { tone: "line", label: "drawer.relation.context" },
};

const NOTE_TEXT: Record<BriefEvidence["note"], TKey> = {
  your_data: "drawer.note.your_data",
  primary: "drawer.note.primary",
  vendor_claim: "drawer.note.vendor_claim",
  opinion: "drawer.note.opinion",
  open_data: "drawer.note.open_data",
  news: "drawer.note.news",
  thin: "drawer.note.thin",
};

function citation(item: BriefEvidence, retrieved: string): string {
  const { evidence } = item;
  const parts = [evidence.source_title ?? evidence.extracted_fact, evidence.publisher, evidence.source_url, retrieved];
  return parts.filter((part): part is string => Boolean(part)).join(". ");
}

interface EvidencePanelProps {
  brief: Brief;
  item: BriefEvidence;
  onClose: () => void;
}

export function EvidencePanel({ brief, item, onClose }: EvidencePanelProps) {
  const { t, formatTime, formatDate } = useI18n();
  const [copied, setCopied] = useState(false);
  const { evidence } = item;
  const used = findingsUsing(brief, item);
  const host = hostOf(evidence.source_url);
  const file = typeof evidence.metadata.file === "string" ? evidence.metadata.file : null;
  const retrieved = `${formatDate(evidence.retrieved_at)} ${formatTime(evidence.retrieved_at)}`;

  // What verification said about this source, drawn from the verdicts that cite it.
  const verdicts = brief.verification.findings.filter(
    (verdict) => verdict.worker === item.worker && verdict.evidence_ids.includes(evidence.evidence_id),
  );
  const extra = verdicts.flatMap((verdict) => [...verdict.contradictions, ...verdict.missing_information]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(citation(item, retrieved));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard access can be refused. Nothing else to do.
    }
  };

  return (
    <Overlay variant="drawer" label={t("drawer.title")} onClose={onClose}>
      {(close) => (
        <>
          <div className="row g10" style={{ padding: "18px 24px", borderBottom: "1px solid var(--hair)" }}>
            <span className="ev" style={{ margin: 0, height: 22, fontSize: 12 }}>
              {`E${item.display_no}`}
            </span>
            <span className="eyebrow grow">{t("drawer.title")}</span>
            <button type="button" className="btn sm ghost icon" data-autofocus aria-label={t("common.close")} onClick={close}>
              <Icon name="x" size={16} />
            </button>
          </div>

          <div className="col drawer-body">
            <div>
              <div className="serif" dir="auto" style={{ fontSize: 22, lineHeight: 1.35 }}>
                {evidence.source_title ?? evidence.extracted_fact}
              </div>
              <div className="s12 t3 row g6 wrap" style={{ marginTop: 6 }}>
                <span className="sq6" style={{ background: WORKER_META[item.worker].color }} />
                <span>{t(`worker.label.${workerKey(item.worker)}` as TKey)}</span>
                {(file ?? host ?? evidence.publisher) && (
                  <>
                    <span>·</span>
                    <span>{file ?? host ?? evidence.publisher}</span>
                  </>
                )}
                <span>·</span>
                <span>{t(SOURCE_TYPE[evidence.source_type])}</span>
                <span>·</span>
                <span>{t("drawer.retrieved", { time: formatTime(evidence.retrieved_at) })}</span>
              </div>
            </div>

            <div className="row g10 wrap">
              <NoteChip note={item.note} small={false} />
              <span className="row g8 s12 t2">
                {t("drawer.reliability")}
                <Pips value={item.reliability} />
              </span>
              <Chip tone="line">{t(item.origin === "internal" ? "brief.origin.internal" : "brief.origin.external")}</Chip>
              {evidence.is_synthetic && <Chip tone="ins">{t("drawer.synthetic")}</Chip>}
            </div>

            <div>
              <div className="eyebrow" style={{ marginBottom: 6 }}>
                {t("drawer.read")}
              </div>
              <div className="serif s16 lh14" dir="auto" style={{ padding: "12px 14px", borderRadius: 10, background: "var(--paper2)" }}>
                {`“${evidence.excerpt ?? evidence.extracted_fact}”`}
              </div>
              {evidence.excerpt && (
                <div className="s12 t3" dir="auto" style={{ marginTop: 6 }}>
                  {evidence.extracted_fact}
                </div>
              )}
            </div>

            <div className="hatch row as g10" style={{ padding: "12px 14px", border: "1px dashed var(--hair2)", borderRadius: 12 }}>
              <Icon name="info" size={16} />
              <div className="s13 t2 lh14">
                <b style={{ color: "var(--ink)" }}>{t("drawer.verification")}</b>{" "}
                {evidence.is_synthetic ? t("drawer.note.synthetic") : t(NOTE_TEXT[item.note])}
                {extra.length > 0 && <bdi> {extra.join(" ")}</bdi>}
              </div>
            </div>

            <div>
              <div className="eyebrow" style={{ marginBottom: 6 }}>
                {t("drawer.usedIn")}
              </div>
              {used.length === 0 ? (
                <div className="s13 t3">{t("drawer.unused")}</div>
              ) : (
                used.map((entry, index) => (
                  <div key={`${entry.statement}-${index}`} className="row g8" style={{ padding: "8px 0", borderBottom: index === used.length - 1 ? 0 : "1px solid var(--hair)" }}>
                    <Icon name="link" size={14} className="t3" />
                    <span className="s13 grow" dir="auto">{entry.statement}</span>
                    <Chip tone={RELATION[entry.relation].tone} style={{ height: 20, fontSize: 11, padding: "0 8px" }}>
                      {t(RELATION[entry.relation].label)}
                    </Chip>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="row g8" style={{ padding: "14px 24px 18px", borderTop: "1px solid var(--hair)" }}>
            {evidence.source_url ? (
              <a className="btn sm" href={evidence.source_url} target="_blank" rel="noopener noreferrer">
                <Icon name="external" size={14} />
                {t("drawer.open")}
              </a>
            ) : (
              <button type="button" className="btn sm" disabled title={t("drawer.noOriginal")}>
                <Icon name="external" size={14} />
                {t("drawer.open")}
              </button>
            )}
            <button type="button" className="btn sm ghost" onClick={() => void copy()}>
              <Icon name={copied ? "check" : "copy"} size={14} />
              {copied ? t("drawer.copied") : t("drawer.copy")}
            </button>
          </div>
        </>
      )}
    </Overlay>
  );
}
