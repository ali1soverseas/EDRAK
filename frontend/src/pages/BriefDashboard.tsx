import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ActionPlanEditor } from "../components/actionplan/ActionPlanEditor";
import { BriefToolbar } from "../components/brief/BriefToolbar";
import { CardDrawer } from "../components/dashboard/CardDrawer";
import { CardFrame, StatusBadge } from "../components/dashboard/CardFrame";
import { Icon } from "../components/ui/Icon";
import { useI18n } from "../i18n";
import { useL } from "../lib/useL";
import { api } from "../services/api";
import type { BriefCard, BriefOption, BriefV3, KpiItem } from "../types/brief3";

type Role = "executive" | "analyst" | "pm";
const ROLE_KEY = "edrak.brief.role";

const readRole = (): Role => {
  try { const v = localStorage.getItem(ROLE_KEY); return v === "executive" || v === "analyst" || v === "pm" ? v : "analyst"; } catch { return "analyst"; }
};

/** The role changes order and density, never the data. */
const ROLE_LENS_FIRST: Record<Role, string[]> = {
  executive: [],
  analyst: [],
  pm: ["customer_trends", "competitor_intelligence"],
};

function KpiTile({ k, onOpen }: { k: KpiItem; onOpen: (key: string) => void }) {
  const { lang } = useI18n();
  const label = lang === "ar" && k.label.ar ? k.label.ar : k.label.en;
  return (
    <button type="button" className={`kpi-tile st-${k.status}`} onClick={() => onOpen(k.card_key)}>
      <span className="eyebrow ellip">{label}</span>
      <b className="kpi-value sm">{k.value}</b>
      <span className="row g4 s12 muted wrap">
        {k.entity && <span className="ellip">{k.entity}</span>}
        {k.comparator && <span className={`cmp ${k.comparator.direction > 0 ? "up" : "down"}`}>{k.comparator.direction > 0 ? "▲" : "▼"} {k.comparator.value} {k.comparator.label}</span>}
      </span>
      {k.status !== "verified" && <StatusBadge status={k.status} />}
    </button>
  );
}

function OptionCard({ o, chosen, onChoose }: { o: BriefOption; chosen: boolean; onChoose: () => void }) {
  const L = useL();
  return (
    <article className={`option3 ${chosen ? "chosen" : ""}`}>
      <header className="row g8"><span className="option-key">{o.key}</span><b className="grow">{o.title}</b>{o.guardrail === "uncited" && <span className="chip c-ins">{L("No signal cited", "بلا إشارة")}</span>}</header>
      <p className="s13" dir="auto">{o.thesis || o.description}</p>
      {o.what_we_would_do?.length > 0 && <div className="stack gap4"><span className="eyebrow">{L("What we would do", "ما سنفعله")}</span><ul className="plain-list">{o.what_we_would_do.slice(0, 4).map((t) => <li key={t} className="s13">{t}</li>)}</ul></div>}
      {o.trade_offs?.length > 0 && <div className="stack gap4"><span className="eyebrow">{L("Trade-offs", "المفاضلات")}</span><ul className="plain-list">{o.trade_offs.slice(0, 3).map((t) => <li key={t} className="s13">{t}</li>)}</ul></div>}
      <div className="grid2" style={{ gap: 12 }}>
        <div className="stack gap4"><span className="eyebrow">{L("Opportunities", "الفرص")}</span>{o.opportunities?.slice(0, 3).map((x) => <span key={x.text} className="s13 opp">＋ {x.text}</span>)}</div>
        <div className="stack gap4"><span className="eyebrow">{L("Risks", "المخاطر")}</span>{o.risks?.slice(0, 3).map((x) => <span key={x.text} className="s13 risk">！ {x.text}{x.likelihood ? <i className="s12 muted"> ({x.likelihood}/{x.impact})</i> : null}</span>)}</div>
      </div>
      {o.would_change_if && <p className="s12 muted"><b>{L("Would change if", "يتغير إذا")}:</b> {o.would_change_if}</p>}
      {o.cost_of_waiting && <p className="s12 muted"><b>{L("Cost of waiting", "كلفة الانتظار")}:</b> {o.cost_of_waiting}</p>}
      {o.evidence_refs && o.evidence_refs.length > 0 && <div className="row g4 wrap">{o.evidence_refs.slice(0, 6).map((n) => <span key={n} className="ev">{`E${n}`}</span>)}</div>}
      <footer><button type="button" className={`btn ${chosen ? "" : "blue"}`} onClick={onChoose}>{chosen ? L("Chosen: see its action plan", "المختار: خطة عمله") : L("Choose this option", "اختر هذا الخيار")}</button></footer>
    </article>
  );
}

