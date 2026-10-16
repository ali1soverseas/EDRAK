import { useEffect, useRef, useState } from "react";
import { useL } from "../../../lib/useL";
import { edrak } from "../../../services/edrak";
import type { BriefOption, DecisionsSlideData } from "../../../types/brief3";
import { Icon } from "../../ui/Icon";
import { AskButton, EvChips } from "../InsightCard";
import { useDeck } from "../context";
import { PlanPanel } from "../PlanPanel";
import { SlideHead } from "./SlideHead";

function OptionCard({ o, chosen, onChoose }: { o: BriefOption; chosen: boolean; onChoose: () => void }) {
  const L = useL();
  const { ask } = useDeck();
  return (
    <article className={`card col option opt5 ${chosen ? "chosen" : ""}`}>
      <div className="row g10">
        <span className="option-key">{o.key}</span>
        <span className="b6 grow" dir="auto" style={{ fontSize: 17 }}>{o.title}</span>
        <AskButton label={L("Ask about this option", "اسأل عن هذا الخيار")} onAsk={() => ask({ title: `${L("Option", "الخيار")} ${o.key}: ${o.title}` })} />
      </div>
      <p className="t2 lh14 opt-thesis" dir="auto">{o.thesis || o.description}</p>
      {o.trade_offs?.length > 0 && (
        <div className="opt-sec"><span className="t3 s13">{L("Trade-offs", "المفاضلات")}</span>
          <ul className="plain-list">{o.trade_offs.slice(0, 3).map((x) => <li key={x} className="s13 lh14" dir="auto">{x}</li>)}</ul></div>
      )}
      <div className="opt-two">
        <div className="opt-sec"><span className="t3 s13">{L("Opportunities", "الفرص")}</span>
          {(o.opportunities ?? []).slice(0, 3).map((x) => <span key={x.text} className="s13 lh14 opp" dir="auto"><Icon name="plus" size={11} /> {x.text}</span>)}</div>
        <div className="opt-sec"><span className="t3 s13">{L("Risks", "المخاطر")}</span>
          {(o.risks ?? []).slice(0, 3).map((x) => <span key={x.text} className="s13 lh14 risk" dir="auto"><Icon name="warning" size={11} /> {x.text}</span>)}</div>
      </div>
      {o.would_change_if && <div className="opt-sec"><span className="t3 s13">{L("Would change if", "يتغير إذا")}</span><span className="s13 lh14" dir="auto">{o.would_change_if}</span></div>}
      <div className="row g8 opt-foot">
        <EvChips refs={o.evidence_refs ?? []} max={4} />
        <span className="grow" />
        <button type="button" className={`btn sm ${chosen ? "ink" : ""}`} onClick={onChoose} aria-pressed={chosen}>
          {chosen ? <><Icon name="check" size={13} /> {L("Chosen", "مختار")}</> : L("Choose this option", "اختر هذا الخيار")}
        </button>
      </div>
    </article>
  );
}

/** 04 Decisions: distinct options, not ranked. Choosing one drafts its action plan right here. */
export function DecisionsSlide({ slide }: { slide: DecisionsSlideData }) {
  const L = useL();
  const { brief, reload } = useDeck();
  const options = slide.options.map((k) => brief.options.find((o) => o.key === k)).filter(Boolean) as BriefOption[];
  const [chosen, setChosen] = useState<string | null>(null);
  const planRef = useRef<HTMLDivElement>(null);

  // A plan that already exists (or a recorded decision) decides which option is shown as chosen.
  useEffect(() => {
    void edrak.actionPlans(brief.brief_id).then((all) => {
      const live = all.find((p) => p.status === "approved") ?? all.find((p) => p.status !== "superseded");
      if (live) setChosen((c) => c ?? live.option_key);
    }).catch(() => undefined);
  }, [brief.brief_id]);

  const choose = (key: string) => {
    setChosen(key);
    window.setTimeout(() => planRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
  };
  const option = options.find((o) => o.key === chosen);

  return (
    <div className="slide">
      <SlideHead no={4} name={L("Decisions", "القرارات")}
        title={options.length ? L(`${options.length} ways forward, not ranked`, `${options.length} طرق للمضي، غير مرتبة`) : L("No options were drafted", "لم تُكتب خيارات")}
        sub={slide.framing}
        aside={<span className="chip c-line"><Icon name="lock" size={12} /> {brief.lifecycle === "decided" ? L("Decision recorded", "سُجّل القرار") : L("The decision stays with you.", "القرار لك.")}</span>} />
      <div className="slide-body">
        {options.length === 0 ? (
          <div className="empty">{L("The analysis did not produce distinct options. The findings and gaps are the basis for a decision.", "لم ينتج التحليل خيارات مميزة. النتائج والفجوات هي أساس القرار.")}</div>
        ) : (
          <div className="option-row">{options.map((o) => <OptionCard key={o.key} o={o} chosen={chosen === o.key} onChoose={() => choose(o.key)} />)}</div>
        )}
        <div ref={planRef}>
          {option ? (
            <PlanPanel key={option.key} option={option} orderedOnly={!slide.priorities_known} onDecisionChanged={reload} />
          ) : options.length > 0 && (
            <p className="s13 t3 choose-hint"><Icon name="info" size={13} /> {L("Choose an option to draft its action plan: ordered recommendations with an owner and a validation step, its risks and its opportunities. You review, edit and approve it.", "اختر خيارًا لكتابة خطة عمله: توصيات مرتبة بمسؤول وخطوة تحقق، ومخاطره وفرصه. تراجعها وتعدّلها وتعتمدها.")}</p>
          )}
        </div>
      </div>
    </div>
  );
}
