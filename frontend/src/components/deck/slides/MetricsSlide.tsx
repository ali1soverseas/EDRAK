import { useL } from "../../../lib/useL";
import type { BriefCard, ComparisonCell, MetricsSlideData } from "../../../types/brief3";
import { Icon } from "../../ui/Icon";
import { EvChips, InsightCard } from "../InsightCard";
import { useDeck } from "../context";
import { SlideHead } from "./SlideHead";

function Cell({ cells, empty, showEntity }: { cells: ComparisonCell[]; empty: string; showEntity?: boolean }) {
  if (!cells.length) return <span className="s13 t3">{empty}</span>;
  return (
    <span className="stack gap4">
      {cells.map((c) => {
        const hasDistinctLabel = c.label && (!c.entity || c.label.toLowerCase() !== c.entity.toLowerCase());
        return (
          <span key={c.uid} className="row g6 wrap" style={{ alignItems: "baseline" }}>
            <b className="s14">{c.value}</b>
            {showEntity && c.entity && (
              <span className="s12" style={{ fontWeight: 600, color: "var(--t1, inherit)" }}>
                · {c.entity}
              </span>
            )}
            {hasDistinctLabel && <span className="s12 t3">({c.label})</span>}
            {c.period && <span className="s12 t3">· {c.period}</span>}
            {c.ref ? <EvChips refs={[c.ref]} /> : null}
          </span>
        );
      })}
    </span>
  );
}

