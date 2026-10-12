import { useEffect, useState } from "react";
import { useL } from "../../lib/useL";
import { edrak, type Dict } from "../../services/edrak";
import type { BriefCard, BriefEvidenceItem, BriefV3, ChallengeResult } from "../../types/brief3";
import { Overlay } from "../ui/Overlay";
import { Icon } from "../ui/Icon";
import { CardBody } from "./charts";
import { useDeck } from "../deck/context";
import { StatusBadge } from "../deck/InsightCard";

const WORKER: Record<string, [string, string]> = {
  competitor_intelligence: ["Competitors", "المنافسون"], customer_trends: ["Customers and trends", "العملاء والاتجاهات"],
  market_intelligence: ["Market", "السوق"], internal_intelligence: ["Your company", "شركتك"],
};

export function CardDrawer({ brief, card, onClose, onChanged }: { brief: BriefV3; card: BriefCard; onClose: () => void; onChanged: () => void }) {
  const L = useL();
  const { ask, openEvidence } = useDeck();
  const [table, setTable] = useState(false);
  const [versions, setVersions] = useState<Dict[]>([]);
  const [challenge, setChallenge] = useState({ text: "", url: "", entity: "", open: false });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ChallengeResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { void edrak.cardVersions(brief.brief_id, card.card_key).then(setVersions).catch(() => setVersions([])); }, [brief.brief_id, card.card_key, card.version]);

  const metrics = card.metric_uids.map((u) => brief.metrics[u]).filter(Boolean);
  const evidence = card.refs.map((n) => brief.evidence.find((e) => e.display_no === n)).filter((e): e is BriefEvidenceItem => !!e);
  const notes = [...(brief.verification?.control_summary?.missing_information ?? []), ...(brief.verification?.control_summary?.conflicts ?? [])]
    .filter((n) => (card.entity && n.toLowerCase().includes(card.entity.toLowerCase())) || (card.metric_id && n.toLowerCase().includes(card.metric_id.replace(/_/g, " "))));
  const worker = WORKER[card.section] ?? [card.section, card.section];
  const needsEntity = !card.entity && new Set(metrics.map((m) => m.entity)).size > 1;

  const send = async () => {
    setBusy(true); setError(null); setResult(null);
    try {
      const r = await edrak.challengeCard(brief.brief_id, card.card_key, {
        source: challenge.url ? "web" : "user", text: challenge.text, url: challenge.url || undefined, entity: challenge.entity || undefined,
      });
      setResult(r);
      if (r.changed) onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <Overlay variant="drawer" label={card.title} onClose={onClose}>
      {(close) => (
        <div className="drawer-body">
          <div className="row g8">
            <div className="stack grow"><span className="eyebrow">{card.title}</span><h3 style={{ margin: 0 }}>{card.headline ?? card.title}</h3></div>
            <StatusBadge status={card.status} />
            <button type="button" className="iconbtn" data-autofocus onClick={close} aria-label={L("Close", "إغلاق")}><Icon name="x" size={16} /></button>
          </div>
          {card.component !== "gap" && (
            <button type="button" className="btn ink" onClick={() => { close(); ask({ cardKey: card.card_key, title: card.title }); }}>
              <Icon name="sparkle" size={14} /> {L("Ask Edrak about this card", "اسأل إدراك عن هذه البطاقة")}
            </button>
          )}

          <div className="panel">{table ? <table className="tbl compact"><tbody>{metrics.map((m) => <tr key={m.uid}><td>{m.entity}{m.label ? ` · ${m.label}` : ""}</td><td className="muted">{m.period ?? ""}</td><td><b>{m.value_text}</b></td><td className="muted">{m.status}</td></tr>)}</tbody></table> : <CardBody card={card} />}</div>
          {card.component !== "gap" && <button type="button" className="btn ghost sm" onClick={() => setTable((v) => !v)}><Icon name="layers" size={14} /> {table ? L("Show as chart", "اعرض كرسم") : L("Show as table", "اعرض كجدول")}</button>}

          <section className="stack gap8">
            <span className="eyebrow">{L("Why this visual", "لماذا هذا الشكل")}</span>
            <span className="s13">{card.why_visual ?? "—"}</span>
          </section>

          <section className="stack gap8">
            <span className="eyebrow">{L("Source agent", "الوكيل المصدر")}</span>
            <span className="s13">{L(worker[0], worker[1])}</span>
            {notes.length > 0 && <><span className="eyebrow">{L("Verifier note", "ملاحظة المدقق")}</span>{notes.map((n) => <span key={n} className="s13">{n}</span>)}</>}
          </section>

          {metrics.length > 0 && (
            <section className="stack gap8">
              <span className="eyebrow">{L("Stored figures", "الأرقام المخزنة")}</span>
              {metrics.map((m) => (
                <div key={m.uid} className="panel stack gap4" style={{ padding: 10 }}>
                  <div className="row g8"><b>{m.value_text}</b><span className="s12 muted">{m.entity}{m.period ? ` · ${m.period}` : ""}</span><span className="grow" /><span className={`chip ${m.status === "verified" ? "c-ok" : m.status === "conflict" ? "c-bad" : "c-ins"}`}>{m.status}</span></div>
                  {m.derived ? <span className="s12 muted">ƒ {m.formula}</span> : m.source_span && <q className="s12 muted">{m.source_span}</q>}
                  {m.sample_size ? <span className="s12 muted">n = {m.sample_size}</span> : null}
                </div>
              ))}
            </section>
          )}

          {evidence.length > 0 && (
            <section className="stack gap8">
              <span className="eyebrow">{L("Evidence", "الأدلة")}</span>
              {evidence.map((e) => (
                <div key={e.display_no} className="panel stack gap4" style={{ padding: 10 }}>
                  <div className="row g8"><button type="button" className="ev" style={{ margin: 0 }} onClick={() => openEvidence(e.display_no)}>{`E${e.display_no}`}</button><b className="s13 grow ellip">{e.evidence.source_title ?? e.evidence.publisher}</b>{e.evidence.source_url && <a href={e.evidence.source_url} target="_blank" rel="noreferrer" className="s12">{L("Open", "فتح")}</a>}</div>
                  <q className="s13" dir="auto">{(e.evidence.excerpt || e.evidence.extracted_fact).slice(0, 420)}</q>
                </div>
              ))}
            </section>
          )}

          {versions.length > 1 && (
            <section className="stack gap8">
              <span className="eyebrow">{L("Version history", "سجل النسخ")}</span>
              {versions.map((v) => <div key={v.version} className="row g8 s13"><span className="chip c-line">v{v.version}</span><span className="grow">{v.note ?? L("Original", "الأصل")}</span><StatusBadge status={v.status} /></div>)}
            </section>
          )}

          {card.component !== "gap" && !card.slots.data_source && (
            <section className="stack gap8">
              <button type="button" className="btn" onClick={() => setChallenge((c) => ({ ...c, open: !c.open }))}><Icon name="warning" size={14} /> {L("Challenge this", "اعترض على هذا")}</button>
              {challenge.open && (
                <div className="stack gap8">
                  <p className="s12 muted">{L("Paste what you know or a web page's text. It goes back through extraction and verification. If it checks out, this card gets a new version; the old one is kept.", "الصق ما تعرفه أو نص صفحة ويب. يمر عبر الاستخراج والتحقق. إن صح، تحصل البطاقة على نسخة جديدة وتبقى القديمة.")}</p>
                  {needsEntity && <input className="inp" placeholder={L("Which company is this about?", "عن أي شركة هذا؟")} value={challenge.entity} onChange={(e) => setChallenge({ ...challenge, entity: e.target.value })} />}
                  <textarea className="inp area" rows={4} value={challenge.text} onChange={(e) => setChallenge({ ...challenge, text: e.target.value })} placeholder={L("e.g. Their team plan costs $23 per user", "مثال: باقة الفريق تكلف 23 دولارًا")} dir="auto" />
                  <input className="inp" placeholder={L("Source link (optional)", "رابط المصدر (اختياري)")} value={challenge.url} onChange={(e) => setChallenge({ ...challenge, url: e.target.value })} />
                  <button type="button" className="btn pri" disabled={busy || challenge.text.trim().length < 3} onClick={() => void send()}>{busy ? L("Checking…", "جارٍ التحقق…") : L("Check and add", "تحقق وأضف")}</button>
                  {error && <div className="chip c-bad" style={{ whiteSpace: "normal" }}>{error}</div>}
                  {result && <div className={`chip ${result.changed ? (result.conflict ? "c-bad" : "c-ok") : "c-line"}`} style={{ whiteSpace: "normal" }}>{result.changed ? (result.conflict ? L(`Sources now disagree. Card version ${result.version} shows both.`, `المصادر متعارضة الآن. النسخة ${result.version} تعرض الاثنين.`) : L(`Added as a second source. Card version ${result.version}.`, `أُضيف كمصدر ثانٍ. النسخة ${result.version}.`)) : result.message}</div>}
                </div>
              )}
            </section>
          )}
        </div>
      )}
    </Overlay>
  );
}
