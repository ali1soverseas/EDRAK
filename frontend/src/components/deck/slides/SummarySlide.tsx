import { useI18n } from "../../../i18n";
import { USE_CASE_META } from "../../../lib/usecases";
import { useL } from "../../../lib/useL";
import type { SummarySlideData } from "../../../types/brief3";
import { Chip } from "../../ui/Chip";
import { Icon } from "../../ui/Icon";
import { EvChips } from "../InsightCard";
import { LENS, useDeck } from "../context";
import { SlideHead } from "./SlideHead";

/** 01 Summary: the one insight that matters, the best findings behind it, and how complete the picture is. */
export function SummarySlide({ slide }: { slide: SummarySlideData }) {
  const L = useL();
  const { t, formatDate } = useI18n();
  const { brief, openSignal, openCard, goTo } = useDeck();
  const meta = USE_CASE_META[brief.use_case as keyof typeof USE_CASE_META];
  const signals = slide.top_signals.map((id) => brief.signals_view.find((s) => s.signal_id === id)).filter(Boolean);
  const kpis = slide.kpis.map((k) => brief.kpis.find((x) => x.card_key === k)).filter(Boolean);
  const cov = slide.coverage;
  const pct = cov?.score == null ? null : Math.round(cov.score * 100);
  const lensStatus = brief.deck?.slides.find((s) => s.id === "evidence_gaps");
  const byLens = lensStatus && "by_lens" in lensStatus ? lensStatus.by_lens : [];

  return (
    <div className="slide summary-slide">
      <div className="summary-left bc-a">
        <div className="stack gap16">
          <SlideHead no={1} name={L("Summary", "الملخص")} />
          <div className="row g10 wrap">
            {meta && <Chip tone="dec" icon="sparkle">{t(meta.short)}</Chip>}
            <span className="mono t3">{formatDate(brief.created_at)}</span>
          </div>
          <h1 className={`disp deck-headline ${slide.headline.length > 110 ? "long" : slide.headline.length > 70 ? "mid" : ""}`} dir="auto">{slide.headline}</h1>
          <p className="t2 s14 lh14" dir="auto">{slide.question}</p>
          <div className="row g8 wrap">
            <Chip tone={brief.partial ? "ins" : "ok"} icon={brief.partial ? "warning" : "check"}>{brief.partial ? L("Partial evidence", "أدلة جزئية") : L("Verified", "موثّق")}</Chip>
            {slide.counts && <Chip tone="line" icon="layers">{L(`${slide.counts.sources} sources · ${slide.counts.verified} verified findings`, `${slide.counts.sources} مصدر · ${slide.counts.verified} نتيجة موثقة`)}</Chip>}
          </div>
          <div className="worker-grid">
            {byLens.map((l) => (
              <button key={l.lens} type="button" className={`worker-pill tap-lite ${l.failed ? "failed" : ""}`} onClick={() => goTo("evidence_gaps")}
                title={L(`${l.verified} verified findings from ${l.sources} sources`, `${l.verified} نتيجة موثقة من ${l.sources} مصدر`)}>
                <span className={`lens-dot ${LENS[l.lens]?.css ?? ""}`} />
                <span className="s12 b6 grow ellip">{L(LENS[l.lens]?.en ?? l.label, LENS[l.lens]?.ar ?? l.label)}</span>
                <span className="mono s12 t3">{l.sources}</span>
                <span style={{ color: l.failed || l.sources === 0 ? "var(--bad)" : "var(--ok)", display: "inline-flex" }}>
                  <Icon name={l.failed || l.sources === 0 ? "x" : "check"} size={13} stroke={2.4} />
                </span>
              </button>
            ))}
          </div>
        </div>
        <div className="stack gap10">
          <button type="button" className="coverage tap-lite" onClick={() => goTo("evidence_gaps")}>
            <div className="row jb"><span className="eyebrow">{L("Required figures found", "الأرقام المطلوبة الموجودة")}</span><b className="mono s13">{pct == null ? "—" : `${pct}%`}</b></div>
            <div className="bar"><i style={{ width: `${pct ?? 0}%` }} /></div>
            <span className="s12 t3">{cov ? L(`${cov.met} of ${cov.required} checked and verified`, `${cov.met} من ${cov.required} موثّقة`) : ""}</span>
          </button>
          {(slide.limits || slide.unmet.length > 0) && (
            <div className="limits hatch">
              <Icon name="warning" size={18} stroke={1.8} />
              <div className="s13 lh14 grow">
                <b>{L("Read with these limits.", "اقرأ مع هذه الحدود.")}</b>{" "}
                {slide.unmet.length > 0 && <bdi>{L(`Nothing was found on ${slide.unmet.join(", ")}, so the brief makes no claim about it. `, `لم يُعثر على شيء عن ${slide.unmet.join("، ")}، لذا لا يدّعي الموجز شيئًا عنه. `)}</bdi>}
                {slide.limits && <bdi>{slide.limits}</bdi>}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="summary-right bc-b">
        {kpis.length > 0 && (
          <div className="summary-block">
            <div className="mono tb">{L("Key figures", "أرقام رئيسية")}</div>
            <div className="kpi-row">
              {kpis.map((k) => k && (
                <button key={k.card_key} type="button" className={`kpi-tile st-${k.status}`} onClick={() => openCard(k.card_key)}>
                  <span className="eyebrow ellip">{L(k.label.en, k.label.ar ?? k.label.en)}</span>
                  <b className="kpi-value sm">{k.value}</b>
                  <span className="s12 t3 ellip">{k.entity}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="summary-block grow-block">
          <div className="mono tb">{L("What we found", "ما وجدناه")}</div>
          {slide.key_findings.length === 0 && <p className="serif summary-text t2">{L("No finding passed verification, so nothing is stated as found.", "لم تجتز أي نتيجة التحقق، فلا شيء يُعرض كنتيجة.")}</p>}
          <ol className="findings-list">
            {slide.key_findings.map((f) => (
              <li key={f.finding_id}>
                <span className={`lens-dot ${LENS[f.lens]?.css ?? ""}`} title={L(LENS[f.lens]?.en ?? f.lens, LENS[f.lens]?.ar ?? f.lens)} />
                <span className="serif finding-line" dir="auto">{f.text}</span>
                <EvChips refs={f.refs} max={3} />
              </li>
            ))}
          </ol>
        </div>
        {signals.length > 0 && (
          <div className="summary-block">
            <div className="row jb"><span className="mono tb">{L("What it means", "ماذا يعني")}</span>
              <button type="button" className="btn ghost sm" onClick={() => goTo("signals")}>{L("All signals", "كل الإشارات")} <Icon name="chevron" size={12} flip /></button></div>
            <div className="mini-signals">
              {signals.map((s) => s && (
                <button key={s.signal_id} type="button" className={`mini-signal tap-lite ${s.type === "conflict" ? "conflict" : ""}`} onClick={() => openSignal(s.signal_id)}>
                  <b className="s13 lh12" dir="auto">{s.title}</b>
                  {s.why_it_matters && <span className="s12 t2 lh14 clamp2" dir="auto">{s.why_it_matters}</span>}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
