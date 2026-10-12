import { useEffect, useState, type RefObject } from "react";
import { useNavigate } from "react-router-dom";
import { useL } from "../../lib/useL";
import { edrak, type Dict } from "../../services/edrak";
import { Icon } from "../ui/Icon";

type BriefDoc = Dict;

/** A small line chart of real search-interest points (0-100, relative). */
function TrendChart({ series }: { series: Dict[] }) {
  const W = 520, H = 150, P = 24;
  const colors = ["#0a3cf0", "#e2a400", "#1f9d55", "#a24bd6", "#d6455b"];
  const all = series.flatMap((s) => s.points.map((p: [string, number]) => Date.parse(p[0])));
  const minT = Math.min(...all), maxT = Math.max(...all);
  const x = (t: number) => P + ((t - minT) / Math.max(1, maxT - minT)) * (W - 2 * P);
  const y = (v: number) => H - P - (v / 100) * (H - 2 * P);
  return (
    <div className="stack gap8">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Search interest">
        <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="currentColor" opacity=".2" />
        {series.map((s, i) => (
          <polyline key={s.keyword} fill="none" stroke={colors[i % colors.length]} strokeWidth="2"
            points={s.points.map((p: [string, number]) => `${x(Date.parse(p[0]))},${y(p[1])}`).join(" ")} />
        ))}
      </svg>
      <div className="row g12 wrap s12">
        {series.map((s, i) => <span key={s.keyword} className="row g4"><i style={{ width: 10, height: 3, background: colors[i % colors.length], display: "inline-block" }} />{s.keyword}{s.geo ? ` (${s.geo})` : ""}</span>)}
      </div>
    </div>
  );
}

const CELL: Record<string, [string, string, string]> = {
  broader: ["Broader", "أوسع", "c-bad"], comparable: ["Comparable", "مماثل", "c-line"], narrower: ["Narrower", "أضيق", "c-ok"],
  different_approach: ["Different approach", "نهج مختلف", "c-blue"], not_established: ["Not found", "غير موجود", "c-draft"],
};

