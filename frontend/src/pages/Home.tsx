import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PageChrome } from "../components/shell/AppShell";
import { StatusChip } from "../components/ui/Chip";
import { Icon } from "../components/ui/Icon";
import { useI18n } from "../i18n";
import { useL } from "../lib/useL";
import { edrak, type Dict } from "../services/edrak";
import { useAuth } from "../state/auth";
import type { AnalysisStatus } from "../types/app";

const USE_CASES = [
  ["competitive_intelligence", "Competitive intelligence", "الاستخبارات التنافسية", "What are competitors doing, and what does it mean for us?", "ماذا يفعل المنافسون، وماذا يعني ذلك لنا؟", "target", "Led by the competitor worker", "يقوده عامل المنافسين"],
  ["market_entry_expansion", "Market entry and expansion", "دخول السوق والتوسع", "What does entering this market look like for us?", "كيف يبدو دخول هذا السوق بالنسبة لنا؟", "globe", "Led by the market worker", "يقوده عامل السوق"],
  ["product_launch", "Product launch", "إطلاق منتج", "What does launching this offering look like for us?", "كيف يبدو إطلاق هذا العرض بالنسبة لنا؟", "sparkle", "Led by the customer worker", "يقوده عامل العملاء"],
] as const;

export function Home() {
  const L = useL();
  const { formatWhen, formatDate } = useI18n();
  const { session } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState<Dict | null>(null);
  const [goal, setGoal] = useState("");

  useEffect(() => {
    void edrak.home().then(setData).catch(() => setData({ needs_you: [], alerts: [], monitors: [], counts: {} }));
  }, []);

  const start = (useCase?: string) => {
    const params = new URLSearchParams();
    if (useCase) params.set("use_case", useCase);
    if (goal.trim()) params.set("goal", goal.trim());
    navigate(`/analyses/new?${params}`);
  };

  const action = (r: Dict): [string, string] => {
    if (r.status === "awaiting_approval") return [L("Review plan", "راجع الخطة"), `/analyses/${r.request_id}/plan`];
    if (r.status === "awaiting_review") return [L("Review findings", "راجع النتائج"), `/analyses/${r.request_id}/run`];
    if (["running", "paused", "planning"].includes(r.status)) return [L("Watch", "تابع"), `/analyses/${r.request_id}/run`];
    if (r.brief_id) return [r.open_gaps ? L("Review gaps", "راجع الفجوات") : L("Open brief", "افتح الموجز"), `/briefs/${r.brief_id}`];
    return [L("Open", "افتح"), `/analyses/${r.request_id}/plan`];
  };
  const c = data?.counts ?? {};

  return (
    <>
      <PageChrome crumbs={[{ label: L("Home", "الرئيسية") }]} title={L("Home", "الرئيسية")} />
      <div className="stack gap24" style={{ maxWidth: 1180 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 12 }}>
          <div>
            <div className="eyebrow">{formatDate(new Date().toISOString())}</div>
            <h1 style={{ margin: "6px 0 0" }}>{data?.first_time ? L(`Welcome, ${session?.user.name.split(" ")[0] ?? ""}.`, `مرحبًا ${session?.user.name.split(" ")[0] ?? ""}.`) : L("What do you want to investigate?", "ما الذي تريد التحقيق فيه؟")}</h1>
          </div>
          <div className="row g8">
            {c.plans_waiting > 0 && <span className="chip c-await">{L(`${c.plans_waiting} plan(s) waiting`, `${c.plans_waiting} خطة بانتظارك`)}</span>}
            {c.reviews_waiting > 0 && <span className="chip c-await">{L(`${c.reviews_waiting} run(s) to review`, `${c.reviews_waiting} تشغيل للمراجعة`)}</span>}
            {c.briefs_to_review > 0 && <span className="chip c-blue">{L(`${c.briefs_to_review} brief(s) to review`, `${c.briefs_to_review} موجز للمراجعة`)}</span>}
          </div>
        </div>

        <form className="panel stack gap12" onSubmit={(e) => { e.preventDefault(); start(); }}>
          <textarea className="textarea" style={{ border: 0, fontFamily: "Newsreader, Georgia, serif", fontSize: 20, minHeight: 80, background: "transparent" }}
            placeholder={L("Describe the decision you are facing. For example: we are considering adding an AI customer-support product for SMEs.", "صف القرار الذي تواجهه. مثال: نفكر في إضافة منتج دعم عملاء بالذكاء الاصطناعي للشركات الصغيرة.")}
            value={goal} onChange={(e) => setGoal(e.target.value)} dir="auto"
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); start(); } }} />
          <div className="row g8 wrap" style={{ justifyContent: "space-between" }}>
            <span className="s13 muted">{L("Edrak drafts a plan first. Nothing runs until you approve it.", "يكتب إدراك خطة أولًا. لا شيء يعمل قبل موافقتك.")}</span>
            <button type="submit" className="btn blue">{L("Draft plan", "اكتب الخطة")} <span className="kbd">⌘↵</span></button>
          </div>
        </form>

        <div className="grid3">
          {USE_CASES.map(([key, en, ar, qEn, qAr, icon, ledEn, ledAr]) => (
            <button key={key} type="button" className="panel stack gap8" style={{ textAlign: "start", cursor: "pointer" }} onClick={() => start(key)}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="sq" style={{ background: "var(--ink)", color: "var(--paper)", width: 32, height: 32, borderRadius: 8, display: "inline-flex", alignItems: "center", justifyContent: "center" }}><Icon name={icon} size={16} /></span>
                <span className="s12 muted">{L(ledEn, ledAr)}</span>
              </div>
              <b>{L(en, ar)}</b>
              <span className="s13 muted">{L(qEn, qAr)}</span>
            </button>
          ))}
        </div>

        <div className="grid2">
          <section className="stack gap8">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h3 style={{ margin: 0 }}>{L("Needs you", "بانتظارك")}</h3>
              <Link to="/reports" className="s13">{L("All work", "كل الأعمال")}</Link>
            </div>
            {!data && <div className="empty">{L("Loading…", "جارٍ التحميل…")}</div>}
            {data && data.needs_you.length === 0 && (
              <div className="empty">{data.first_time ? L("Run your first analysis: describe a decision above or pick a use case.", "ابدأ أول تحليل: صف قرارًا أعلاه أو اختر حالة استخدام.") : L("Nothing waits for you right now.", "لا شيء بانتظارك الآن.")}</div>
            )}
            {data?.needs_you.map((r: Dict) => {
              const [label, to] = action(r);
              return (
                <div key={r.request_id} className="list-row">
                  <div className="grow stack gap4" style={{ minWidth: 0 }}>
                    <bdi className="b6 ellip">{r.title}</bdi>
                    <span className="s12 muted">{formatWhen(r.updated_at)}{r.tasks ? ` · ${r.tasks} ${L("tasks", "مهام")}` : ""}{r.open_gaps ? ` · ${r.open_gaps} ${L("gaps", "فجوات")}` : ""}</span>
                  </div>
                  <StatusChip status={r.status as AnalysisStatus} />
                  <button type="button" className="btn" onClick={() => navigate(to)}>{label}</button>
                </div>
              );
            })}
          </section>
          <aside className="stack gap16">
            <section className="stack gap8">
              <div className="row" style={{ justifyContent: "space-between" }}><h3 style={{ margin: 0 }}>{L("Latest alerts", "أحدث التنبيهات")}</h3><Link to="/alerts" className="s13">{L("Change feed", "سجل التغييرات")}</Link></div>
              {(data?.alerts ?? []).length === 0 && <div className="empty">{L("No alerts yet. Monitors raise them when something changes.", "لا تنبيهات بعد.")}</div>}
              {(data?.alerts ?? []).map((a: Dict) => (
                <Link key={a.id} to="/alerts" className="list-row" style={{ color: "inherit" }}>
                  <span className={`chip ${a.importance === "high" ? "c-bad" : "c-line"}`}>{a.importance}</span>
                  <bdi className="grow s13">{a.title}</bdi>
                </Link>
              ))}
            </section>
            <section className="stack gap8">
              <div className="row" style={{ justifyContent: "space-between" }}><h3 style={{ margin: 0 }}>{L("Monitors", "المراقبة")}</h3><Link to="/monitors" className="s13">{L("Manage", "إدارة")}</Link></div>
              {(data?.monitors ?? []).length === 0 && <div className="empty">{L("Set a monitor to rerun an analysis every week.", "أنشئ مراقبة لإعادة التحليل أسبوعيًا.")}</div>}
              {(data?.monitors ?? []).map((m: Dict) => (
                <div key={m.id} className="list-row"><span style={{ width: 8, height: 8, borderRadius: 8, background: m.status === "active" ? "#1f9d55" : "#aaa" }} /><bdi className="grow s13">{m.name}</bdi><span className="s12 muted">{m.frequency}</span></div>
              ))}
            </section>
          </aside>
        </div>
      </div>
    </>
  );
}