export function BriefDashboard({ initial }: { initial: BriefV3 }) {
  const L = useL();
  const navigate = useNavigate();
  const [brief, setBrief] = useState<BriefV3>(initial);
  const [role, setRole] = useState<Role>(readRole);
  const [open, setOpen] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const decisionRef = useRef<HTMLElement>(null);

  const reload = useCallback(() => { void api.getBrief(brief.brief_id).then((b) => setBrief(b as unknown as BriefV3)); }, [brief.brief_id]);
  useEffect(() => { try { localStorage.setItem(ROLE_KEY, role); } catch { /* private window */ } }, [role]);

  const ask = (cardKey?: string) => window.dispatchEvent(new CustomEvent("edrak:ask", { detail: { briefId: brief.brief_id, cardKey } }));
  const evidenceChip = (no: number) => { const el = document.getElementById(`ev-${no}`); el?.scrollIntoView({ behavior: "smooth", block: "center" }); el?.classList.add("flash"); window.setTimeout(() => el?.classList.remove("flash"), 1600); };

  const sections = useMemo(() => {
    const first = ROLE_LENS_FIRST[role];
    return [...brief.sections].sort((a, b) => (first.includes(a.lens) ? -1 : 0) - (first.includes(b.lens) ? -1 : 0) || 0);
  }, [brief.sections, role]);
  const card = open ? brief.cards[open] : null;
  const decided = brief.lifecycle === "decided";
  const optionChosen = chosen ?? (brief.options.length === 1 ? brief.options[0].key : null);
  const cov = brief.coverage;
  const covPct = cov.score == null ? null : Math.round(cov.score * 100);
  const gaps = brief.gaps_panel.gaps;

  return (
    <div ref={container} className={`col ${fullscreen ? "brief-fullscreen" : ""}`} style={{ height: "100%", minHeight: 0 }}>
      <BriefToolbar brief={brief as never} container={container} fullscreen={fullscreen} setFullscreen={setFullscreen} typed onChanged={reload} />
      <div className={`dash3 role-${role}`} data-role={role}>
        <header className="dash3-head">
          <div className="stack gap8 grow">
            <span className="eyebrow">{brief.title} · {L("Brief", "موجز")} {String(brief.brief_no).padStart(4, "0")}</span>
            <h1 className="dash3-headline" dir="auto">{brief.headline}</h1>
          </div>
          <div className="stack gap8" style={{ alignItems: "flex-end" }}>
            <div className="seg-toggle" role="group" aria-label={L("View for", "عرض لـ")}>
              {(["executive", "analyst", "pm"] as Role[]).map((r) => (
                <button key={r} type="button" aria-pressed={role === r} className={role === r ? "on" : ""} onClick={() => setRole(r)}>
                  {r === "executive" ? L("Executive", "تنفيذي") : r === "analyst" ? L("Analyst", "محلل") : L("PM", "مدير منتج")}
                </button>
              ))}
            </div>
            <button type="button" className="btn ghost sm" onClick={() => ask()}><Icon name="sparkle" size={14} /> {L("Ask about this brief", "اسأل عن هذا الموجز")}</button>
          </div>
        </header>

        {brief.kpis.length > 0 && <div className="kpi-strip" role="list">{brief.kpis.map((k) => <KpiTile key={k.card_key} k={k} onOpen={setOpen} />)}</div>}

        <section className="panel coverage" aria-label={L("Coverage and gaps", "التغطية والفجوات")}>
          <div className="row g12 wrap">
            <div className="stack gap4" style={{ minWidth: 200 }}>
              <span className="eyebrow">{L("Coverage of required figures", "تغطية الأرقام المطلوبة")}</span>
              <div className="row g8"><b className="s20">{covPct == null ? "—" : `${covPct}%`}</b><span className="s12 muted">{cov.met} / {cov.required} {L("verified", "موثّق")}</span></div>
              <div className="bar"><i style={{ width: `${covPct ?? 0}%` }} /></div>
            </div>
            <div className="stack gap4 grow">
              <span className="eyebrow">{L("Gaps", "الفجوات")} ({gaps.length})</span>
              {gaps.length === 0 && <span className="s13 muted">{L("No required figure is missing.", "لا نقص في الأرقام المطلوبة.")}</span>}
              <div className="row g8 wrap">{gaps.slice(0, role === "executive" ? 4 : 12).map((g) => <button key={g.card_key} type="button" className={`chip ${g.blocking ? "c-bad" : "c-draft"}`} title={g.explanation} onClick={() => g.blocking && setOpen(g.card_key)}>{g.title}</button>)}</div>
              {brief.gaps_panel.collapsed_lenses.length > 0 && <span className="s12 muted">{L("Not enough evidence to show these areas as sections:", "أدلة غير كافية لعرض هذه المجالات كأقسام:")} {brief.gaps_panel.collapsed_lenses.join(", ")}</span>}
            </div>
            <div className="row g8"><button type="button" className="btn ghost sm" onClick={() => navigate("/sources")}><Icon name="upload" size={14} /> {L("Add data", "أضف بيانات")}</button></div>
          </div>
        </section>

        {brief.signals_view.length > 0 && (
          <section className="stack gap8">
            <span className="eyebrow">{L("What this means", "ماذا يعني هذا")}</span>
            <div className="signals">{brief.signals_view.slice(0, role === "executive" ? 3 : 6).map((s) => (
              <article key={s.signal_id} className="card3 signal">
                <b className="s13" dir="auto">{s.title}</b>
                <p className="s13 muted" dir="auto">{s.signal}</p>
                {s.why_it_matters && <p className="s13" dir="auto"><b>{L("Why it matters", "لماذا يهم")}:</b> {s.why_it_matters}</p>}
                <div className="row g4 wrap">{s.refs.slice(0, 6).map((n) => <button key={n} type="button" className="ev" onClick={() => evidenceChip(n)}>{`E${n}`}</button>)}</div>
              </article>))}</div>
          </section>
        )}

        {sections.map((sec) => {
          if (sec.collapsed || (sec.cards.length === 0 && sec.gap_cards.length === 0)) return null;
          const cards = [...sec.cards, ...sec.gap_cards].map((k) => brief.cards[k]).filter(Boolean) as BriefCard[];
          return (
            <details key={sec.lens} className="lens3" open={role !== "executive"}>
              <summary className="row g8"><b>{sec.label}</b><span className="chip c-line">{cards.length}</span><span className="grow" /><span className="s12 muted">{sec.evidence_count} {L("sources", "مصادر")}</span></summary>
              <div className="cards3">{cards.map((c) => <CardFrame key={c.card_key} card={c} onOpen={setOpen} onAsk={ask} onEvidence={evidenceChip} />)}</div>
            </details>
          );
        })}

        {role !== "executive" && (
          <details className="lens3" open={role === "analyst"}>
            <summary className="row g8"><b>{L("Evidence", "الأدلة")}</b><span className="chip c-line">{brief.evidence.length}</span></summary>
            <div style={{ overflow: "auto" }}>
              <table className="tbl compact"><thead><tr><th>E#</th><th>{L("Source", "المصدر")}</th><th>{L("Area", "المجال")}</th><th>{L("Reliability", "الموثوقية")}</th></tr></thead>
                <tbody>{brief.evidence.map((e) => (
                  <tr key={e.display_no} id={`ev-${e.display_no}`}><td><span className="ev">{`E${e.display_no}`}</span></td>
                    <td><bdi className="b6 s13">{e.evidence.source_title ?? e.evidence.publisher}</bdi>{e.evidence.source_url && <> <a className="s12" href={e.evidence.source_url} target="_blank" rel="noreferrer">↗</a></>}<div className="s12 muted" dir="auto">{(e.evidence.extracted_fact || "").slice(0, 160)}</div></td>
                    <td className="s12">{e.worker.replace(/_/g, " ")}</td><td>{"●".repeat(e.reliability)}{"○".repeat(Math.max(0, 3 - e.reliability))}</td></tr>))}</tbody></table>
            </div>
          </details>
        )}

        <section ref={decisionRef} className="stack gap12" aria-label={L("Decision", "القرار")}>
          <div className="row g8"><span className="eyebrow grow">{L("Options", "الخيارات")}</span>{decided && <span className="chip c-dec">{L("Decision recorded", "سُجّل القرار")}</span>}</div>
          {brief.options.length === 0 && <div className="empty">{L("The analysis did not produce options for this decision.", "لم ينتج التحليل خيارات لهذا القرار.")}</div>}
          <div className="options3">{brief.options.map((o) => <OptionCard key={o.key} o={o} chosen={optionChosen === o.key} onChoose={() => setChosen(o.key)} />)}</div>
          {optionChosen && brief.options.find((o) => o.key === optionChosen) && (
            <ActionPlanEditor brief={brief} option={brief.options.find((o) => o.key === optionChosen) as BriefOption} onDecisionChanged={reload} />
          )}
        </section>
      </div>
      {card && <CardDrawer brief={brief} card={card} onClose={() => setOpen(null)} onChanged={reload} />}
    </div>
  );
}
