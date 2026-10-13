import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { QuickAnalysisDrawer } from "../components/QuickAnalysisDrawer";
import { PageChrome } from "../components/shell/AppShell";
import { Icon } from "../components/ui/Icon";
import { useI18n } from "../i18n";
import { useL } from "../lib/useL";
import { edrak, type Dict } from "../services/edrak";

const UC: Record<string, [string, string]> = {
  competitive_intelligence: ["Competitive intelligence", "الاستخبارات التنافسية"],
  market_entry_expansion: ["Market entry and expansion", "دخول السوق والتوسع"],
  product_launch: ["Product launch", "إطلاق منتج"],
};
const no = (n: number) => String(n ?? 0).padStart(4, "0");

function Header({ eyebrow, title, children }: { eyebrow: string; title: string; children?: React.ReactNode }) {
  return (
    <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 12 }}>
      <div><div className="eyebrow">{eyebrow}</div><h1 style={{ margin: "6px 0 0" }}>{title}</h1></div>
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ Reports */

export function Reports() {
  const L = useL();
  const { formatWhen } = useI18n();
  const navigate = useNavigate();
  const [rows, setRows] = useState<Dict[] | null>(null);
  const [useCase, setUseCase] = useState("");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [compare, setCompare] = useState<string[]>([]);
  const [diff, setDiff] = useState<Dict | null>(null);
  useEffect(() => { void edrak.reports(useCase ? `?use_case=${useCase}` : "").then(setRows).catch(() => setRows([])); }, [useCase]);
  const pick = (id: string) => setCompare((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id].slice(-2)));
  useEffect(() => { if (compare.length === 2) void edrak.compare(compare[0], compare[1]).then(setDiff); else setDiff(null); }, [compare]);

  return (
    <>
      <PageChrome crumbs={[{ label: L("Reports", "التقارير") }]} title={L("Reports", "التقارير")} />
      <div className="stack gap16" style={{ maxWidth: 1180 }}>
        <Header eyebrow={L("Reports", "التقارير")} title={L("Every brief.", "كل الموجزات.")}>
          <div className="row g8">
            <select className="input" style={{ width: 230 }} value={useCase} onChange={(e) => setUseCase(e.target.value)}>
              <option value="">{L("All use cases", "كل حالات الاستخدام")}</option>
              {Object.entries(UC).map(([k, [en, ar]]) => <option key={k} value={k}>{L(en, ar)}</option>)}
            </select>
            <button type="button" className="btn ghost" onClick={() => setView(view === "grid" ? "list" : "grid")}>{view === "grid" ? L("List", "قائمة") : L("Grid", "شبكة")}</button>
            <Link to="/analyses" className="btn ghost">{L("All analyses", "كل التحليلات")}</Link>
          </div>
        </Header>
        <span className="s13 muted">{L("Select two briefs to see what changed between them. Download any report as PDF, Word or Markdown from the brief.", "اختر موجزين لرؤية ما تغيّر بينهما.")}</span>
        {rows?.length === 0 && <div className="empty">{L("No briefs yet. Approve a plan to get your first one.", "لا موجزات بعد.")}</div>}
        <div className={view === "grid" ? "grid3" : "stack"}>
          {rows?.map((r) => (
            <div key={r.id} className={view === "grid" ? "panel stack gap8" : "list-row"} style={{ cursor: "pointer", outline: compare.includes(r.id) ? "2px solid var(--blue)" : undefined }} onClick={() => navigate(`/briefs/${r.id}`)}>
              <div className="row g8"><span className="eyebrow">{L(...(UC[r.use_case] ?? ["", ""]) as [string, string])}</span><span className="s12 muted">#{no(r.number)}</span></div>
              <bdi className="b6 grow">{r.title}</bdi>
              <div className="row g8 wrap">
                <span className={`chip ${r.evidence_status === "verified" ? "c-ok" : "c-ins"}`}>{r.evidence_status}</span>
                <span className="chip c-line">{r.lifecycle}</span>
                <span className="s12 muted">{formatWhen(r.generated_at)}</span>
                <button type="button" className="btn ghost" style={{ height: 26 }} onClick={(e) => { e.stopPropagation(); pick(r.id); }}>{compare.includes(r.id) ? L("Selected", "محدد") : L("Compare", "قارن")}</button>
              </div>
            </div>
          ))}
        </div>
        {diff && (
          <div className="panel stack gap8">
            <span className="eyebrow">{L(`What changed: brief ${no(diff.from.number)} → ${no(diff.to.number)}`, `ما الذي تغيّر: ${no(diff.from.number)} ← ${no(diff.to.number)}`)}</span>
            {diff.changes.length === 0 && <span className="muted">{L("No differences in findings.", "لا فروق.")}</span>}
            {diff.changes.map((c: Dict, i: number) => (
              <div key={i} className="list-row"><b style={{ width: 18 }}>{c.change_type === "added" ? "+" : c.change_type === "removed" ? "−" : "~"}</b><bdi className="grow s13">{c.title}</bdi><span className={`chip ${c.importance === "high" ? "c-bad" : "c-line"}`}>{c.importance}</span></div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ Monitors */

const EMPTY_MONITOR = { name: "", use_case: "competitive_intelligence", goal: "", frequency: "weekly", day_of_week: 1, run_time: "08:00", time_zone: "Africa/Cairo", focus: ["pricing", "features"], entity_ids: [] as string[], notify_email: true, notify_in_app: true, notify_min_importance: "medium", auto_approve_plans: false, status: "active", triggers: [] as Dict[] };

export function Monitors() {
  const L = useL();
  const { formatWhen } = useI18n();
  const navigate = useNavigate();
  const [rows, setRows] = useState<Dict[]>([]);
  const [entities, setEntities] = useState<Dict[]>([]);
  const [edit, setEdit] = useState<(typeof EMPTY_MONITOR & { id?: string }) | null>(null);
  const load = () => void edrak.monitors().then(setRows).catch(() => setRows([]));
  useEffect(() => { load(); void edrak.entities().then(setEntities).catch(() => undefined); }, []);
  const save = async () => {
    if (!edit) return;
    await edrak.saveMonitor(edit, edit.id);
    setEdit(null);
    load();
  };
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  return (
    <>
      <PageChrome crumbs={[{ label: L("Monitors", "المراقبة") }]} title={L("Monitors", "المراقبة")} />
      <div className="grid2" style={{ maxWidth: 1180, gridTemplateColumns: edit ? "minmax(0,1fr) 380px" : "1fr" }}>
        <div className="stack gap16">
          <Header eyebrow={L("Monitors", "المراقبة")} title={L("Runs that repeat by themselves.", "تحليلات تتكرر تلقائيًا.")}>
            <button type="button" className="btn ink" onClick={() => setEdit({ ...EMPTY_MONITOR })}><Icon name="plus" size={14} /> {L("New monitor", "مراقبة جديدة")}</button>
          </Header>
          <div className="panel" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr><th>{L("Monitor", "المراقبة")}</th><th>{L("Watching", "تتابع")}</th><th>{L("Schedule", "الجدول")}</th><th>{L("Last run", "آخر تشغيل")}</th><th>{L("Status", "الحالة")}</th><th /></tr></thead>
              <tbody>
                {rows.length === 0 && <tr><td colSpan={6}><div className="empty">{L("No monitors yet.", "لا مراقبات بعد.")}</div></td></tr>}
                {rows.map((m) => (
                  <tr key={m.id}>
                    <td><b>{m.name}</b><div className="s12 muted">{L(...(UC[m.use_case] ?? ["", ""]) as [string, string])}</div></td>
                    <td className="s13">{m.entities.map((e: Dict) => e.name).join(", ") || "—"}</td>
                    <td className="s13">{m.frequency}{m.run_time ? ` · ${m.run_time}` : ""}</td>
                    <td className="s13">{m.last_run_at ? formatWhen(m.last_run_at) : "—"}{m.last_brief && <> · <Link to={`/briefs/${m.last_brief.id}`}>#{no(m.last_brief.number)}</Link></>}</td>
                    <td><span className={`chip ${m.status === "active" ? "c-ok" : "c-draft"}`}>{m.status}</span></td>
                    <td className="row g8">
                      <button type="button" className="btn ghost" onClick={() => void edrak.runMonitor(m.id).then((r) => r.analysis_id && navigate(`/analyses/${r.analysis_id}/plan`))}>{L("Run now", "شغّل الآن")}</button>
                      <button type="button" className="btn ghost" onClick={() => setEdit({ ...EMPTY_MONITOR, ...m, entity_ids: m.entities.map((e: Dict) => e.id), auto_approve_plans: !!m.auto_approve_plans, notify_email: !!m.notify_email, notify_in_app: !!m.notify_in_app })}>{L("Edit", "تعديل")}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        {edit && (
          <aside className="panel stack gap12" style={{ animation: "rise .3s var(--ease) both" }}>
            <b>{edit.id ? L("Edit monitor", "تعديل المراقبة") : L("New monitor", "مراقبة جديدة")}</b>
            <label className="field"><span>{L("Name", "الاسم")}</span><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
            <label className="field"><span>{L("Use case", "حالة الاستخدام")}</span>
              <select className="input" value={edit.use_case} onChange={(e) => setEdit({ ...edit, use_case: e.target.value })}>{Object.entries(UC).map(([k, [en, ar]]) => <option key={k} value={k}>{L(en, ar)}</option>)}</select></label>
            <label className="field"><span>{L("Question (optional)", "السؤال (اختياري)")}</span><input className="input" value={edit.goal ?? ""} onChange={(e) => setEdit({ ...edit, goal: e.target.value })} /></label>
            <div className="field"><span>{L("Watch", "تابع")}</span><div className="row g8 wrap">
              {entities.map((e) => <button key={e.id} type="button" className={`pill ${edit.entity_ids.includes(e.id) ? "b6" : ""}`} style={edit.entity_ids.includes(e.id) ? { borderColor: "var(--blue)" } : undefined} onClick={() => setEdit({ ...edit, entity_ids: toggle(edit.entity_ids, e.id) })}>{e.name}</button>)}
            </div></div>
            <div className="field"><span>{L("Focus", "التركيز")}</span><div className="row g8 wrap">
              {[["pricing", "Pricing", "الأسعار"], ["features", "Features", "الميزات"], ["announcements", "Announcements", "الإعلانات"], ["customer_perception", "Customer perception", "رأي العملاء"]].map(([k, en, ar]) => (
                <button key={k} type="button" className={`pill ${edit.focus.includes(k) ? "b6" : ""}`} onClick={() => setEdit({ ...edit, focus: toggle(edit.focus, k) })}>{L(en, ar)}</button>))}
            </div></div>
            <div className="row g8">
              <label className="field grow"><span>{L("Frequency", "التكرار")}</span>
                <select className="input" value={edit.frequency} onChange={(e) => setEdit({ ...edit, frequency: e.target.value })}>
                  {["daily", "weekly", "monthly", "event_only"].map((f) => <option key={f} value={f}>{f}</option>)}</select></label>
              {edit.frequency === "weekly" && <label className="field"><span>{L("Day", "اليوم")}</span>
                <select className="input" value={edit.day_of_week} onChange={(e) => setEdit({ ...edit, day_of_week: Number(e.target.value) })}>{[1, 2, 3, 4, 5, 6, 7].map((d) => <option key={d} value={d}>{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d - 1]}</option>)}</select></label>}
              {edit.frequency !== "event_only" && <label className="field"><span>{L("Time", "الوقت")}</span><input className="input" type="time" value={edit.run_time ?? "08:00"} onChange={(e) => setEdit({ ...edit, run_time: e.target.value })} /></label>}
            </div>
            <label className="field"><span>{L("Alert me when importance is at least", "نبهني عندما تكون الأهمية على الأقل")}</span>
              <select className="input" value={edit.notify_min_importance} onChange={(e) => setEdit({ ...edit, notify_min_importance: e.target.value })}>{["low", "medium", "high"].map((i) => <option key={i} value={i}>{i}</option>)}</select></label>
            <label className="row g8"><input type="checkbox" checked={edit.auto_approve_plans} onChange={(e) => setEdit({ ...edit, auto_approve_plans: e.target.checked })} /> {L("Auto-approve this monitor's plans (the run still stops for review unless verification passes)", "اعتماد خطط هذه المراقبة تلقائيًا")}</label>
            <label className="row g8"><input type="checkbox" checked={edit.status === "active"} onChange={(e) => setEdit({ ...edit, status: e.target.checked ? "active" : "paused" })} /> {L("Active", "نشطة")}</label>
            <div className="row g8" style={{ justifyContent: "space-between" }}>
              {edit.id ? <button type="button" className="btn ghost" onClick={() => void edrak.deleteMonitor(edit.id!).then(() => { setEdit(null); load(); })}>{L("Delete", "حذف")}</button> : <span />}
              <div className="row g8"><button type="button" className="btn ghost" onClick={() => setEdit(null)}>{L("Cancel", "إلغاء")}</button>
                <button type="button" className="btn ink" disabled={!edit.name.trim()} onClick={() => void save()}>{L("Save monitor", "احفظ")}</button></div>
            </div>
          </aside>
        )}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ Alerts */

export function Alerts() {
  const L = useL();
  const { formatWhen } = useI18n();
  const navigate = useNavigate();
  const [data, setData] = useState<{ alerts: Dict[]; digests: Dict[] }>({ alerts: [], digests: [] });
  const [importance, setImportance] = useState("");
  const load = () => void edrak.alerts().then(setData).catch(() => undefined);
  useEffect(load, []);
  const act = async (id: string, action: string) => {
    const r = await edrak.actOnAlert(id, action);
    if (r.analysis_id) navigate(`/analyses/${r.analysis_id}/plan`);
    else load();
  };
  const rows = data.alerts.filter((a) => !importance || a.importance === importance);
  return (
    <>
      <PageChrome crumbs={[{ label: L("Alerts", "التنبيهات") }]} title={L("Alerts", "التنبيهات")} />
      <div className="stack gap16" style={{ maxWidth: 980 }}>
        <Header eyebrow={L("Change feed", "سجل التغييرات")} title={L("What changed since the last run.", "ما الذي تغيّر منذ آخر تشغيل.")}>
          <div className="row g8">
            <select className="input" style={{ width: 160 }} value={importance} onChange={(e) => setImportance(e.target.value)}>
              <option value="">{L("Any importance", "أي أهمية")}</option>{["high", "medium", "low"].map((i) => <option key={i} value={i}>{i}</option>)}</select>
            <button type="button" className="btn ghost" onClick={() => void edrak.readAllAlerts().then(load)}>{L("Mark all read", "تعليم الكل كمقروء")}</button>
          </div>
        </Header>
        {rows.length === 0 && <div className="empty">{L("No alerts. Monitors raise them when a run finds something new.", "لا تنبيهات.")}</div>}
        {rows.map((a) => (
          <div key={a.id} className="panel stack gap8" style={{ opacity: a.status === "read" ? 0.8 : 1 }}>
            <div className="row g8"><span className={`chip ${a.importance === "high" ? "c-bad" : a.importance === "medium" ? "c-ins" : "c-line"}`}>{a.importance}</span>
              <span className="s12 muted">{a.monitor_name ?? ""} · {formatWhen(a.created_at)}</span>{a.status === "unread" && <span className="tag-agent">{L("New", "جديد")}</span>}</div>
            <bdi className="b6">{a.title}</bdi>
            {a.what_changed && <span className="s13" dir="auto">{a.what_changed}</span>}
            {a.why_it_matters && <span className="s13 muted" dir="auto">{a.why_it_matters}</span>}
            <div className="row g8 wrap">
              {a.evidence?.map((e: Dict) => e.source_url ? <a key={e.uid} className="ev" href={e.source_url} target="_blank" rel="noreferrer">E{e.display_no}</a> : null)}
              <div className="grow" />
              {a.brief_id && <button type="button" className="btn ghost" onClick={() => navigate(`/briefs/${a.brief_id}`)}>{L("Open brief", "افتح الموجز")}</button>}
              <button type="button" className="btn ghost" onClick={() => void act(a.id, "escalate")}>{L("Escalate to full analysis", "حوّل إلى تحليل كامل")}</button>
              <button type="button" className="btn ghost" onClick={() => void act(a.id, "dismiss")}>{L("Dismiss", "تجاهل")}</button>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ Watchlist */

export function Watchlist() {
  const L = useL();
  const { id } = useParams();
  const navigate = useNavigate();
  const [rows, setRows] = useState<Dict[]>([]);
  const [name, setName] = useState("");
  const [quick, setQuick] = useState(false);
  const load = () => void edrak.entities().then(setRows).catch(() => setRows([]));
  useEffect(load, []);
  const current = rows.find((r) => r.id === id) ?? null;
  return (
    <>
      <PageChrome crumbs={[{ label: L("Knowledge", "المعرفة") }, { label: L("Watchlist", "قائمة المتابعة"), to: "/watchlist" }, ...(current ? [{ label: current.name }] : [])]} title={L("Watchlist", "قائمة المتابعة")} />
      <div className="grid2" style={{ maxWidth: 1180, gridTemplateColumns: "340px minmax(0,1fr)" }}>
        <div className="stack gap12">
          <Header eyebrow={L("Watchlist", "قائمة المتابعة")} title={L("What Edrak tracks.", "ما يتابعه إدراك.")} />
          <form className="row g8" onSubmit={(e) => { e.preventDefault(); if (name.trim()) void edrak.createEntity({ kind: "competitor", name }).then(() => { setName(""); load(); }); }}>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={L("Add a competitor", "أضف منافسًا")} />
            <button type="submit" className="btn"><Icon name="plus" size={14} /></button>
          </form>
          {rows.map((r) => (
            <button key={r.id} type="button" className="list-row" style={{ background: r.id === id ? "rgba(10,60,240,.06)" : "none", border: 0, cursor: "pointer", textAlign: "start", borderRadius: 8, padding: "10px 8px" }} onClick={() => navigate(`/watchlist/${r.id}`)}>
              <span className="chip c-line">{r.kind}</span><bdi className="grow b6">{r.name}</bdi>{r.discovered_by === "agent" && <span className="tag-agent">AI</span>}
            </button>
          ))}
        </div>
        <div className="panel stack gap12">
          {!current && <div className="empty">{L("Pick an entity to see what Edrak knows about it.", "اختر كيانًا لرؤية ما يعرفه إدراك عنه.")}</div>}
          {current && (
            <>
              <div className="row g8"><h2 style={{ margin: 0 }}>{current.name}</h2><span className="chip c-line">{current.kind}</span></div>
              {current.website_domain && <a href={`https://${current.website_domain}`} target="_blank" rel="noreferrer">{current.website_domain}</a>}
              {current.discovery_reason && <p className="s13 muted" dir="auto">{L("Found by Edrak", "وجده إدراك")}: {current.discovery_reason}</p>}
              {current.summary && <p dir="auto">{current.summary}</p>}
              <div className="stack gap4"><span className="eyebrow">{L("Official accounts", "الحسابات الرسمية")}</span>
                {current.links.length === 0 && <span className="s13 muted">{L("None found yet.", "لم يُعثر على شيء بعد.")}</span>}
                {current.links.map((l: Dict) => <a key={l.url} href={l.url} target="_blank" rel="noreferrer" className="s13">{l.platform}: {l.url}{l.verified ? " ✓" : ""}</a>)}
              </div>
              <span className="s13 muted">{L(`Appears in ${current.appears_in_reports} report(s).`, `يظهر في ${current.appears_in_reports} تقرير.`)}</span>
              <div className="row g8 wrap">
                {current.website_domain && <button type="button" className="btn ghost" onClick={() => void edrak.refreshLinks(current.id).then(load)}>{L("Find official accounts", "ابحث عن الحسابات الرسمية")}</button>}
                <button type="button" className="btn ink" onClick={() => setQuick(true)}><Icon name="plus" size={14} /> {L("New analysis", "تحليل جديد")}</button>
                <button type="button" className="btn ghost" onClick={() => void edrak.track(current.id, false).then(() => { navigate("/watchlist"); load(); })}>{L("Stop tracking", "أوقف المتابعة")}</button>
              </div>
            </>
          )}
        </div>
      </div>
      {quick && current && <QuickAnalysisDrawer entity={current} onClose={() => setQuick(false)} />}
    </>
  );
}

/* ------------------------------------------------------------------ Evidence explorer */

export function EvidenceExplorer() {
  const L = useL();
  const { briefId } = useParams();
  const navigate = useNavigate();
  const [briefs, setBriefs] = useState<Dict[]>([]);
  const [rows, setRows] = useState<Dict[] | null>(null);
  const [sel, setSel] = useState<Dict | null>(null);
  const [filter, setFilter] = useState("");
  useEffect(() => { void edrak.reports().then((b) => { setBriefs(b); if (!briefId && b[0]) navigate(`/evidence/${b[0].id}`, { replace: true }); }); }, [briefId, navigate]);
  useEffect(() => { if (briefId) void edrak.evidence(briefId).then(setRows).catch(() => setRows([])); }, [briefId]);
  const shown = (rows ?? []).filter((r) => !filter || r.worker === filter || r.origin === filter || (filter === "flagged" && r.flags > 0));
  return (
    <>
      <PageChrome crumbs={[{ label: L("Knowledge", "المعرفة") }, { label: L("Evidence", "الأدلة") }]} title={L("Evidence", "الأدلة")} />
      <div className="stack gap12" style={{ maxWidth: 1180 }}>
        <Header eyebrow={L("Evidence explorer", "مستكشف الأدلة")} title={L("Every source behind a brief.", "كل مصدر وراء الموجز.")}>
          <div className="row g8">
            <select className="input" style={{ width: 280 }} value={briefId ?? ""} onChange={(e) => navigate(`/evidence/${e.target.value}`)}>
              {briefs.map((b) => <option key={b.id} value={b.id}>#{no(b.number)} {b.title}</option>)}</select>
            <select className="input" style={{ width: 200 }} value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">{L("All sources", "كل المصادر")}</option><option value="internal">{L("Internal", "داخلي")}</option><option value="external">{L("External", "خارجي")}</option>
              <option value="flagged">{L("Flagged", "مُبلّغ عنه")}</option>
              {["internal_intelligence", "competitor_intelligence", "market_intelligence", "customer_trends"].map((w) => <option key={w} value={w}>{w.replace("_", " ")}</option>)}
            </select>
          </div>
        </Header>
        <div className="grid2">
          <div className="panel" style={{ padding: 0 }}>
            <table className="tbl"><thead><tr><th>ID</th><th>{L("Source", "المصدر")}</th><th>{L("Worker", "العامل")}</th><th>{L("Claims", "الادعاءات")}</th></tr></thead>
              <tbody>
                {rows?.length === 0 && <tr><td colSpan={4}><div className="empty">{L("No sources.", "لا مصادر.")}</div></td></tr>}
                {shown.map((r) => (
                  <tr key={r.uid} onClick={() => setSel(r)} style={{ cursor: "pointer", background: sel?.uid === r.uid ? "rgba(10,60,240,.05)" : undefined }}>
                    <td><span className="ev">E{r.display_no}</span>{r.flags > 0 && <Icon name="warning" size={12} />}</td>
                    <td><bdi className="b6 s13">{r.source_title ?? r.publisher ?? r.source_type}</bdi><div className="s12 muted">{r.origin} · {r.source_type}</div></td>
                    <td className="s12">{r.worker.replace("_", " ")}</td><td>{r.claims}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <aside className="panel stack gap8" style={{ position: "sticky", top: 80 }}>
            {!sel && <span className="muted">{L("Select a source.", "اختر مصدرًا.")}</span>}
            {sel && (
              <>
                <span className="ev" style={{ alignSelf: "flex-start" }}>E{sel.display_no}</span>
                <b dir="auto">{sel.source_title}</b>
                {sel.source_url?.startsWith("http") && <a href={sel.source_url} target="_blank" rel="noreferrer" className="s12">{L("Open source", "افتح المصدر")} <Icon name="external" size={11} /></a>}
                <span className="eyebrow">{L("Extracted fact", "الحقيقة المستخرجة")}</span><span className="s13" dir="auto">{sel.extracted_fact}</span>
                <span className="eyebrow">{L("Claims that depend on it", "ادعاءات تعتمد عليه")}</span>
                {sel.used_in.map((u: Dict, i: number) => <span key={i} className="s13" dir="auto">· {u.statement}</span>)}
                <div className="row g8">
                  <button type="button" className="btn ghost" onClick={() => void edrak.annotate(briefId!, { evidence_uid: sel.uid, kind: "flag_wrong" }).then(() => void edrak.evidence(briefId!).then(setRows))}>{L("Flag as wrong", "أبلغ أنه خاطئ")}</button>
                  <button type="button" className="btn ghost" onClick={() => { const body = prompt(L("Note", "ملاحظة")); if (body) void edrak.annotate(briefId!, { evidence_uid: sel.uid, kind: "note", body }); }}>{L("Add note", "أضف ملاحظة")}</button>
                </div>
              </>
            )}
          </aside>
        </div>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ Decision log */

export function Decisions() {
  const L = useL();
  const { formatDate } = useI18n();
  const [rows, setRows] = useState<Dict[] | null>(null);
  useEffect(() => { void edrak.decisions().then(setRows).catch(() => setRows([])); }, []);
  return (
    <>
      <PageChrome crumbs={[{ label: L("Knowledge", "المعرفة") }, { label: L("Decision log", "سجل القرارات") }]} title={L("Decision log", "سجل القرارات")} />
      <div className="stack gap12" style={{ maxWidth: 980 }}>
        <Header eyebrow={L("Decision log", "سجل القرارات")} title={L("What was decided, and on what evidence.", "ما الذي تقرر، وعلى أي أدلة.")} />
        {rows?.length === 0 && <div className="empty">{L("No decisions recorded yet. Record one from a brief.", "لا قرارات بعد.")}</div>}
        {rows?.map((d) => (
          <div key={d.id} className="panel stack gap8">
            <div className="row g8 wrap"><span className={`chip ${d.status === "decided" ? "c-dec" : "c-draft"}`}>{d.status}</span><span className="s12 muted">{formatDate(d.recorded_at)} · {L(...(UC[d.use_case] ?? ["", ""]) as [string, string])}</span>
              <div className="grow" /><Link to={`/briefs/${d.brief_id}`} className="s13">{L("Open brief", "افتح الموجز")} #{no(d.brief_no)}</Link></div>
            <b dir="auto">{d.option_label ? `${d.option_label}. ` : ""}{d.title}</b>
            {d.rationale && <span className="s13" dir="auto">{d.rationale}</span>}
            {d.options_considered?.length > 0 && <span className="s12 muted">{L("Options considered", "الخيارات المدروسة")}: {d.options_considered.map((o: Dict) => `${o.key}. ${o.title}`).join(" · ")}</span>}
            {d.next_steps.length > 0 && <div className="stack gap4">{d.next_steps.map((s: Dict) => <span key={s.id} className="s13">→ {s.body}</span>)}</div>}
            <span className="s12 muted"><Icon name="lock" size={11} /> {L(`${d.source_count ?? 0} sources locked`, `${d.source_count ?? 0} مصدر مقفل`)}{d.review_on ? ` · ${L("review on", "المراجعة في")} ${d.review_on}` : ""}{d.owner_name ? ` · ${d.owner_name}` : ""}</span>
          </div>
        ))}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ Settings */

export function Settings() {
  const L = useL();
  const { setLang } = useI18n();
  const [s, setS] = useState<Dict | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { void edrak.settings().then(setS); }, []);
  if (!s) return <PageChrome crumbs={[{ label: L("Settings", "الإعدادات") }]} />;
  const save = async (patch: Dict) => {
    setS(await edrak.saveSettings(patch));
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };
  const EVENTS: [string, string, string][] = [["plan_awaiting_approval", "A plan waits for approval", "خطة بانتظار الموافقة"], ["run_awaiting_review", "A run waits for my review", "تشغيل بانتظار مراجعتي"], ["run_finished", "A run finishes", "انتهاء التشغيل"], ["high_importance_alert", "High-importance alert", "تنبيه عالي الأهمية"], ["weekly_digest", "Weekly digest", "الملخص الأسبوعي"]];
  return (
    <>
      <PageChrome crumbs={[{ label: L("Settings", "الإعدادات") }]} title={L("Settings", "الإعدادات")} />
      <div className="stack gap16" style={{ maxWidth: 760 }}>
        <Header eyebrow={L("Settings", "الإعدادات")} title={L("Your account and workspace.", "حسابك ومساحة عملك.")}>{saved && <span className="chip c-ok">{L("Saved", "تم الحفظ")}</span>}</Header>
        <section className="panel stack gap12"><b>{L("Account", "الحساب")}</b>
          <label className="field"><span>{L("Name", "الاسم")}</span><input className="input" defaultValue={s.account?.full_name} onBlur={(e) => void save({ full_name: e.target.value })} /></label>
          <label className="field"><span>{L("Job title", "المسمى الوظيفي")}</span><input className="input" defaultValue={s.account?.job_title ?? ""} onBlur={(e) => void save({ job_title: e.target.value })} /></label>
          <label className="field"><span>{L("Workspace name", "اسم مساحة العمل")}</span><input className="input" defaultValue={s.workspace?.name} onBlur={(e) => void save({ workspace_name: e.target.value })} /></label>
          <span className="s12 muted">{s.account?.email}</span>
        </section>
        <section className="panel stack gap8"><b>{L("Notifications", "الإشعارات")}</b>
          <table className="tbl"><thead><tr><th /><th>{L("Email", "البريد")}</th><th>{L("In app", "داخل التطبيق")}</th></tr></thead>
            <tbody>{EVENTS.map(([k, en, ar]) => (
              <tr key={k}><td>{L(en, ar)}</td>
                {(["email", "in_app"] as const).map((ch) => <td key={ch}><input type="checkbox" checked={!!s.notifications?.[k]?.[ch]} onChange={(e) => void save({ notifications: { ...s.notifications, [k]: { ...s.notifications?.[k], [ch]: e.target.checked } } })} /></td>)}</tr>
            ))}</tbody></table>
          <span className="s12 muted">{L("Email is sent once a mail server is configured; in-app notifications always work.", "يُرسل البريد عند إعداد خادم البريد.")}</span>
        </section>
        <section className="panel stack gap12"><b>{L("Language and region", "اللغة والمنطقة")}</b>
          <select className="input" value={s.preferences?.language} onChange={(e) => { setLang(e.target.value as "en" | "ar"); void save({ language: e.target.value }); }}><option value="en">English</option><option value="ar">العربية</option></select>
          <input className="input" defaultValue={s.preferences?.time_zone} onBlur={(e) => void save({ time_zone: e.target.value })} />
        </section>
        <section className="panel stack gap8"><b>{L("Automation", "الأتمتة")}</b>
          <label className="row g8"><input type="checkbox" checked={!!s.preferences?.auto_approve_scheduled_plans} onChange={(e) => void save({ auto_approve_scheduled_plans: e.target.checked })} /> {L("Auto-approve plans for my scheduled runs", "اعتماد خطط التشغيلات المجدولة تلقائيًا")}</label>
          <label className="row g8"><input type="checkbox" checked={!!s.preferences?.notify_when_run_finishes} onChange={(e) => void save({ notify_when_run_finishes: e.target.checked })} /> {L("Notify me when a run finishes", "أبلغني عند انتهاء التشغيل")}</label>
        </section>
        <section className="panel stack gap8"><b>{L("Export defaults", "إعدادات التصدير")}</b>
          <select className="input" value={s.export?.export_format} onChange={(e) => void save({ export_format: e.target.value })}><option value="pdf">PDF</option><option value="docx">Word</option><option value="markdown">Markdown</option></select>
          <label className="row g8"><input type="checkbox" checked={!!s.export?.export_include_sources} onChange={(e) => void save({ export_include_sources: e.target.checked })} /> {L("Include sources", "تضمين المصادر")}</label>
          <label className="row g8"><input type="checkbox" checked={!!s.export?.export_include_limitations} onChange={(e) => void save({ export_include_limitations: e.target.checked })} /> {L("Include limitations", "تضمين القيود")}</label>
        </section>
      </div>
    </>
  );
}
