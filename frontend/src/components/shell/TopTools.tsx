import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { edrak, type Dict } from "../../services/edrak";
import { useL } from "../../lib/useL";
import { useI18n } from "../../i18n";
import { Icon } from "../ui/Icon";

/** Cmd+K search across briefs, analyses, watchlist, documents, decisions and sources. */
export function GlobalSearch() {
  const L = useL();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<Record<string, Dict[]>>({});
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        input.current?.focus();
        setOpen(true);
      }
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (q.trim().length < 2) {
      setResults({});
      return;
    }
    const id = setTimeout(() => void edrak.search(q).then(setResults).catch(() => setResults({})), 220);
    return () => clearTimeout(id);
  }, [q]);

  const go = (to: string) => {
    setOpen(false);
    setQ("");
    navigate(to);
  };
  const groups: Array<[string, string, (r: Dict) => [string, string]]> = [
    ["briefs", L("Briefs", "الموجزات"), (r) => [`Brief ${String(r.number).padStart(4, "0")} · ${r.title}`, `/briefs/${r.id}`]],
    ["analyses", L("Analyses", "التحليلات"), (r) => [r.title, `/analyses/${r.request_id}/plan`]],
    ["entities", L("Watchlist", "قائمة المتابعة"), (r) => [r.name, `/watchlist/${r.id}`]],
    ["documents", L("Documents", "المستندات"), (r) => [r.name, `/sources?doc=${r.id}`]],
    ["decisions", L("Decisions", "القرارات"), (r) => [r.title, `/decisions`]],
    ["sources", L("Sources", "المصادر"), (r) => [`E${r.display_no} · ${r.source_title ?? ""}`, `/evidence/${r.brief_id}`]],
  ];
  const any = groups.some(([k]) => (results[k] ?? []).length);

  return (
    <div className="search-box">
      <label className="search" style={{ minWidth: 260 }}>
        <Icon name="name" size={15} />
        <input
          ref={input}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder={L("Search reports, competitors, sources", "ابحث في التقارير والمنافسين والمصادر")}
          aria-label={L("Search", "بحث")}
          style={{ border: 0, background: "transparent", outline: "none", flex: 1, font: "inherit" }}
        />
        <span className="kbd">⌘K</span>
      </label>
      {open && q.trim().length >= 2 && (
        <div className="popover search-results">
          {!any && <div className="empty" style={{ margin: 12 }}>{L("Nothing found.", "لا نتائج.")}</div>}
          {groups.map(([key, label, view]) =>
            (results[key] ?? []).length ? (
              <div key={key} style={{ padding: "8px 12px" }}>
                <div className="eyebrow">{label}</div>
                {(results[key] ?? []).map((r, i) => {
                  const [text, to] = view(r);
                  return (
                    <button key={i} type="button" className="list-row" style={{ width: "100%", background: "none", border: 0, cursor: "pointer", textAlign: "start" }} onClick={() => go(to)}>
                      <bdi className="ellip">{text}</bdi>
                    </button>
                  );
                })}
              </div>
            ) : null,
          )}
        </div>
      )}
    </div>
  );
}

