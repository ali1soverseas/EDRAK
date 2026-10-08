/**
 * Card 3. The handoff also draws a comparison matrix here, but the contracts have no
 * source for it yet, so until the backend provides one this card shows the evidence
 * table on its own, across the full width.
 */
import { useI18n } from "../../i18n";
import { hostOf } from "../../lib/brief";
import { WORKER_META } from "../../lib/workers";
import type { Brief, BriefEvidence } from "../../types/app";
import { Pips } from "../ui/Progress";
import { NoteChip } from "./shared";

function sourceLine(item: BriefEvidence): string {
  const file = item.evidence.metadata.file;
  if (typeof file === "string" && file) return file;
  return hostOf(item.evidence.source_url) ?? item.evidence.publisher ?? "";
}

function SourceRow({ item, onOpen }: { item: BriefEvidence; onOpen: (item: BriefEvidence) => void }) {
  const { t } = useI18n();
  return (
    <button type="button" className="srow" onClick={() => onOpen(item)} aria-label={t("brief.openSource", { no: item.display_no })}>
      <span className="ev" aria-hidden="true" style={{ margin: 0, justifySelf: "start" }}>
        {`E${item.display_no}`}
      </span>
      <span className="col" style={{ minWidth: 0 }}>
        <span className="s13 b6 ellip" dir="auto">{item.evidence.source_title ?? item.evidence.extracted_fact}</span>
        <span className="s12 t3 ellip" dir="auto">{sourceLine(item)}</span>
      </span>
      <span className="row g6 s12 t2">
        <span className="sq6" style={{ background: WORKER_META[item.worker].color }} />
        {t(item.origin === "internal" ? "brief.origin.internal" : "brief.origin.external")}
      </span>
      <Pips value={item.reliability} />
      <NoteChip note={item.note} />
    </button>
  );
}

export function EvidenceCard({ brief, onOpen }: { brief: Brief; onOpen: (item: BriefEvidence) => void }) {
  const { t, tn } = useI18n();
  return (
    <div className="col" style={{ height: "100%" }}>
      <div className="row as jb bc-a" style={{ gap: 24, marginBottom: 22 }}>
        <div className="col g8">
          <div className="eyebrow tb">{t("brief.sec.3.eyebrow")}</div>
          <h2 className="disp" style={{ fontSize: 34, margin: 0, maxWidth: 1000 }}>
            {t("brief.sec.3.title")}
          </h2>
        </div>
      </div>
      <div className="col bc-b" style={{ flex: 1, minHeight: 0 }}>
        <div className="row jb" style={{ marginBottom: 8 }}>
          <div className="eyebrow">{t("brief.sources")}</div>
          <span className="s12 t3">{t("brief.sourcesCount", { sources: tn("plural.sources", brief.evidence.length) })}</span>
        </div>
        <div className="card sources">
          <div className="srow-head">
            <span className="mono t3">{t("brief.col.ref")}</span>
            <span className="mono t3">{t("brief.col.source")}</span>
            <span className="mono t3">{t("brief.col.origin")}</span>
            <span className="mono t3">{t("brief.col.reliability")}</span>
            <span className="mono t3">{t("brief.col.note")}</span>
          </div>
          <div className="sources-body">
            {brief.evidence.length === 0 ? (
              <div className="s13 t3" style={{ padding: 18 }}>
                {t("brief.noSources")}
              </div>
            ) : (
              brief.evidence.map((item) => <SourceRow key={item.display_no} item={item} onOpen={onOpen} />)
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

