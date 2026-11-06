import { useEffect, useRef, useState } from "react";
import { useL } from "../../../lib/useL";
import { edrak } from "../../../services/edrak";
import type { BriefOption, DecisionsSlideData } from "../../../types/brief3";
import { Icon } from "../../ui/Icon";
import { AskButton, EvChips } from "../InsightCard";
import { useDeck } from "../context";
import { PlanPanel } from "../PlanPanel";
import { SlideHead } from "./SlideHead";

const CRITERION: Record<string, [string, string]> = {
  speed: ["implementation time", "وقت التنفيذ"],
  cost: ["total cost", "التكلفة الإجمالية"],
  control: ["in-house control", "التحكم الداخلي"],
  quality: ["quality", "الجودة"],
  risk: ["reliability and risk", "الموثوقية والمخاطر"],
  scale: ["scale", "الحجم والنطاق"],
  differentiation: ["differentiation", "التميّز"],
  reversibility: ["reversibility", "إمكانية التراجع"],
  compliance: ["compliance", "الامتثال"],
  revenue: ["revenue", "الإيراد"],
  customer: ["customers", "العملاء"],
  partnership: ["partnership", "الشراكة"],
  talent: ["talent", "الكفاءات"],
};

const RELATION: Record<string, [string, string]> = {
  time_to_value: ["time to value", "الوقت حتى العائد"],
  quality_at_speed: ["quality at speed", "الجودة مع السرعة"],
  timing_risk: ["timing risk", "مخاطر التوقيت"],
  in_house_timeline: ["in-house timeline", "الجدول الزمني للتنفيذ الداخلي"],
  cost_of_failure: ["cost of failure", "تكلفة الفشل"],
  cost_of_ownership: ["cost of ownership", "تكلفة الامتلاك"],
  spend_and_outcome: ["where spend changes the outcome", "أين يغيّر الإنفاق النتيجة"],
  unit_economics: ["unit economics as volume grows", "اقتصاديات الوحدة مع نمو الحجم"],
  who_carries_failure: ["who carries the failure", "من يتحمل الفشل"],
  in_house_quality: ["in-house quality", "جودة التنفيذ الداخلي"],
  reliable_outcome: ["how reliable the customer outcome is", "موثوقية نتيجة العميل"],
  capacity_risk: ["capacity and concentration risk", "مخاطر الطاقة والتركّز"],
  switch_for: ["what a customer would switch for", "ما الذي يدفع العميل للتحوّل"],
  undo_downside: ["how much of the downside can be undone", "ما يمكن التراجع عنه من الخسارة"],
  reversible_cost: ["the cost of a reversible path versus a commitment", "تكلفة المسار القابل للتراجع مقابل الالتزام"],
  regulatory_downside: ["regulatory downside", "المخاطر التنظيمية"],
  compliant_owner: ["who must own the compliant path", "من يملك مسار الامتثال"],
  margin: ["margin", "الهامش"],
  revenue_speed: ["how soon the revenue arrives", "متى يصل الإيراد"],
  revenue_scale: ["how revenue changes as volume grows", "كيف يتغير الإيراد مع نمو الحجم"],
  price_hold: ["whether a distinct offer can hold its price", "قدرة العرض المتميّز على الحفاظ على سعره"],
  keep_customers: ["whether the experience keeps customers", "هل التجربة تحافظ على العملاء"],
  lose_customers: ["the risk of losing customers", "مخاطر خسارة العملاء"],
  customers_wait: ["whether customers will wait", "هل ينتظر العملاء"],
  switch_and_stay: ["what would make a customer switch and stay", "ما الذي يجعل العميل يتحوّل ويبقى"],
  partner_control: ["what ownership is given up to a partner", "ما يُتنازل عنه من الملكية للشريك"],
  partner_speed: ["whether a partner reaches the market sooner", "هل يصل الشريك إلى السوق أسرع"],
  partner_cost: ["the cost of the partner path", "تكلفة مسار الشراكة"],
  partner_compliance: ["whether the partner can carry the regulatory burden", "هل يستطيع الشريك تحمّل العبء التنظيمي"],
  talent_control: ["whether the people exist to own it", "هل تتوفر الكفاءات لامتلاكه"],
  talent_speed: ["whether hiring can still hit the date", "هل يمكن للتوظيف أن يلحق بالموعد"],
  talent_quality: ["whether the team can hold the quality bar", "هل يستطيع الفريق الحفاظ على الجودة"],
  talent_scale: ["whether the team can support the volume", "هل يستطيع الفريق تحمّل الحجم"],
};

function EmphasisNote({ personalization }: { personalization: NonNullable<DecisionsSlideData["personalization"]> }) {
  const L = useL();
  const phrase = (table: Record<string, [string, string]>, id?: string) => (id && table[id] ? L(table[id][0], table[id][1]) : "");
  const label = (id?: string) => phrase(CRITERION, id);
  const focus = personalization.relation ? phrase(RELATION, personalization.relation) : label(personalization.criterion);
  const titles = (personalization.decisions ?? []).map((d) => d.title).filter(Boolean).join(", ");
  const text = personalization.status === "applied"
    ? L(
        `This analysis looks more closely at ${focus}${titles ? `, based on earlier decisions: ${titles}` : ""}.`,
        `يتعمق هذا التحليل في ${focus}${titles ? ` بناءً على قرارات سابقة: ${titles}` : ""}.`,
      )
    : L(
        `Earlier decisions weighed ${label(personalization.criteria?.[0])} and ${label(personalization.criteria?.[1])} differently, so each option compares both.`,
        `وازنت القرارات السابقة بين ${label(personalization.criteria?.[0])} و${label(personalization.criteria?.[1])} بشكل مختلف، لذا يقارن كل خيار بينهما.`,
      );
  return <p className="s13 t2 choose-hint"><Icon name="info" size={13} /> {text}</p>;
}

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
        {slide.personalization && <EmphasisNote personalization={slide.personalization} />}
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
