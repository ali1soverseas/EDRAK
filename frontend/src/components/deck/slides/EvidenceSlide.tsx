import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useL } from "../../../lib/useL";
import { edrak } from "../../../services/edrak";
import type { EvidenceSlideData, SignalView } from "../../../types/brief3";
import { Icon } from "../../ui/Icon";
import { Clickable, EvChips } from "../InsightCard";
import { LENS, useDeck } from "../context";
import { SlideHead } from "./SlideHead";

const REASON: Record<string, [string, string]> = {
  not_found: ["Searched, not found", "بُحث عنه ولم يوجد"], not_publicly_disclosed: ["Not publicly disclosed", "غير مُعلن"],
  only_third_party_source: ["Only third-party sources", "مصادر خارجية فقط"], below_min_sample: ["Too little data", "بيانات قليلة جدًا"],
  conflict: ["Sources disagree", "المصادر متعارضة"], unverified: ["Could not be matched to its source", "تعذرت مطابقته بمصدره"],
};

/** 05 Evidence and gaps: how complete the picture is, where it comes from, where sources disagree, what is missing. */
export function EvidenceSlide({ slide }: { slide: EvidenceSlideData }) {
  const L = useL();
  const navigate = useNavigate();
  const { brief, openCard, openSignal, ask } = useDeck();
  const [followUp, setFollowUp] = useState<string | null>(null);
  const mix = slide.source_mix;
  const conflicts = slide.conflicts.map((id) => brief.signals_view.find((s) => s.signal_id === id)).filter(Boolean) as SignalView[];
  const figureGaps = slide.gaps.filter((g) => g.kind === "figure");
  const researchGaps = slide.gaps.filter((g) => g.kind === "research");

  const rerun = async () => {
    try {
      const r = await edrak.reviewBrief(brief.brief_id, "requested_investigation");
      if (r.follow_up_analysis_id) navigate(`/analyses/new?draft=${r.follow_up_analysis_id}`);
      else setFollowUp(L("A follow-up analysis was requested.", "طُلب تحليل متابعة."));
    } catch (e) { setFollowUp((e as Error).message); }
  };

  return (
    <div className="slide">
      <SlideHead no={5} name={L("Evidence and gaps", "الأدلة والفجوات")} title={L("How much to trust this, and what is missing", "إلى أي حد نثق بهذا، وما الناقص")}
        aside={<button type="button" className="btn ghost sm" onClick={() => navigate(`/evidence/${brief.brief_id}`)}><Icon name="link" size={13} /> {L("All sources", "كل المصادر")}</button>} />
      <div className="slide-body">
        <div className="ev-top">
          <section className="ev-panel">
            <span className="eyebrow">{L("Coverage by lens", "التغطية حسب العدسة")}</span>
            {slide.by_lens.map((l) => {
              // Required figures when the template asks for some; otherwise the share of findings that passed verification.
              const pct = l.required ? Math.round((l.met / l.required) * 100) : l.findings ? Math.round((l.verified / l.findings) * 100) : 0;
              return (
                <div key={l.lens} className={`lens-cov ${l.collapsed ? "collapsed" : ""}`}>
                  <span className="row g6"><span className={`lens-dot ${LENS[l.lens]?.css ?? ""}`} /><b className="s13">{L(LENS[l.lens]?.en ?? l.label, LENS[l.lens]?.ar ?? l.label)}</b></span>
                  <span className="bar"><i style={{ width: `${pct}%` }} /></span>
                  <span className="s12 t2 mono" title={l.required ? L("Required figures verified", "أرقام مطلوبة موثقة") : L("Findings verified", "نتائج موثقة")}>{l.required ? `${l.met}/${l.required}` : `${l.verified}/${l.findings}`}</span>
                  <span className="s12 t3">{L(`${l.sources} sources · ${l.verified}/${l.findings} verified`, `${l.sources} مصدر · ${l.verified}/${l.findings} موثّق`)}{l.collapsed ? L(" · too little evidence for its own section", " · أدلة أقل من أن تُعرض كقسم") : ""}</span>
                </div>
              );
            })}
          </section>
          <section className="ev-panel">
            <span className="eyebrow">{L("Where the evidence comes from", "من أين تأتي الأدلة")}</span>
            <div className="mix-bar" role="img" aria-label={L(`${mix.internal} internal, ${mix.external} external`, `${mix.internal} داخلي، ${mix.external} خارجي`)}>
              <i className="int" style={{ flex: mix.internal || 0 }} /><i className="ext" style={{ flex: mix.external || 0 }} />
            </div>
            <div className="row g12 s12 wrap"><span className="row g4"><i className="dot int-dot" />{L("Internal", "داخلي")} <b>{mix.internal}</b></span><span className="row g4"><i className="dot ext-dot" />{L("External", "خارجي")} <b>{mix.external}</b></span></div>
            <span className="eyebrow" style={{ marginTop: 10 }}>{L("Reliability", "الموثوقية")}</span>
            {(["3", "2", "1"] as const).map((k) => (
              <div key={k} className="rel-row"><span className="rel-dots">{[1, 2, 3].map((i) => <i key={i} className={i <= +k ? "on" : ""} />)}</span>
                <span className="bar"><i style={{ width: `${mix.total ? ((mix.by_reliability[k] ?? 0) / mix.total) * 100 : 0}%` }} /></span><b className="s12 mono">{mix.by_reliability[k] ?? 0}</b></div>
            ))}
          </section>
        </div>

        {conflicts.length > 0 && (
          <section className="stack gap8 ev-sec">
            <span className="eyebrow">{L("Where sources disagree", "حيث تتعارض المصادر")}</span>
            <div className="signal-grid">
              {conflicts.map((c) => (
                <Clickable key={c.signal_id} className="scard conflict" label={c.title} onOpen={() => openSignal(c.signal_id)}>
                  <b className="scard-title" dir="auto">{c.title}</b>
                  <div className="sides">{(c.sides ?? []).map((s) => <span key={s.uid} className="side"><b>{s.value}</b>{s.ref ? <EvChips refs={[s.ref]} /> : null}</span>)}</div>
                  <span className="s12 t3">{L("Shown as a range. No single value is used.", "تُعرض كنطاق. لا تُستخدم قيمة واحدة.")}</span>
                </Clickable>
              ))}
            </div>
          </section>
        )}

        <section className="stack gap8 ev-sec">
          <div className="row g8"><span className="eyebrow grow">{L("Open gaps", "الفجوات المفتوحة")} <span className="t3">· {slide.gaps.length}</span></span>
            <button type="button" className="btn sm" onClick={() => void rerun()}><Icon name="refresh" size={13} /> {L("Re-run for the gaps", "أعد التشغيل للفجوات")}</button>
            <button type="button" className="btn ghost sm" onClick={() => navigate("/sources")}><Icon name="upload" size={13} /> {L("Provide data", "زوّد بيانات")}</button></div>
          {followUp && <span className="s12 t2">{followUp}</span>}
          {slide.gaps.length === 0 && <div className="empty">{L("No open gap.", "لا فجوات مفتوحة.")}</div>}
          <div className="gap-list">
            {figureGaps.map((g) => (
              <div key={`${g.card_key}-${g.title}`} className={`hatch gap-row ${g.card_key ? "tap-lite" : ""}`} role={g.card_key ? "button" : undefined} tabIndex={g.card_key ? 0 : undefined}
                onClick={() => g.card_key && brief.cards[g.card_key] && openCard(g.card_key)} onKeyDown={(e) => e.key === "Enter" && g.card_key && brief.cards[g.card_key] && openCard(g.card_key)}>
                <span className="gap-icon"><Icon name="warning" size={16} /></span>
                <div className="grow" style={{ minWidth: 0 }}>
                  <div className="b6 s14 lh12" dir="auto">{g.title}{g.blocking && <span className="chip c-bad" style={{ marginInlineStart: 8 }}>{L("Required", "مطلوب")}</span>}</div>
                  <div className="s13 t2" style={{ marginTop: 2 }}>{g.reason ? L(REASON[g.reason]?.[0] ?? g.reason, REASON[g.reason]?.[1] ?? g.reason) : g.explanation}</div>
                </div>
                <span className="chip c-line">{g.action === "provide_data" ? L("Provide data", "زوّد بيانات") : L("Re-run", "أعد التشغيل")}</span>
              </div>
            ))}
            {researchGaps.map((g) => (
              <div key={g.title} className="hatch gap-row">
                <span className="gap-icon"><Icon name="info" size={16} /></span>
                <div className="grow" style={{ minWidth: 0 }}>
                  <div className="b6 s14 lh12" dir="auto">{g.title}</div>
                  <div className="s13 t2" style={{ marginTop: 2 }}>{g.explanation}</div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="challenge-cta">
          <Icon name="shield" size={18} />
          <div className="grow"><b className="s14">{L("Think something here is wrong?", "تظن أن شيئًا هنا خاطئ؟")}</b>
            <p className="s13 t2">{L("Open any card and use Challenge this. What you add goes back through extraction and verification, and the card gets a new version; the old one is kept.", "افتح أي بطاقة واستخدم «اعترض على هذا». ما تضيفه يمر بالاستخراج والتحقق، وتحصل البطاقة على نسخة جديدة وتبقى القديمة.")}</p></div>
          <button type="button" className="btn ink sm" onClick={() => ask({ title: L("Evidence and gaps", "الأدلة والفجوات") })}><Icon name="sparkle" size={13} /> {L("Ask Edrak", "اسأل إدراك")}</button>
        </section>
      </div>
    </div>
  );
}
