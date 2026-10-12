import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PageChrome } from "../components/shell/AppShell";
import { Icon } from "../components/ui/Icon";
import { useAsync } from "../lib/hooks";
import { useL } from "../lib/useL";
import { edrak } from "../services/edrak";
import { INDUSTRIES } from "./Onboarding";

/** One part of the profile, with an Edit link that opens that onboarding step and returns here. */
function Section({ title, step, children, wide = false }: { title: string; step: number; children: ReactNode; wide?: boolean }) {
  const L = useL();
  return (
    <section className={`card pad profile-sec ${wide ? "wide" : ""}`}>
      <div className="row jb"><span className="eyebrow">{title}</span>
        <Link className="btn ghost sm" to={`/onboarding?step=${step}&return=/company`}><Icon name="pencil" size={13} /> {L("Edit", "تعديل")}</Link></div>
      {children}
    </section>
  );
}

function Tags({ items, tone, empty }: { items: string[]; tone?: string; empty: string }) {
  if (!items.length) return <span className="s13 t3">{empty}</span>;
  return <div className="row g8 wrap">{items.map((x) => <span key={x} className={`tag ${tone ?? ""}`} dir="auto">{x}</span>)}</div>;
}

/** The company profile created during onboarding: the baseline every analysis compares against. */
export function CompanyProfile() {
  const L = useL();
  const navigate = useNavigate();
  const data = useAsync(() => edrak.onboarding(), []);
  const docs = useAsync(() => edrak.documents(), []);
  const p = data.data?.profile;
  const chrome = <PageChrome crumbs={[{ label: L("Company profile", "ملف الشركة") }]} title={L("Company profile", "ملف الشركة")} />;

  if (!p) return <>{chrome}<div className="page-pad stack gap16" aria-busy="true"><div className="shim" style={{ height: 120, borderRadius: 16 }} /><div className="shim" style={{ height: 260, borderRadius: 16 }} /></div></>;

  const industries = (p.industries ?? []).map((k) => {
    const i = INDUSTRIES.find((x) => x[0] === k);
    return k === "other" ? p.industry_other || L("Other", "أخرى") : i ? L(i[1], i[2]) : k;
  });
  const serves = p.markets.filter((m) => m.relation === "serves").map((m) => m.name);
  const considers = p.markets.filter((m) => m.relation === "considers").map((m) => m.name);
  const socials = p.links.filter((l) => l.platform !== "website");

  return (
    <>
      {chrome}
      <div className="page-pad profile">
        <header className="profile-hero card pad">
          <span className="profile-mark">{(p.name.trim()[0] ?? "?").toUpperCase()}</span>
          <div className="stack gap8 grow" style={{ minWidth: 0 }}>
            <div className="row g10 wrap">
              <h1 className="disp" style={{ margin: 0, fontSize: 34 }} dir="auto">{p.name || L("Your company", "شركتك")}</h1>
              {p.completed ? <span className="chip c-ok"><Icon name="check" size={12} /> {L("Profile complete", "الملف مكتمل")}</span>
                : <span className="chip c-ins">{L("Setup not finished", "الإعداد غير مكتمل")}</span>}
            </div>
            {p.aliases.length > 0 && <span className="s13 t2">{L("Also known as", "يُعرف أيضًا باسم")} {p.aliases.join(", ")}</span>}
            <p className="t2 lh14" style={{ margin: 0, maxWidth: 820 }} dir="auto">{p.description || L("No description yet.", "لا وصف بعد.")}</p>
            <div className="row g8 wrap">{industries.map((x) => <span key={x} className="chip c-line">{x}</span>)}</div>
          </div>
          <div className="stack gap8" style={{ alignItems: "flex-end" }}>
            <button type="button" className="btn ink" onClick={() => navigate("/analyses/new")}><Icon name="plus" size={15} /> {L("New analysis", "تحليل جديد")}</button>
            <Link className="btn ghost sm" to="/onboarding?step=1&return=/company"><Icon name="pencil" size={13} /> {L("Edit basics", "عدّل الأساسيات")}</Link>
          </div>
        </header>

        <div className="profile-grid">
          <Section title={L("What you offer", "ما تقدمه")} step={2}>
            <span className="s12 t3">{L("Products and services", "المنتجات والخدمات")}</span>
            <Tags items={p.offerings} empty={L("None yet", "لا شيء بعد")} />
            <span className="s12 t3" style={{ marginTop: 6 }}>{L("Target customers", "العملاء المستهدفون")}</span>
            <Tags items={p.target_customer_groups ?? []} empty={L("None yet", "لا شيء بعد")} />
          </Section>

          <Section title={L("Markets", "الأسواق")} step={3}>
            <span className="s12 t3 row g6"><i className="dot" style={{ background: "var(--ok)" }} /> {L("Sell today in", "تبيع اليوم في")}</span>
            <div className="tag-group tone-ok"><Tags items={serves} tone="tag-item" empty={L("None yet", "لا شيء بعد")} /></div>
            <span className="s12 t3 row g6" style={{ marginTop: 6 }}><i className="dot" style={{ background: "var(--blue)" }} /> {L("Considering", "تفكر في")}</span>
            <div className="tag-group tone-blue"><Tags items={considers} tone="tag-item" empty={L("None yet", "لا شيء بعد")} /></div>
          </Section>

          <Section title={L("Competitors you track", "المنافسون الذين تتابعهم")} step={4} wide>
            {p.competitors.length === 0 ? <span className="s13 t3">{L("No competitors yet. Edrak can find them for you.", "لا منافسين بعد. يمكن لإدراك إيجادهم لك.")}</span> : (
              <div className="comp-list">
                {p.competitors.map((c) => (
                  <div key={c.name} className="comp-row">
                    <span className="comp-sq">{c.name[0]?.toUpperCase()}</span>
                    <div className="stack grow" style={{ minWidth: 0 }}>
                      <div className="row g8"><b dir="auto">{c.name}</b>{c.discovered_by === "agent" && <span className="chip c-blue">{L("Found by Edrak", "وجده إدراك")}</span>}</div>
                      {c.website_domain && <a className="s12" href={`https://${c.website_domain}`} target="_blank" rel="noreferrer">{c.website_domain}</a>}
                      {c.discovery_reason && <span className="s12 t2 clamp2" dir="auto">{c.discovery_reason}</span>}
                      {c.links.length > 0 && <span className="s12 t3">{c.links.map((l) => (l.platform === "x" ? "X" : l.platform)).join(" · ")}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Section>

          <Section title={L("Web and social", "الموقع والتواصل")} step={5}>
            {p.website ? <a className="s13" href={p.website.startsWith("http") ? p.website : `https://${p.website}`} target="_blank" rel="noreferrer">{p.website}</a> : <span className="s13 t3">{L("No website", "لا موقع")}</span>}
            <div className="stack gap4">{socials.map((l) => <a key={l.platform} className="s13 row g6" href={l.url} target="_blank" rel="noreferrer"><span className="t3" style={{ width: 80, textTransform: "capitalize" }}>{l.platform === "x" ? "X" : l.platform}</span><span className="ellip">{l.url}</span></a>)}</div>
          </Section>

          <Section title={L("Strategic goals", "الأهداف الاستراتيجية")} step={6}>
            {p.goals.length === 0 ? <span className="s13 t3">{L("No goals yet", "لا أهداف بعد")}</span>
              : <ol className="goal-list">{p.goals.map((g) => <li key={g} dir="auto">{g}</li>)}</ol>}
          </Section>

          <Section title={L("Your documents", "مستنداتك")} step={7}>
            {(docs.data ?? []).length === 0 ? <span className="s13 t3">{L("No documents uploaded.", "لا مستندات مرفوعة.")}</span> : (
              <div className="stack gap4">{(docs.data ?? []).slice(0, 6).map((d) => <div key={d.document_id} className="row g8 s13"><Icon name="file" size={14} /><bdi className="grow ellip">{d.filename}</bdi><span className="s12 t3">{d.status}</span></div>)}</div>
            )}
            <Link className="s13" to="/sources">{L("Manage data sources", "إدارة مصادر البيانات")}</Link>
          </Section>
        </div>
      </div>
    </>
  );
}