/** The bell: unread notifications, with links to what each is about. */
export function NotificationBell() {
  const L = useL();
  const { formatWhen } = useI18n();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<{ items: Dict[]; unread: number }>({ items: [], unread: 0 });
  const [tab, setTab] = useState<"all" | "approvals" | "alerts">("all");

  const load = () => void edrak.notifications().then(setData).catch(() => undefined);
  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [pathname]);

  const target = (n: Dict) =>
    n.brief_id ? `/briefs/${n.brief_id}` : n.alert_id ? "/alerts" : n.document_id ? "/sources" :
    n.request_id ? (n.kind === "run_awaiting_review" ? `/analyses/${n.request_id}/run` : `/analyses/${n.request_id}/plan`) : "/";
  const items = data.items.filter((n) =>
    tab === "all" ? true : tab === "alerts" ? n.kind === "alert" : ["plan_awaiting_approval", "run_awaiting_review"].includes(n.kind),
  );

  return (
    <div style={{ position: "relative" }}>
      <button type="button" className="iconbtn" aria-label={L("Notifications", "الإشعارات")} onClick={() => setOpen((o) => !o)}>
        <Icon name="info" size={17} />
        {data.unread > 0 && <span className="pip" />}
      </button>
      {open && (
        <div className="popover" role="dialog">
          <div className="row" style={{ justifyContent: "space-between", padding: "12px 14px", borderBottom: "1px solid var(--line)" }}>
            <b>{L("Notifications", "الإشعارات")}</b>
            <button type="button" className="btn ghost" style={{ height: 28 }} onClick={() => void edrak.readNotifications().then(load)}>
              {L("Mark all read", "تعليم الكل كمقروء")}
            </button>
          </div>
          <div className="row g8" style={{ padding: "8px 14px" }}>
            {(["all", "approvals", "alerts"] as const).map((t) => (
              <button key={t} type="button" className={`pill ${tab === t ? "b6" : ""}`} onClick={() => setTab(t)}>
                {t === "all" ? L("All", "الكل") : t === "approvals" ? L("Approvals", "الموافقات") : L("Alerts", "التنبيهات")}
              </button>
            ))}
          </div>
          {items.length === 0 && <div className="empty" style={{ margin: 14 }}>{L("You are all caught up.", "لا جديد.")}</div>}
          {items.map((n) => (
            <button
              key={n.id}
              type="button"
              className="list-row"
              style={{ width: "100%", background: n.read_at ? "none" : "rgba(10,60,240,.04)", border: 0, cursor: "pointer", textAlign: "start", padding: "10px 14px" }}
              onClick={() => {
                void edrak.readNotifications(n.id).then(load);
                setOpen(false);
                navigate(target(n));
              }}
            >
              <div className="stack gap4" style={{ minWidth: 0 }}>
                <bdi className="b6 s13">{n.title}</bdi>
                {n.body && <bdi className="s12 muted ellip">{n.body}</bdi>}
                <span className="s12 muted">{formatWhen(n.created_at)}</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Ask Edrak: a chat panel over the workspace (or the open brief), opened with Cmd+J. */
export function AskEdrak() {
  const L = useL();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const [thread, setThread] = useState<string | null>(null);
  const [scopeKey, setScopeKey] = useState("");
  const [msgs, setMsgs] = useState<Dict[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const briefId = pathname.match(/^\/briefs\/([^/]+)/)?.[1];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const send = async () => {
    const content = text.trim();
    if (!content || busy) return;
    setBusy(true);
    setText("");
    setMsgs((m) => [...m, { role: "user", content }]);
    try {
      let id = thread;
      const key = briefId ?? "workspace";
      if (!id || key !== scopeKey) {
        id = (await edrak.newThread(briefId ? { scope: "brief", brief_id: briefId } : { scope: "workspace" })).thread_id;
        setThread(id);
        setScopeKey(key);
      }
      const reply = await edrak.ask(id, content);
      setMsgs((m) => [...m, { role: "assistant", content: reply.content }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", content: (e as Error).message || L("Something went wrong.", "حدث خطأ.") }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button type="button" className="btn ink ask-fab" style={{ position: "fixed", insetInlineEnd: 24, bottom: 22, zIndex: 40, borderRadius: 999 }} onClick={() => setOpen(true)}>
        <Icon name="sparkle" size={16} /> {L("Ask Edrak", "اسأل إدراك")} <span className="kbd">⌘J</span>
      </button>
      {open && (
        <div className="ask-panel" role="dialog" aria-label="Ask Edrak">
          <div className="row" style={{ justifyContent: "space-between", padding: "14px 16px", borderBottom: "1px solid var(--line)" }}>
            <div className="stack">
              <b>{L("Ask Edrak", "اسأل إدراك")}</b>
              <span className="s12 muted">{briefId ? L("About this brief", "عن هذا الموجز") : L("About your workspace", "عن مساحة العمل")}</span>
            </div>
            <button type="button" className="iconbtn" onClick={() => setOpen(false)} aria-label={L("Close", "إغلاق")}><Icon name="x" size={16} /></button>
          </div>
          <div className="ask-msgs">
            {msgs.length === 0 && (
              <div className="stack gap8">
                {[L("What changed with our competitors this month?", "ما الذي تغيّر لدى المنافسين هذا الشهر؟"),
                  L("Which claims in this brief rest on one source?", "أي الادعاءات تعتمد على مصدر واحد؟"),
                  L("What do customers complain about most?", "ما أكثر ما يشتكي منه العملاء؟")].map((s) => (
                  <button key={s} type="button" className="pill" style={{ justifyContent: "flex-start" }} onClick={() => setText(s)}>{s}</button>
                ))}
              </div>
            )}
            {msgs.map((m, i) => <div key={i} className={`msg ${m.role}`} dir="auto">{m.content}</div>)}
            {busy && <div className="msg assistant muted">{L("Reading your evidence…", "جارٍ قراءة الأدلة…")}</div>}
          </div>
          <form className="row g8" style={{ padding: 12, borderTop: "1px solid var(--line)" }} onSubmit={(e) => { e.preventDefault(); void send(); }}>
            <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder={L("Ask about your briefs and sources", "اسأل عن الموجزات والمصادر")} dir="auto" />
            <button type="submit" className="btn blue" disabled={busy || !text.trim()}>{L("Ask", "اسأل")}</button>
          </form>
        </div>
      )}
    </>
  );
}
