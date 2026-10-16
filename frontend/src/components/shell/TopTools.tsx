import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { edrak, type Dict } from "../../services/edrak";
import { useL } from "../../lib/useL";
import { useI18n } from "../../i18n";
import { Icon } from "../ui/Icon";
import { ThinkingMark } from "../ui/ThinkingMark";
import { ChatPanel } from "../chat/ChatPanel";
import { modKey } from "../../lib/platform";

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
        <Icon name="search" size={15} />
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
        <span className="kbd">{modKey()}K</span>
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

/** Ask Edrak over the workspace, from anywhere in the app. On a brief, the brief's own docked chat is used instead. */
export function AskEdrak() {
  const L = useL();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const onBrief = /^\/briefs\/[^/]+/.test(pathname);

  useEffect(() => {
    if (onBrief) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onBrief]);

  if (onBrief) return null;
  return (
    <>
      {!open && (
        <button type="button" className="ask ask-fab" onClick={() => setOpen(true)}>
          <span className="ask-fab-mark"><ThinkingMark size={16} live={false} color="#F4F3EE" /></span>
          {L("Ask Edrak", "اسأل إدراك")} <span className="kbd dark">{modKey()}J</span>
        </button>
      )}
      {open && <ChatPanel variant="floating" scope={{}} onClose={() => setOpen(false)} />}
    </>
  );
}
