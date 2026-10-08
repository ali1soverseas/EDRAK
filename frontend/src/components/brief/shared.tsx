import { createContext, useContext, type ReactNode } from "react";
import { useI18n, type TKey } from "../../i18n";
import type { Brief, BriefEvidence, RichSegment, SourceNoteKind } from "../../types/app";
import { evidenceKey } from "../../types/app";
import { Chip } from "../ui/Chip";

interface EvidenceContextValue {
  brief: Brief;
  byKey: ReadonlyMap<string, BriefEvidence>;
  /** Opens the source drawer for an evidence item. */
  open: (key: string) => void;
}

export const EvidenceContext = createContext<EvidenceContextValue | null>(null);

export function useEvidence(): EvidenceContextValue {
  const value = useContext(EvidenceContext);
  if (!value) throw new Error("useEvidence must be used inside the brief.");
  return value;
}

/** The small blue E-number. Clicking it opens the source drawer. */
export function EvChip({ evKey, no }: { evKey: string; no: number }) {
  const { open } = useEvidence();
  const { t } = useI18n();
  return (
    <button type="button" className="ev" aria-label={t("brief.openSource", { no })} onClick={() => open(evKey)}>
      {`E${no}`}
    </button>
  );
}

/** Summary text: plain text, highlighted phrases, and evidence chips that open the drawer. */
export function RichText({ segments }: { segments: RichSegment[] }) {
  const { byKey } = useEvidence();
  const nodes: ReactNode[] = [];
  segments.forEach((segment, index) => {
    switch (segment.t) {
      case "text":
        nodes.push(segment.text);
        break;
      case "mark":
        nodes.push(
          <span key={index} className="mk">
            {segment.text}
          </span>,
        );
        break;
      case "ev": {
        const key = evidenceKey(segment.worker, segment.evidence_id);
        const found = byKey.get(key);
        // A chip that points at nothing would be a dead end, so leave it out.
        if (found) nodes.push(<EvChip key={index} evKey={key} no={found.display_no} />);
        break;
      }
    }
  });
  return <>{nodes}</>;
}

const NOTE_TONE: Record<SourceNoteKind, "ok" | "line" | "ins"> = {
  your_data: "ok",
  primary: "ok",
  vendor_claim: "line",
  opinion: "line",
  open_data: "line",
  news: "line",
  thin: "ins",
};

const NOTE_LABEL: Record<SourceNoteKind, TKey> = {
  your_data: "note.your_data",
  primary: "note.primary",
  vendor_claim: "note.vendor_claim",
  opinion: "note.opinion",
  open_data: "note.open_data",
  news: "note.news",
  thin: "note.thin",
};

export const noteLabelKey = (note: SourceNoteKind): TKey => NOTE_LABEL[note];

/** The label in the Note column and the drawer: Your data, Primary source, Opinion, and so on. */
export function NoteChip({ note, small = true }: { note: SourceNoteKind; small?: boolean }) {
  const { t } = useI18n();
  return (
    <Chip tone={NOTE_TONE[note]} style={small ? { height: 20, fontSize: 11, padding: "0 8px", justifySelf: "start" } : undefined}>
      {t(NOTE_LABEL[note])}
    </Chip>
  );
}
