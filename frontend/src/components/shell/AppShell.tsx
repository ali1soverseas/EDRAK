import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { Link, Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useI18n } from "../../i18n";
import { useAuth } from "../../state/auth";
import type { Lang } from "../../types/app";
import { useMediaQuery } from "../../lib/useMediaQuery";
import { Icon } from "../ui/Icon";
import { Logo } from "../ui/Logo";
import { Segmented } from "../ui/Controls";
import { DotField } from "../ui/DotField";
import { AskEdrak, GlobalSearch, NotificationBell } from "./TopTools";
import { useL } from "../../lib/useL";

export interface Crumb {
  label: string;
  /** Crumbs without a target are plain text. The last crumb is the current page. */
  to?: string;
}

export interface ChromeConfig {
  crumbs: Crumb[];
  /** Narrow icon-only sidebar. The brief uses it to give the cards room. */
  rail?: boolean;
  /** Remove the page padding and own the scrolling. The brief does. */
  flush?: boolean;
  /** Browser tab title, without the product name. */
  title?: string;
}

const ChromeContext = createContext<Dispatch<SetStateAction<ChromeConfig>> | null>(null);

/** Rendered by each screen to say what the top bar and sidebar should show. */
export function PageChrome({ crumbs, rail = false, flush = false, title }: ChromeConfig) {
  const setChrome = useContext(ChromeContext);
  const key = crumbs.map((crumb) => `${crumb.label}|${crumb.to ?? ""}`).join("›");
  useEffect(() => {
    setChrome?.({ crumbs, rail, flush, title });
    // `key` stands for `crumbs`, which is a new array on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setChrome, key, rail, flush, title]);
  return null;
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? (parts[parts.length - 1][0] ?? "") : "")).toUpperCase() || "?";
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

export function LanguageSwitch() {
  const { lang, setLang, t } = useI18n();
  return (
    <Segmented<Lang>
      label={t("common.language")}
      value={lang}
      onChange={setLang}
      options={[
        { value: "en", label: "EN", ariaLabel: "English" },
        { value: "ar", label: "ع", ariaLabel: "العربية", style: { fontFamily: "'IBM Plex Sans Arabic'" } },
      ]}
    />
  );
}

function TopBar({ crumbs, menuOpen, onMenu }: { crumbs: Crumb[]; menuOpen: boolean; onMenu: () => void }) {
  const { t } = useI18n();
  return (
    <header className="top">
      <button type="button" className="iconbtn nav-toggle" aria-label={t("nav.menu")} aria-expanded={menuOpen} onClick={onMenu}>
        <Icon name="menu" size={18} />
      </button>
      <nav className="crumb" aria-label="Breadcrumb">
        {crumbs.map((crumb, index) => {
          const last = index === crumbs.length - 1;
          const label = last ? (
            <b aria-current="page">
              <bdi>{crumb.label}</bdi>
            </b>
          ) : crumb.to ? (
            <Link to={crumb.to}>
              <bdi>{crumb.label}</bdi>
            </Link>
          ) : (
            <span>
              <bdi>{crumb.label}</bdi>
            </span>
          );
          return (
            <span key={`${crumb.label}-${index}`} className="crumb-item">
              {index > 0 && <Icon name="chevron" size={14} flip />}
              {label}
            </span>
          );
        })}
      </nav>
      <div className="grow" />
      <GlobalSearch />
      <LanguageSwitch />
      <NotificationBell />
    </header>
  );
}

function Sidebar({ rail, open }: { rail: boolean; open: boolean }) {
  const { t } = useI18n();
  const { session, signOut } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const L = useL();
  if (!session) return null;

  const { user, workspace } = session;
  const at = (prefix: string) => pathname === prefix || pathname.startsWith(prefix + "/");
  const links = [
    { to: "/", icon: "home" as const, label: L("Home", "الرئيسية"), on: pathname === "/" || at("/analyses") },
    { to: "/reports", icon: "fileLines" as const, label: L("Reports", "التقارير"), on: at("/reports") || at("/briefs") },
    { to: "/monitors", icon: "activity" as const, label: L("Monitors", "المراقبة"), on: at("/monitors") },
    { to: "/alerts", icon: "warning" as const, label: L("Alerts", "التنبيهات"), on: at("/alerts") },
  ];
  const knowledge = [
    { to: "/watchlist", icon: "target" as const, label: L("Watchlist", "قائمة المتابعة"), on: at("/watchlist") },
    { to: "/evidence", icon: "layers" as const, label: L("Evidence", "الأدلة"), on: at("/evidence") },
    { to: "/decisions", icon: "checks" as const, label: L("Decision log", "سجل القرارات"), on: at("/decisions") },
    { to: "/sources", icon: "building" as const, label: L("Data sources", "مصادر البيانات"), on: at("/sources") || at("/company") },
  ];

  return (
    <aside className={`side ${open ? "open" : ""}`.trim()}>
      <Link to="/" aria-label="edrak" className={rail ? "brand rail" : "brand"}>
        {rail ? <Logo variant="mark" height={30} style={{ alignSelf: "center" }} /> : <Logo variant="full" height={26} style={{ alignSelf: "flex-start" }} />}
      </Link>

      {!rail && (
        <div className="ws">
          <span className="sq">{(workspace.name.trim()[0] ?? "E").toUpperCase()}</span>
          <div className="grow" style={{ lineHeight: 1.25, minWidth: 0 }}>
            <div className="b6 s13 ellip" dir="auto">{workspace.name}</div>
            <div className="s12 t3">{t("nav.workspace")}</div>
          </div>
        </div>
      )}

      {rail ? (
        <button type="button" className="btn ink icon" style={{ width: 44, height: 44 }} aria-label={t("nav.newAnalysis")} title={t("nav.newAnalysis")} onClick={() => navigate("/analyses/new")}>
          <Icon name="plus" size={20} />
        </button>
      ) : (
        <button type="button" className="btn ink" style={{ width: "100%", height: 44, justifyContent: "space-between" }} onClick={() => navigate("/analyses/new")}>
          <span className="row g8">
            <Icon name="plus" size={18} />
            {t("nav.newAnalysis")}
          </span>
          <span className="kbd" aria-hidden="true">N</span>
        </button>
      )}

      <nav className="nav" style={{ flex: 1 }} aria-label={t("nav.main")}>
        {links.map((link) => (
          <Link key={link.to} to={link.to} className={link.on ? "on" : ""} title={link.label} aria-current={link.on ? "page" : undefined}>
            <Icon name={link.icon} size={19} />
            {!rail && <span>{link.label}</span>}
          </Link>
        ))}
        {!rail && <div className="eyebrow" style={{ margin: "14px 10px 6px" }}>{L("Knowledge", "المعرفة")}</div>}
        {knowledge.map((link) => (
          <Link key={link.to} to={link.to} className={link.on ? "on" : ""} title={link.label} aria-current={link.on ? "page" : undefined}>
            <Icon name={link.icon} size={19} />
            {!rail && <span>{link.label}</span>}
          </Link>
        ))}
        <div style={{ flex: 1 }} />
        <Link to="/settings" className={at("/settings") ? "on" : ""} title={L("Settings", "الإعدادات")}>
          <Icon name="shield" size={19} />
          {!rail && <span>{L("Settings", "الإعدادات")}</span>}
        </Link>
      </nav>

      {rail ? (
        <span className="av" style={{ width: 36, height: 36, background: "#0B0D38", color: "#F4F3EE", fontSize: 14 }} title={user.name}>
          {initials(user.name)}
        </span>
      ) : (
        <div className="me">
          <span className="av" style={{ width: 34, height: 34, background: "#0B0D38", color: "#F4F3EE", fontSize: 13 }}>
            {initials(user.name)}
          </span>
          <div className="grow" style={{ lineHeight: 1.25, minWidth: 0 }}>
            <div className="b6 s13 ellip" dir="auto">{user.name}</div>
            <div className="s12 t3 ellip" dir="auto">{user.title ?? user.email}</div>
          </div>
          <button
            type="button"
            className="signout"
            title={t("nav.signOut")}
            aria-label={t("nav.signOut")}
            onClick={() => void signOut().then(() => navigate("/sign-in"))}
          >
            <Icon name="signOut" size={16} />
          </button>
        </div>
      )}
    </aside>
  );
}

/** Sidebar, top bar and page area for every signed-in screen. */
export function ShellLayout(): ReactNode {
  const { session } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [chrome, setChrome] = useState<ChromeConfig>({ crumbs: [] });
  // Below 900 px the sidebar shrinks to icons. Below 640 px it leaves the layout and slides in
  // from the top bar's menu button.
  const phone = useMediaQuery("(max-width: 640px)");
  const narrow = useMediaQuery("(max-width: 900px)");
  const rail = !phone && (Boolean(chrome.rail) || narrow);
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => setNavOpen(false), [location.pathname, phone]);
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setNavOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navOpen]);

  // N opens a new analysis, as the key hint on the button says.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== "n" || event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTyping(event.target) || document.querySelector(".ovl")) return;
      event.preventDefault();
      navigate("/analyses/new");
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navigate]);

  useEffect(() => {
    document.title = chrome.title ? `${chrome.title} · Edrak` : "Edrak";
  }, [chrome.title]);

  const context = useMemo(() => setChrome, []);

  if (session === undefined) return <div className="boot" aria-busy="true" />;
  if (session === null) return <Navigate to="/sign-in" replace state={{ from: `${location.pathname}${location.search}` }} />;
  // Nothing works well without the company profile: finish onboarding first.
  if (session.onboarding_complete === false) return <Navigate to="/onboarding" replace />;

  return (
    <ChromeContext.Provider value={context}>
      <div className={`app ${rail ? "rail" : ""} ${navOpen ? "nav-open" : ""}`.trim()}>
        <DotField />
        <Sidebar rail={rail} open={navOpen} />
        {navOpen && <div className="nav-scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />}
        <div className="main">
          <TopBar crumbs={chrome.crumbs} menuOpen={navOpen} onMenu={() => setNavOpen((value) => !value)} />
          <main className={`page ${chrome.flush ? "flush" : ""}`.trim()}>
            <Outlet />
          </main>
        </div>
        <AskEdrak />
      </div>
    </ChromeContext.Provider>
  );
}
