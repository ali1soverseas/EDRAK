import type { BriefCard } from "../../types/brief3";
import { useL } from "../../lib/useL";

/** The closed set of card bodies. The same metric type always renders as the same component. */

const PALETTE = ["#0742ED", "#6A3DDB", "#08807A", "#A85F00", "#C0331F", "#4A4868"];

export function KpiBody({ card }: { card: BriefCard }) {
  const s = card.slots;
  const cmp = s.comparator as { label: string; value: string; direction: number } | null;
  return (
    <div className="stack gap4">
      <div className="kpi-value">{s.range ? `${s.range.low} – ${s.range.high}` : s.value}</div>
      <div className="row g8 wrap s12 muted">
        {s.period && <span>{s.period}</span>}
        {cmp && <span className={`cmp ${cmp.direction > 0 ? "up" : cmp.direction < 0 ? "down" : ""}`}>{cmp.direction > 0 ? "▲" : cmp.direction < 0 ? "▼" : "■"} {cmp.value} {cmp.label}</span>}
      </div>
    </div>
  );
}

export function LadderBody({ card }: { card: BriefCard }) {
  const rows = (card.slots.rows ?? []) as { label: string; value: string; raw: number; status: string; comparator?: { value: string; label: string; direction: number } | null }[];
  const max = Math.max(1, ...rows.map((r) => r.raw));
  return (
    <div className="stack gap8">
      {rows.map((r, i) => (
        <div key={`${r.label}-${i}`} className="ladder-row">
          <span className="s13 ellip">{r.label}</span>
          <span className="ladder-track"><i style={{ width: `${Math.max(4, (r.raw / max) * 100)}%`, background: r.status === "conflict" ? "var(--bad)" : r.status === "thin" ? "var(--warn)" : PALETTE[0] }} /></span>
          <b className="s13">{r.value}</b>
          {r.comparator && <span className={`cmp s12 ${r.comparator.direction > 0 ? "up" : "down"}`}>{r.comparator.direction > 0 ? "▲" : "▼"} {r.comparator.value}</span>}
        </div>
      ))}
    </div>
  );
}

export function SegmentedBody({ card }: { card: BriefCard }) {
  const parts = (card.slots.parts ?? []) as { label: string; value: number; display: string }[];
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  return (
    <div className="stack gap8">
      <div className="seg-bar" role="img" aria-label={parts.map((p) => `${p.label} ${p.display}`).join(", ")}>
        {parts.map((p, i) => <i key={p.label} style={{ width: `${(p.value / total) * 100}%`, background: PALETTE[i % PALETTE.length] }} title={`${p.label} ${p.display}`} />)}
      </div>
      <div className="row g12 wrap s12">
        {parts.map((p, i) => <span key={p.label} className="row g4"><i className="dot" style={{ background: PALETTE[i % PALETTE.length] }} />{p.label} <b>{p.display}</b></span>)}
      </div>
      {card.slots.n ? <span className="s12 muted">n = {card.slots.n}</span> : null}
    </div>
  );
}

export function LineBody({ card }: { card: BriefCard }) {
  const pts = (card.slots.points ?? []) as { x: string; y: number; display: string }[];
  const W = 320, H = 110, P = 14;
  const times = pts.map((p) => Date.parse(p.x) || 0);
  const minT = Math.min(...times), maxT = Math.max(...times);
  const ys = pts.map((p) => p.y);
  const minY = Math.min(0, ...ys), maxY = Math.max(...ys, 1);
  const X = (i: number) => P + ((maxT === minT ? i / Math.max(1, pts.length - 1) : (times[i] - minT) / (maxT - minT))) * (W - 2 * P);
  const Y = (v: number) => H - P - ((v - minY) / Math.max(1e-9, maxY - minY)) * (H - 2 * P);
  const src = card.slots.data_source as { name?: string; geo?: string; timeframe?: string } | undefined;
  return (
    <div className="stack gap4">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={card.title}>
        <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="currentColor" opacity=".15" />
        <polyline fill="none" stroke={PALETTE[0]} strokeWidth="2" points={pts.map((p, i) => `${X(i)},${Y(p.y)}`).join(" ")} />
        {pts.map((p, i) => <circle key={i} cx={X(i)} cy={Y(p.y)} r="2.5" fill={PALETTE[0]}><title>{`${p.x}: ${p.display}`}</title></circle>)}
      </svg>
      <div className="row g8 s12 muted wrap"><span>{pts[0]?.x} → {pts[pts.length - 1]?.x}</span>{src?.name && <span>· {src.name}{src.geo ? ` (${src.geo})` : ""}</span>}</div>
    </div>
  );
}

export function TimelineBody({ card }: { card: BriefCard }) {
  const events = (card.slots.events ?? []) as { when: string | null; label: string; value: string }[];
  return (
    <ol className="timeline">
      {events.map((e, i) => <li key={i}><span className="mono">{e.when ?? "—"}</span><span className="s13">{e.label}{e.value && e.value !== "—" ? ` · ${e.value}` : ""}</span></li>)}
    </ol>
  );
}

export function TextBody({ card }: { card: BriefCard }) {
  const items = (card.slots.items ?? []) as { entity: string; text: string }[];
  return <ul className="plain-list">{items.map((it, i) => <li key={i} className="s13"><b>{it.entity}</b> {it.text}</li>)}</ul>;
}

export function TableBody({ card }: { card: BriefCard }) {
  const rows = (card.slots.rows ?? []) as { entity: string; label: string | null; period: string | null; value: string }[];
  return (
    <div style={{ overflow: "auto" }}>
      <table className="tbl compact"><tbody>{rows.map((r, i) => <tr key={i}><td>{r.entity}{r.label ? ` · ${r.label}` : ""}</td><td className="muted">{r.period ?? ""}</td><td><b>{r.value}</b></td></tr>)}</tbody></table>
    </div>
  );
}

export function GapBody({ card }: { card: BriefCard }) {
  const L = useL();
  return (
    <div className="stack gap4">
      <span className="s13">{card.slots.explanation}</span>
      <span className="s12 muted">{card.slots.definition}</span>
      <span className="s12 muted">{L("Shown as a gap: Edrak never fills it with a guess.", "يظهر كفجوة: لا يملؤها إدراك بتخمين.")}</span>
    </div>
  );
}

export function CardBody({ card }: { card: BriefCard }) {
  switch (card.component) {
    case "kpi": return <KpiBody card={card} />;
    case "ladder": return <LadderBody card={card} />;
    case "segmented": return <SegmentedBody card={card} />;
    case "line": return <LineBody card={card} />;
    case "timeline": return <TimelineBody card={card} />;
    case "text": return <TextBody card={card} />;
    case "gap": return <GapBody card={card} />;
    default: return <TableBody card={card} />;
  }
}
