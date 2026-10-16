/**
 * The brief: five full-height slides you move through, a dot rail with a label on hover, arrow
 * buttons and keys, a side chat and the detail drawers.
 *
 * `.bscroll` must be the scroll element. Snapping and the blur between slides are driven by CSS
 * scroll timelines on it (edrak.css). Keep the slides out of the page scroll.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useFullscreen } from "../../lib/useFullscreen";
import { modKey } from "../../lib/platform";
import { useL } from "../../lib/useL";
import { api } from "../../services/api";
import { edrak } from "../../services/edrak";
import type { BriefV3, DeckSlide } from "../../types/brief3";
import { ChatPanel, type ChatScope } from "../chat/ChatPanel";
import { CardDrawer } from "../dashboard/CardDrawer";
import { Icon } from "../ui/Icon";
import { DeckContext, type DeckApi } from "./context";
import { EvidenceDrawer, SignalDrawer } from "./Drawers";
import { DecisionsSlide } from "./slides/DecisionsSlide";
import { EvidenceSlide } from "./slides/EvidenceSlide";
import { MetricsSlide } from "./slides/MetricsSlide";
import { SignalsSlide } from "./slides/SignalsSlide";
import { SummarySlide } from "./slides/SummarySlide";

const DESC: Record<DeckSlide["id"], [string, string, string, string]> = {
  summary: ["Summary", "الملخص", "The insight, the key findings and how complete the picture is.", "الفكرة والنتائج الرئيسية ومدى اكتمال الصورة."],
  business_metrics: ["Business metrics", "مقاييس الأعمال", "Our figures against theirs, each traced to a source.", "أرقامنا مقابل أرقامهم، كل رقم بمصدره."],
  signals: ["Signals", "الإشارات", "What the lenses show together.", "ما تُظهره العدسات معًا."],
  decisions: ["Decisions", "القرارات", "Distinct options, then the action plan for the one you choose.", "خيارات مميزة، ثم خطة العمل لما تختاره."],
  evidence_gaps: ["Evidence and gaps", "الأدلة والفجوات", "Coverage, sources, conflicts and what is missing.", "التغطية والمصادر والتعارضات والناقص."],
};

const briefNumber = (n: number) => String(n).padStart(4, "0");

function Slide({ slide }: { slide: DeckSlide }) {
  switch (slide.id) {
    case "summary": return <SummarySlide slide={slide} />;
    case "business_metrics": return <MetricsSlide slide={slide} />;
    case "signals": return <SignalsSlide slide={slide} />;
    case "decisions": return <DecisionsSlide slide={slide} />;
    default: return <EvidenceSlide slide={slide} />;
  }
}

export function BriefDeck({ initial }: { initial: BriefV3 }) {
  const L = useL();
  const navigate = useNavigate();
  const [brief, setBrief] = useState<BriefV3>(initial);
  const container = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const frame = useRef(0);
  const { fullscreen, toggle } = useFullscreen(container);
  const [active, setActive] = useState(0);
  const [scrolled, setScrolled] = useState(false);
  const [card, setCard] = useState<string | null>(null);
  const [evidenceNo, setEvidenceNo] = useState<number | null>(null);
  const [signalId, setSignalId] = useState<string | null>(null);
  const [chat, setChat] = useState<ChatScope | null>(null);
  const [menu, setMenu] = useState(false);
  const [lifecycle, setLifecycle] = useState(initial.lifecycle);

  const slides = brief.deck?.slides ?? [];
  const reload = useCallback(() => {
    void api.getBrief(brief.brief_id).then((b) => { setBrief(b as unknown as BriefV3); setLifecycle((b as unknown as BriefV3).lifecycle); });
  }, [brief.brief_id]);

  const goToIndex = useCallback((index: number) => {
    const el = scroller.current;
    if (!el) return;
    const i = Math.max(0, Math.min(slides.length - 1, index));
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({ top: i * el.clientHeight, behavior: calm ? "auto" : "smooth" });
  }, [slides.length]);

  const api_: DeckApi = useMemo(() => ({
    brief,
    openCard: (k) => { if (brief.cards[k]) setCard(k); },
    openEvidence: (n) => { if (brief.evidence.some((e) => e.display_no === n)) setEvidenceNo(n); },
    openSignal: (id) => setSignalId(id),
    ask: (scope) => setChat({ cardKey: scope?.cardKey, signalId: scope?.signalId, title: scope?.title, nonce: Date.now() }),
    goTo: (id) => goToIndex(slides.findIndex((s) => s.id === id)),
    reload,
  }), [brief, goToIndex, slides, reload]);

  const onScroll = () => {
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      const el = scroller.current;
      if (!el || el.clientHeight === 0) return;
      setActive(Math.max(0, Math.min(slides.length - 1, Math.round(el.scrollTop / el.clientHeight))));
      setScrolled(el.scrollTop > 8);
    });
  };
  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  // Arrows and Page keys move between slides; Cmd/Ctrl+J opens the chat. Typing and open drawers win.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") { e.preventDefault(); setChat((c) => (c ? null : { nonce: Date.now() })); return; }
      if (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable || document.querySelector(".ovl")) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const rtl = document.documentElement.dir === "rtl";
      const next = ["ArrowDown", "PageDown", rtl ? "ArrowLeft" : "ArrowRight"];
      const prev = ["ArrowUp", "PageUp", rtl ? "ArrowRight" : "ArrowLeft"];
      // Up and down scroll a long slide first; at its edge they move to the next slide.
      const inner = t.closest?.(".slide-body") as HTMLElement | null;
      if (inner && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        const atEnd = inner.scrollTop + inner.clientHeight >= inner.scrollHeight - 2;
        const atStart = inner.scrollTop <= 1;
        if ((e.key === "ArrowDown" && !atEnd) || (e.key === "ArrowUp" && !atStart)) return;
      }
      if (next.includes(e.key)) { e.preventDefault(); goToIndex(active + 1); }
      else if (prev.includes(e.key)) { e.preventDefault(); goToIndex(active - 1); }
      else if (e.key === "Home") { e.preventDefault(); goToIndex(0); }
      else if (e.key === "End") { e.preventDefault(); goToIndex(slides.length - 1); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [active, goToIndex, slides.length]);

  const names = slides.map((s) => L(DESC[s.id][0], DESC[s.id][1]));
  const openCard = card ? brief.cards[card] : null;
  const evidence = evidenceNo != null ? brief.evidence.find((e) => e.display_no === evidenceNo) : null;
  const signal = signalId ? brief.signals_view.find((s) => s.signal_id === signalId) : null;

  return (
    <DeckContext.Provider value={api_}>
      <div ref={container} className={`col brief-page deck ${fullscreen ? "brief-fullscreen" : ""}`}>
        <div className="row jb brief-bar deck-bar">
          <div className="row g10" style={{ minWidth: 0 }}>
            <span className="mono t3">{L("Brief", "موجز")} {briefNumber(brief.brief_no)}</span>
            <span className="t3">·</span>
            <b className="s14 ellip deck-title" dir="auto" title={brief.title}>{brief.title}</b>
            <span className={`chip ${lifecycle === "decided" ? "c-dec" : lifecycle === "reviewed" ? "c-rev" : "c-line"}`}>
              {lifecycle === "decided" ? L("Decided", "تم القرار") : lifecycle === "reviewed" ? L("Reviewed", "تمت المراجعة") : L("Not yet reviewed", "لم تُراجع بعد")}
            </span>
          </div>
          <div className="row g8">
            <span className="row g8 mono t3 deck-counter" aria-live="polite">
              <bdi className="tb" dir="ltr">{`${String(active + 1).padStart(2, "0")} / ${String(slides.length).padStart(2, "0")}`}</bdi>
              <span className="deck-counter-name">{names[active]}</span>
            </span>
            <button type="button" className={`btn sm ${chat ? "ink" : "ghost"}`} onClick={() => setChat((c) => (c ? null : { nonce: Date.now() }))} title={`${modKey()}J`}>
              <Icon name="sparkle" size={14} /> {L("Ask Edrak", "اسأل إدراك")}
            </button>
            <div style={{ position: "relative" }}>
              <button type="button" className="btn ghost sm" onClick={() => setMenu((v) => !v)} aria-expanded={menu}><Icon name="menu" size={14} /> {L("More", "المزيد")}</button>
              {menu && (
                <div className="popover deck-menu" onMouseLeave={() => setMenu(false)}>
                  {[["pdf", "PDF"], ["docx", "Word"], ["markdown", "Markdown"]].map(([f, label]) => (
                    <a key={f} className="list-row" href={edrak.exportUrl(brief.brief_id, f)} onClick={() => setMenu(false)}><Icon name="upload" size={14} /> {L("Download as", "تنزيل كـ")} {label}</a>
                  ))}
                  <button type="button" className="list-row" onClick={() => navigate(`/evidence/${brief.brief_id}`)}><Icon name="link" size={14} /> {L("All sources", "كل المصادر")}</button>
                  {lifecycle === "ready" && <button type="button" className="list-row" onClick={() => void edrak.reviewBrief(brief.brief_id, "accepted_as_reviewed").then(() => { setLifecycle("reviewed"); setMenu(false); })}><Icon name="check" size={14} /> {L("Accept as reviewed", "اعتمد كمراجَع")}</button>}
                  {lifecycle === "decided" && <button type="button" className="list-row" onClick={() => void edrak.undoDecision(brief.brief_id).then(() => { setMenu(false); reload(); })}><Icon name="refresh" size={14} /> {L("Undo decision", "تراجع عن القرار")}</button>}
                </div>
              )}
            </div>
            <button type="button" className="btn sm" onClick={() => void toggle()} title="F">
              <Icon name={fullscreen ? "x" : "external"} size={14} /> {fullscreen ? L("Exit full screen", "إنهاء ملء الشاشة") : L("Full screen", "ملء الشاشة")}
            </button>
          </div>
        </div>

        <div className="deck-main">
          <div className="bframe">
            <div ref={scroller} className="bscroll" tabIndex={0} onScroll={onScroll} aria-label={L("Brief slides", "شرائح الموجز")}>
              {slides.map((s, i) => (
                <section key={s.id} className="bcard" aria-label={names[i]}>
                  <div className="bc-body"><Slide slide={s} /></div>
                </section>
              ))}
            </div>

            <div className="brail" role="group" aria-label={L("Slides", "الشرائح")}>
              <button type="button" className="bnav" aria-label={L("Previous slide", "الشريحة السابقة")} disabled={active === 0} onClick={() => goToIndex(active - 1)}><Icon name="chevronUp" size={14} /></button>
              {slides.map((s, i) => {
                const on = i === active;
                return (
                  <button key={s.id} type="button" className="bdot" aria-label={L(`Go to ${i + 1}: ${names[i]}`, `اذهب إلى ${i + 1}: ${names[i]}`)} aria-current={on} onClick={() => goToIndex(i)}>
                    <i style={{ width: 8, height: on ? 24 : 8, background: on ? "#4A495E" : "#CFCCC1" }} />
                    <span className="btip">
                      <span className="mono tb">{String(i + 1).padStart(2, "0")}</span>
                      <span className="b7" style={{ fontSize: 14, lineHeight: 1.25 }}>{names[i]}</span>
                      <span className="s12 t2 lh14">{L(DESC[s.id][2], DESC[s.id][3])}</span>
                    </span>
                  </button>
                );
              })}
              <button type="button" className="bnav" aria-label={L("Next slide", "الشريحة التالية")} disabled={active === slides.length - 1} onClick={() => goToIndex(active + 1)}><Icon name="chevronDown" size={14} /></button>
            </div>

            <div className="bhint" style={{ opacity: scrolled ? 0 : 1 }} aria-hidden="true">
              <span className="mono">{L("Scroll, use the arrows or the dots", "مرّر أو استخدم الأسهم أو النقاط")}</span>
              <Icon name="chevronDown" size={14} />
            </div>
          </div>

          {chat && <ChatPanel key={`${chat.cardKey ?? ""}:${chat.signalId ?? ""}`} variant="docked" scope={{ ...chat, briefId: brief.brief_id }} onClose={() => setChat(null)} onEvidence={api_.openEvidence} />}
        </div>

        {openCard && <CardDrawer brief={brief} card={openCard} onClose={() => setCard(null)} onChanged={reload} />}
        {evidence && <EvidenceDrawer item={evidence} onClose={() => setEvidenceNo(null)} />}
        {signal && <SignalDrawer signal={signal} onClose={() => setSignalId(null)} />}
      </div>
    </DeckContext.Provider>
  );
}