/** 02 Business metrics: the metrics agent's typed figures. Our numbers against theirs, then every chart card. */
export function MetricsSlide({ slide }: { slide: MetricsSlideData }) {
  const L = useL();
  const { brief, openCard, goTo, ask } = useDeck();
  const cards = slide.cards.map((k) => brief.cards[k]).filter(Boolean) as BriefCard[];
  const gaps = slide.gap_cards.map((k) => brief.cards[k]).filter(Boolean) as BriefCard[];
  const kpis = slide.kpis.map((k) => brief.kpis.find((x) => x.card_key === k)).filter(Boolean);
  const cardFor = (metricId: string) => cards.find((c) => c.metric_id === metricId)?.card_key;

  return (
    <div className="slide">
      <SlideHead no={2} name={L("Business metrics", "مقاييس الأعمال")} title={slide.title} sub={slide.subtitle}
        aside={<button type="button" className="btn ghost sm" onClick={() => ask({ title: slide.title })}><Icon name="sparkle" size={13} /> {L("Ask about these figures", "اسأل عن هذه الأرقام")}</button>} />
      <div className="slide-body">
        {slide.collapsed ? (
          <div className="collapsed-note hatch">
            <Icon name="info" size={18} />
            <div className="stack gap4 grow">
              <b className="s14">{slide.collapsed_reason}</b>
              <span className="s13 t2">{L("Every required figure is listed with the reason it is missing, and you can re-run or provide the data.", "كل رقم مطلوب مذكور مع سبب غيابه، ويمكنك إعادة التشغيل أو تزويد البيانات.")}</span>
            </div>
            <button type="button" className="btn sm" onClick={() => goTo("evidence_gaps")}>{L("See the gaps", "اعرض الفجوات")} <Icon name="chevron" size={12} flip /></button>
          </div>
        ) : (
          <div className="stack gap16">
            {kpis.length > 0 && (
              <div className="kpi-strip" role="list">
                {kpis.map((k) => k && (
                  <button key={k.card_key} type="button" role="listitem" className="kpi-tile" onClick={() => openCard(k.card_key)}>
                    <span className="eyebrow ellip">{L(k.label.en, k.label.ar ?? k.label.en)}</span>
                    <b className="kpi-value">{k.value}</b>
                    <span className="row g6 s12 t3 wrap">
                      {k.entity && <span className="ellip">{k.entity}</span>}
                      {k.period && <span>· {k.period}</span>}
                      {k.status === "thin" && <span className="chip c-ins" title={L("Only one independent source reports this figure.", "مصدر مستقل واحد فقط يذكر هذا الرقم.")}>{L("One source", "مصدر واحد")}</span>}
                      {k.comparator && <span className={`cmp ${k.comparator.direction > 0 ? "up" : "down"}`}>{k.comparator.direction > 0 ? "▲" : "▼"} {k.comparator.value} {k.comparator.label}</span>}
                    </span>
                    {k.reads_as && <span className="s12 t2 lh14 kpi-reads" dir="auto">{k.reads_as}</span>}
                  </button>
                ))}
              </div>
            )}

            {slide.comparison.length > 0 && (() => {
              // Market figures (size, growth, investment, capacity) describe places, not rivals: they get their own table.
              const MARKET_METRIC_IDS = new Set(["market_size", "market_cagr", "market_concentration", "adoption_rate", "segment_growth", "regulation_events"]);
              const isMarket = (r: (typeof slide.comparison)[number]) => (r.lens ? r.lens === "market_intelligence" : MARKET_METRIC_IDS.has(r.metric_id));
              const marketRows = slide.comparison.filter(isMarket);
              const competitiveRows = slide.comparison.filter((r) => !isMarket(r));
              const rivals = [...new Set(competitiveRows.flatMap((r) => r.theirs.map((c) => c.entity)).filter(Boolean))];

              return (
                <div className="stack gap16">
                  {marketRows.length > 0 && (
                    <section className="stack gap8">
                      <div className="row items-center g8">
                        <span className="eyebrow" style={{ color: "var(--accent, #6366f1)", fontWeight: 700 }}>
                          {L("Market Dynamics · Target Regions", "ديناميكيات السوق · المناطق المستهدفة")}
                        </span>
                        <span className="s12 t3">
                          · {L("Macro size, growth and adoption across targeted markets", "الحجم والنمو والتبني في الأسواق المستهدفة")}
                        </span>
                      </div>
                      <div className="vs-table" aria-label={L("Market dynamics across target regions", "ديناميكيات السوق في المناطق المستهدفة")}>
                        <div className="vs-row vs-head">
                          <span className="eyebrow">{L("Metric", "المؤشر")}</span>
                          <span className="eyebrow">
                            {slide.company ? `${slide.company} (${L("Our presence", "حضورنا")})` : L("Our presence", "حضورنا")}
                          </span>
                          <span className="eyebrow">{L("Target Region / Market Data", "بيانات المنطقة / السوق المستهدف")}</span>
                        </div>
                        {marketRows.map((r) => {
                          const key = cardFor(r.metric_id);
                          return (
                            <div key={r.metric_id} className={`vs-row ${key ? "tap-lite" : ""}`} role={key ? "button" : undefined} tabIndex={key ? 0 : undefined}
                              onClick={() => key && openCard(key)} onKeyDown={(e) => key && e.key === "Enter" && openCard(key)}>
                              <span className="stack gap4">
                                <b className="s14">{L(r.name.en, r.name.ar ?? r.name.en)}</b>
                                {r.reads_as && <span className="s12 t3 lh14" dir="auto">{r.reads_as}</span>}
                                {r.note && <span className="s12 warn-text">{r.note}</span>}
                              </span>
                              <Cell cells={r.ours} empty="—" />
                              <Cell cells={r.theirs} empty="—" showEntity />
                            </div>
                          );
                        })}
                      </div>
                    </section>
                  )}

                  {competitiveRows.length > 0 && (
                    <section className="stack gap8">
                      <div className="row items-center g8">
                        <span className="eyebrow" style={{ color: "var(--accent, #6366f1)", fontWeight: 700 }}>
                          {L("Competitive Benchmark · Rivals", "المقارنة التنافسية · المنافسون")}
                        </span>
                        <span className="s12 t3">
                          · {L("Our offering against direct competitor products", "عرضنا مقارنة بمنتجات المنافسين المباشرين")}
                        </span>
                      </div>
                      <div className="vs-table" aria-label={L("Competitive benchmark", "المقارنة التنافسية")}>
                        <div className="vs-row vs-head">
                          <span className="eyebrow">{L("Offering Feature / Pricing", "الميزة / التسعير")}</span>
                          <span className="eyebrow">{slide.company || L("Us", "نحن")}</span>
                          <span className="eyebrow">{rivals.length ? `${L("Competitors", "المنافسون")} (${rivals.slice(0, 3).join(", ")}${rivals.length > 3 ? ", …" : ""})` : L("Competitors", "المنافسون")}</span>
                        </div>
                        {competitiveRows.map((r) => {
                          const key = cardFor(r.metric_id);
                          return (
                            <div key={r.metric_id} className={`vs-row ${key ? "tap-lite" : ""}`} role={key ? "button" : undefined} tabIndex={key ? 0 : undefined}
                              onClick={() => key && openCard(key)} onKeyDown={(e) => key && e.key === "Enter" && openCard(key)}>
                              <span className="stack gap4">
                                <b className="s14">{L(r.name.en, r.name.ar ?? r.name.en)}</b>
                                {r.reads_as && <span className="s12 t3 lh14" dir="auto">{r.reads_as}</span>}
                                {r.note && <span className="s12 warn-text">{r.note}</span>}
                              </span>
                              <Cell cells={r.ours} empty={L("Not found", "غير موجود")} />
                              <Cell cells={r.theirs} empty="—" showEntity />
                            </div>
                          );
                        })}
                      </div>
                    </section>
                  )}
                </div>
              );
            })()}

            <div className="card-grid">
              {cards.map((c) => <InsightCard key={c.card_key} card={c} />)}
            </div>
          </div>
        )}
        {gaps.length > 0 && (
          <section className="stack gap8" style={{ marginTop: 18 }}>
            <span className="eyebrow">{L("Required figures still missing", "أرقام مطلوبة ما زالت مفقودة")}</span>
            <div className="card-grid">{gaps.map((c) => <InsightCard key={c.card_key} card={c} compact />)}</div>
          </section>
        )}
      </div>
    </div>
  );
}
