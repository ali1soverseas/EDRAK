import { useMemo, useState } from "react";
import { useL } from "../../../lib/useL";
import type { SignalView, SignalsSlideData } from "../../../types/brief3";
import { AskButton, Clickable, EvChips } from "../InsightCard";
import { LENS, useDeck } from "../context";
import { SlideHead } from "./SlideHead";

const TYPE: Record<string, [string, string]> = {
  momentum: ["Momentum", "زخم"], timing: ["Timing", "توقيت"], gap: ["Gap", "فجوة"], convergence: ["Lenses agree", "اتفاق العدسات"],
  dependency: ["Dependency", "اعتماد"], conflict: ["Sources disagree", "تعارض المصادر"],
};

function SignalCard({ s }: { s: SignalView }) {
  const L = useL();
  const { openSignal, ask } = useDeck();
  const t = TYPE[s.type ?? ""];
  return (
    <Clickable onOpen={() => openSignal(s.signal_id)} label={s.title} className={`scard ${s.type === "conflict" ? "conflict" : ""}`}>
      {(t || (s.urgency && ["immediate", "high"].includes(s.urgency))) && (
        <header className="row g6 wrap">
          {t && <span className={`chip ${s.type === "conflict" ? "c-bad" : "c-line"}`}>{L(t[0], t[1])}</span>}
          {s.urgency && ["immediate", "high"].includes(s.urgency) && <span className="chip c-ins">{L("Time-sensitive", "حساس للوقت")}</span>}
        </header>
      )}
      <b className="scard-title" dir="auto">{s.title}</b>
      <p className="s13 t2 lh14 clamp3" dir="auto">{s.type === "conflict" ? s.signal : (s.why_it_matters || s.signal)}</p>
      {s.type === "conflict" && s.sides && <div className="sides">{s.sides.map((x) => <span key={x.uid} className="side"><b>{x.value}</b>{x.ref ? <EvChips refs={[x.ref]} /> : null}</span>)}</div>}
      <footer className="row g6 wrap scard-foot">
        {s.domains.map((d) => LENS[d] && <span key={d} className={`lens-tag ${LENS[d].css}`}>{L(LENS[d].en, LENS[d].ar)}</span>)}
        <span className="grow" />
        <EvChips refs={s.refs} max={4} />
        <AskButton label={L("Ask about this signal", "اسأل عن هذه الإشارة")} onAsk={() => ask({ signalId: s.signal_id, title: s.title })} />
      </footer>
    </Clickable>
  );
}

/** 03 Signals: what the lenses say together. A lens filter narrows the cards; order follows the use case. */
export function SignalsSlide({ slide }: { slide: SignalsSlideData }) {
  const L = useL();
  const { brief } = useDeck();
  const [lens, setLens] = useState<string>("all");
  const all = useMemo(() => slide.items.map((id) => brief.signals_view.find((s) => s.signal_id === id)).filter(Boolean) as SignalView[], [slide.items, brief.signals_view]);
  const shown = lens === "all" ? all : all.filter((s) => s.domains.includes(lens));
  return (
    <div className="slide">
      <SlideHead no={3} name={L("Signals", "الإشارات")} title={L("What the lenses show together", "ما تُظهره العدسات معًا")}
        aside={slide.lenses.length > 1 ? (
          <div className="seg" role="group" aria-label={L("Filter by lens", "تصفية حسب العدسة")}>
            <button type="button" className={lens === "all" ? "on" : ""} aria-pressed={lens === "all"} onClick={() => setLens("all")}>{L("All", "الكل")} <span className="t3">{all.length}</span></button>
            {slide.lenses.map((l) => (
              <button key={l.lens} type="button" className={lens === l.lens ? "on" : ""} aria-pressed={lens === l.lens} onClick={() => setLens(l.lens)}>
                <span className={`lens-dot ${LENS[l.lens]?.css ?? ""}`} />{L(LENS[l.lens]?.en ?? l.label, LENS[l.lens]?.ar ?? l.label)}
              </button>
            ))}
          </div>
        ) : undefined} />
      <div className="slide-body">
        {shown.length === 0 ? (
          <div className="empty">{all.length === 0 ? L("No cross-domain signal passed verification for this question.", "لم تجتز أي إشارة التحقق لهذا السؤال.") : L("No signal involves this lens.", "لا إشارة تتعلق بهذه العدسة.")}</div>
        ) : (
          <div className="signal-grid">{shown.map((s) => <SignalCard key={s.signal_id} s={s} />)}</div>
        )}
      </div>
    </div>
  );
}
