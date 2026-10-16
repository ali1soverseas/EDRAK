import { useL } from "../../lib/useL";
import type { BriefEvidenceItem, SignalView } from "../../types/brief3";
import { Icon } from "../ui/Icon";
import { Overlay } from "../ui/Overlay";
import { EvChips } from "./InsightCard";
import { LENS, useDeck } from "./context";

const NOTE: Record<string, [string, string]> = {
  your_data: ["Your data", "بياناتك"], primary: ["Primary source", "مصدر أولي"], vendor_claim: ["Vendor claim", "ادعاء البائع"],
  opinion: ["Opinion", "رأي"], open_data: ["Open data", "بيانات مفتوحة"], news: ["News", "أخبار"], thin: ["Thin", "ضعيف"],
};

function Dots({ n }: { n: number }) {
  return <span className="rel-dots" aria-label={`${n}/3`}>{[1, 2, 3].map((i) => <i key={i} className={i <= n ? "on" : ""} />)}</span>;
}

/** One source: what it says, where it is from, how reliable it is, which agent found it. */
export function EvidenceDrawer({ item, onClose }: { item: BriefEvidenceItem; onClose: () => void }) {
  const L = useL();
  const e = item.evidence;
  const lens = LENS[item.worker];
  const note = NOTE[item.note] ?? [item.note, item.note];
  return (
    <Overlay variant="drawer" label={L(`Source E${item.display_no}`, `المصدر E${item.display_no}`)} onClose={onClose}>
      {(close) => (
        <div className="drawer-body">
          <div className="row g8">
            <span className="ev big">{`E${item.display_no}`}</span>
            <div className="stack grow" style={{ minWidth: 0 }}>
              <span className="eyebrow">{L("Source", "المصدر")}</span>
              <h3 className="drawer-title" dir="auto">{e.source_title || e.publisher || L("Untitled source", "مصدر بلا عنوان")}</h3>
            </div>
            <button type="button" className="iconbtn" data-autofocus onClick={close} aria-label={L("Close", "إغلاق")}><Icon name="x" size={16} /></button>
          </div>
          <div className="row g8 wrap">
            <span className="chip c-line">{L(note[0], note[1])}</span>
            <span className="chip c-line"><Dots n={item.reliability} /> {L("Reliability", "الموثوقية")}</span>
            {lens && <span className={`lens-tag ${lens.css}`}>{L(lens.en, lens.ar)}</span>}
            <span className="chip c-line">{item.origin === "internal" ? L("Internal", "داخلي") : L("External", "خارجي")}</span>
          </div>
          <section className="drawer-sec">
            <span className="eyebrow">{L("What it says", "ما يقوله")}</span>
            <blockquote className="quote serif" dir="auto">{e.extracted_fact}</blockquote>
            {e.excerpt && e.excerpt !== e.extracted_fact && <p className="s13 t2 lh14" dir="auto">{e.excerpt.slice(0, 900)}{e.excerpt.length > 900 ? "…" : ""}</p>}
          </section>
          <section className="drawer-sec">
            <span className="eyebrow">{L("Where it is from", "من أين")}</span>
            <span className="s13">{e.publisher || "—"}{e.retrieved_at ? ` · ${L("retrieved", "استُرجع")} ${e.retrieved_at.slice(0, 10)}` : ""}</span>
            {e.source_url && <a className="btn ghost sm" href={e.source_url} target="_blank" rel="noreferrer"><Icon name="external" size={13} /> {L("Open the page", "افتح الصفحة")}</a>}
          </section>
        </div>
      )}
    </Overlay>
  );
}

/** One cross-domain signal: what it is, why it matters, the lenses and the evidence behind it. */
export function SignalDrawer({ signal, onClose }: { signal: SignalView; onClose: () => void }) {
  const L = useL();
  const { ask } = useDeck();
  const conflict = signal.type === "conflict";
  return (
    <Overlay variant="drawer" label={signal.title} onClose={onClose}>
      {(close) => (
        <div className="drawer-body">
          <div className="row g8">
            <div className="stack grow" style={{ minWidth: 0 }}>
              <span className="eyebrow">{conflict ? L("Sources disagree", "المصادر متعارضة") : L("Signal", "إشارة")}{signal.type && !conflict ? ` · ${signal.type}` : ""}</span>
              <h3 className="drawer-title" dir="auto">{signal.title}</h3>
            </div>
            <button type="button" className="iconbtn" data-autofocus onClick={close} aria-label={L("Close", "إغلاق")}><Icon name="x" size={16} /></button>
          </div>
          <div className="row g6 wrap">{signal.domains.map((d) => LENS[d] && <span key={d} className={`lens-tag ${LENS[d].css}`}>{L(LENS[d].en, LENS[d].ar)}</span>)}</div>
          <section className="drawer-sec"><span className="eyebrow">{L("What we see", "ما نراه")}</span><p className="serif drawer-lead" dir="auto">{signal.signal}</p></section>
          {conflict && signal.sides && (
            <section className="drawer-sec"><span className="eyebrow">{L("Both sides", "الجانبان")}</span>
              <div className="sides">{signal.sides.map((s) => <div key={s.uid} className="side"><b className="kpi-value sm">{s.value}</b>{s.ref ? <EvChips refs={[s.ref]} /> : null}</div>)}</div>
            </section>)}
          {signal.why_it_matters && <section className="drawer-sec"><span className="eyebrow">{L("So what", "ماذا يعني")}</span><p className="s14 lh14" dir="auto">{signal.why_it_matters}</p></section>}
          {signal.interpretation && signal.interpretation !== signal.why_it_matters && <section className="drawer-sec"><span className="eyebrow">{L("Reading", "القراءة")}</span><p className="s13 t2 lh14" dir="auto">{signal.interpretation}</p></section>}
          {(signal.evidence_gaps?.length ?? 0) > 0 && <section className="drawer-sec"><span className="eyebrow">{L("What would make it firmer", "ما يجعلها أقوى")}</span>{signal.evidence_gaps!.map((g) => <span key={g} className="s13">· {g}</span>)}</section>}
          <section className="drawer-sec"><span className="eyebrow">{L("Evidence", "الأدلة")}</span>{signal.refs.length ? <EvChips refs={signal.refs} max={12} /> : <span className="s13 t3">{L("No linked source.", "لا مصدر مرتبط.")}</span>}</section>
          <button type="button" className="btn ink" onClick={() => { close(); ask({ signalId: signal.signal_id, title: signal.title }); }}><Icon name="sparkle" size={14} /> {L("Ask Edrak about this", "اسأل إدراك عن هذا")}</button>
        </div>
      )}
    </Overlay>
  );
}