export function BriefToolbar({ brief, container, fullscreen, setFullscreen, typed, onChanged }: {
  typed?: boolean;
  onChanged?: () => void;
  brief: BriefDoc; container: RefObject<HTMLDivElement | null>; fullscreen: boolean; setFullscreen: (v: boolean) => void;
}) {
  const L = useL();
  const navigate = useNavigate();
  const [lifecycle, setLifecycle] = useState<string>(brief.lifecycle ?? "ready");
  const [menu, setMenu] = useState(false);
  const [extras, setExtras] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [decision, setDecision] = useState({ option_label: "", title: "", rationale: "", review_on: "", next_steps: "" });
  const [error, setError] = useState<string | null>(null);
  const id = brief.brief_id as string;
  const options: Dict[] = brief.options ?? [];

  const toggleFullscreen = async () => {
    const el = container.current;
    if (!fullscreen) {
      try {
        if (el?.requestFullscreen) await el.requestFullscreen();
      } catch {
        /* the CSS focus mode below still applies */
      }
      setFullscreen(true);
    } else {
      if (document.fullscreenElement) await document.exitFullscreen().catch(() => undefined);
      setFullscreen(false);
    }
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (["INPUT", "TEXTAREA"].includes(target.tagName)) return;
      if (e.key.toLowerCase() === "f" && !e.metaKey && !e.ctrlKey) void toggleFullscreen();
      if (e.key === "Escape" && fullscreen) setFullscreen(false);
    };
    const onChange = () => { if (!document.fullscreenElement) setFullscreen(false); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("fullscreenchange", onChange);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("fullscreenchange", onChange); };
  });

  const record = async () => {
    setError(null);
    try {
      await edrak.recordDecision(id, {
        option_label: decision.option_label || null, title: decision.title || options.find((o) => o.key === decision.option_label)?.title || "Decision",
        rationale: decision.rationale || null, review_on: decision.review_on || null,
        next_steps: decision.next_steps.split("\n").map((s) => s.trim()).filter(Boolean),
      });
      setLifecycle("decided");
      setDeciding(false);
      onChanged?.();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <>
      <div className="row g8 wrap" style={{ padding: "10px 24px", borderBottom: "1px solid var(--hair)", background: "var(--paper)", position: "relative", zIndex: 3 }}>
        <span className={`chip ${lifecycle === "decided" ? "c-dec" : lifecycle === "reviewed" ? "c-rev" : "c-line"}`}>
          {lifecycle === "decided" ? L("Decided", "تم القرار") : lifecycle === "reviewed" ? L("Reviewed", "تمت المراجعة") : L("Not yet reviewed", "لم تُراجع بعد")}
        </span>
        <span className={`chip ${brief.evidence_status === "verified" ? "c-ok" : "c-ins"}`}>{brief.evidence_status}</span>
        <div className="grow" />
        <button type="button" className="btn ghost" onClick={() => setExtras((v) => !v)}><Icon name="layers" size={14} /> {L("Compared and trends", "المقارنة والاتجاهات")}</button>
        <button type="button" className="btn ghost" onClick={() => navigate(`/evidence/${id}`)}><Icon name="link" size={14} /> {L("Evidence", "الأدلة")}</button>
        <div style={{ position: "relative" }}>
          <button type="button" className="btn" onClick={() => setMenu((v) => !v)}><Icon name="upload" size={14} /> {L("Download", "تنزيل")} <Icon name="chevronDown" size={12} /></button>
          {menu && (
            <div className="popover" style={{ width: 200, top: 42 }}>
              {[["pdf", "PDF"], ["docx", "Word"], ["markdown", "Markdown"]].map(([f, label]) => (
                <a key={f} className="list-row" style={{ padding: "10px 14px", color: "inherit" }} href={edrak.exportUrl(id, f)} onClick={() => setMenu(false)}>{label}</a>
              ))}
            </div>
          )}
        </div>
        <button type="button" className="btn" onClick={() => void toggleFullscreen()} title="F">
          <Icon name={fullscreen ? "x" : "external"} size={14} /> {fullscreen ? L("Exit full screen", "إنهاء ملء الشاشة") : L("Full screen", "ملء الشاشة")}
        </button>
        {lifecycle !== "decided" ? (
          <>
            {lifecycle === "ready" && <button type="button" className="btn ghost" onClick={() => void edrak.reviewBrief(id, "accepted_as_reviewed").then(() => setLifecycle("reviewed"))}>{L("Accept as reviewed", "اعتمد كمراجَع")}</button>}
            <button type="button" className="btn ghost" onClick={() => void edrak.reviewBrief(id, "requested_investigation").then((r) => r.follow_up_analysis_id && navigate(`/analyses/new?draft=${r.follow_up_analysis_id}`))}>{L("More investigation", "مزيد من التحقيق")}</button>
            {!typed && <button type="button" className="btn" style={{ background: "var(--gold, #f5d547)" }} onClick={() => setDeciding(true)}>{L("Record decision", "سجّل القرار")}</button>}
          </>
        ) : (
          <button type="button" className="btn ghost" onClick={() => void edrak.undoDecision(id).then(() => { setLifecycle("reviewed"); onChanged?.(); })}>{L("Undo decision", "تراجع عن القرار")}</button>
        )}
      </div>

      {extras && (
        <div className="panel stack gap16" style={{ margin: "12px 24px", maxHeight: "60vh", overflow: "auto", position: "relative", zIndex: 3 }}>
          {brief.comparison?.dimensions?.length ? (
            <div className="stack gap8">
              <span className="eyebrow">{L("Alternatives compared", "مقارنة البدائل")}</span>
              <div style={{ overflow: "auto" }}>
                <table className="tbl">
                  <thead><tr><th>{L("Dimension", "البعد")}</th><th>{L("Us", "نحن")}</th>{brief.comparison.dimensions[0].cells.map((c: Dict) => <th key={c.competitor}>{c.competitor}</th>)}</tr></thead>
                  <tbody>{brief.comparison.dimensions.map((d: Dict) => (
                    <tr key={d.dimension}><td className="b6">{d.dimension}</td><td className="s13">{d.target_position}</td>
                      {d.cells.map((c: Dict) => {
                        const [en, ar, tone] = CELL[c.relative_to_target] ?? CELL.not_established;
                        return <td key={c.competitor} title={c.summary}><span className={`chip ${tone}`}>{L(en, ar)}</span> {c.refs?.map((r: number) => <span key={r} className="ev">E{r}</span>)}</td>;
                      })}</tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          ) : <div className="empty">{L("No comparison was established for this brief.", "لم تُبنَ مقارنة لهذا الموجز.")}</div>}
          {brief.trends?.length ? (
            <div className="stack gap8"><span className="eyebrow">{L("Search interest (relative, 0-100)", "اهتمام البحث (نسبي)")}</span><TrendChart series={brief.trends} /></div>
          ) : null}
          {(brief.opportunities?.length || brief.risks?.length) ? (
            <div className="grid2" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <div className="stack gap4"><span className="eyebrow">{L("Opportunities", "الفرص")}</span>{(brief.opportunities ?? []).map((o: Dict, i: number) => <span key={i} className="s13" dir="auto">· {o.title}</span>)}</div>
              <div className="stack gap4"><span className="eyebrow">{L("Risks", "المخاطر")}</span>{(brief.risks ?? []).map((o: Dict, i: number) => <span key={i} className="s13" dir="auto">· {o.title}</span>)}</div>
            </div>
          ) : null}
        </div>
      )}

      {deciding && (
        <div className="viewer-scrim" onClick={() => setDeciding(false)}>
          <div className="panel stack gap12" style={{ width: 560, maxWidth: "100%" }} onClick={(e) => e.stopPropagation()}>
            <span className="eyebrow">{L("Your decision", "قرارك")}</span>
            <h3 style={{ margin: 0 }}>{L("Record your decision", "سجّل قرارك")}</h3>
            {options.length > 0 && (
              <div className="stack gap8">
                {options.map((o) => (
                  <label key={o.key} className="row g8" style={{ alignItems: "flex-start", cursor: "pointer" }}>
                    <input type="radio" name="opt" checked={decision.option_label === o.key} onChange={() => setDecision({ ...decision, option_label: o.key, title: decision.title || o.title })} />
                    <span><b>{o.key}.</b> {o.title}</span>
                  </label>
                ))}
                <span className="s12 muted">{L("Not ranked. The decision stays with you.", "غير مرتبة. القرار لك.")}</span>
              </div>
            )}
            <label className="field"><span>{L("Decision", "القرار")}</span><input className="input" value={decision.title} onChange={(e) => setDecision({ ...decision, title: e.target.value })} dir="auto" /></label>
            <label className="field"><span>{L("Rationale", "المبررات")}</span><textarea className="textarea" value={decision.rationale} onChange={(e) => setDecision({ ...decision, rationale: e.target.value })} dir="auto" /></label>
            <label className="field"><span>{L("Review on", "المراجعة في")}</span><input className="input" type="date" value={decision.review_on} onChange={(e) => setDecision({ ...decision, review_on: e.target.value })} /></label>
            <label className="field"><span>{L("Next steps (one per line)", "الخطوات التالية (سطر لكل خطوة)")}</span><textarea className="textarea" value={decision.next_steps} onChange={(e) => setDecision({ ...decision, next_steps: e.target.value })} dir="auto" /></label>
            <span className="s12 muted">{L(`Locks a snapshot of the ${brief.counts?.sources ?? brief.evidence?.length ?? 0} sources. The brief can change later; this record cannot.`, "يقفل نسخة من المصادر. يمكن أن يتغير الموجز لاحقًا، لكن هذا السجل لا يتغير.")}</span>
            {error && <span className="s13" style={{ color: "#c0392b" }}>{error}</span>}
            <div className="row g8" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn ghost" onClick={() => setDeciding(false)}>{L("Cancel", "إلغاء")}</button>
              <button type="button" className="btn" style={{ background: "var(--gold, #f5d547)" }} disabled={!decision.title.trim() && !decision.option_label} onClick={() => void record()}>{L("Record decision", "سجّل القرار")}</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
