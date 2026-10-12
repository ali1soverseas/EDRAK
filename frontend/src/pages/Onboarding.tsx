import { useEffect, useMemo, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { LanguageSwitch } from "../components/shell/AppShell";
import { TagInput } from "../components/ui/Controls";
import { DotField } from "../components/ui/DotField";
import { Icon } from "../components/ui/Icon";
import { Logo } from "../components/ui/Logo";
import { useL } from "../lib/useL";
import { edrak, type Candidate, type Doc, type Link as SocialLink, type Profile } from "../services/edrak";
import { useAuth } from "../state/auth";

const INDUSTRIES = [
  ["fintech", "Fintech", "تقنية مالية"], ["saas", "SaaS", "برمجيات كخدمة"], ["retail", "Retail", "تجزئة"],
  ["logistics", "Logistics", "لوجستيات"], ["health", "Health", "صحة"], ["other", "Something else", "مجال آخر"],
] as const;
const PLATFORMS = ["x", "facebook", "instagram", "linkedin", "youtube", "tiktok"] as const;
const STEPS = 8;

type Draft = Pick<Profile, "name" | "aliases" | "industry" | "industry_other" | "description" | "offerings" | "target_customers" | "goals" | "website"> & {
  serves: string[];
  considers: string[];
  links: Record<string, string>;
  competitors: Array<{ name: string; website?: string | null; links?: SocialLink[]; discovered_by?: string; discovery_reason?: string | null; discovery_confidence?: number | null }>;
};

function fromProfile(p: Profile): Draft {
  return {
    name: p.name, aliases: p.aliases, industry: p.industry, industry_other: p.industry_other, description: p.description,
    offerings: p.offerings, target_customers: p.target_customers, goals: p.goals, website: p.website,
    serves: p.markets.filter((m) => m.relation === "serves").map((m) => m.name),
    considers: p.markets.filter((m) => m.relation === "considers").map((m) => m.name),
    links: Object.fromEntries(p.links.filter((l) => l.platform !== "website").map((l) => [l.platform, l.url])),
    competitors: p.competitors.map((c) => ({
      name: c.name, website: c.website_domain ? `https://${c.website_domain}` : null, links: c.links,
      discovered_by: c.discovered_by, discovery_reason: c.discovery_reason, discovery_confidence: c.discovery_confidence,
    })),
  };
}

export function Onboarding() {
  const L = useL();
  const navigate = useNavigate();
  const { session, refresh } = useAuth();
  const [step, setStep] = useState(1);
  const [d, setD] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [found, setFound] = useState<Candidate[] | null>(null);
  const [finding, setFinding] = useState(false);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [compName, setCompName] = useState("");

  useEffect(() => {
    void edrak.onboarding().then((o) => {
      setD(fromProfile(o.profile));
      setStep(Math.min(Math.max(o.step, 1), STEPS));
    });
    void edrak.documents().then(setDocs).catch(() => undefined);
  }, []);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setD((cur) => (cur ? { ...cur, [key]: value } : cur));

  const payload = useMemo(() => {
    if (!d) return {};
    return {
      name: d.name, aliases: d.aliases, industry: d.industry, industry_other: d.industry_other, description: d.description,
      offerings: d.offerings, target_customers: d.target_customers, goals: d.goals, website: d.website,
      markets: [...d.serves.map((name) => ({ name, relation: "serves" })), ...d.considers.map((name) => ({ name, relation: "considers" }))],
      links: Object.entries(d.links).filter(([, url]) => url.trim()).map(([platform, url]) => ({ platform, url })),
      competitors: d.competitors,
    };
  }, [d]);

  if (session === null) return <Navigate to="/sign-in" replace />;
  if (!d) return <div className="boot" aria-busy="true" />;

  const canNext = [
    true,
    d.name.trim().length > 1 && !!d.industry,
    d.description.trim().length > 5 && d.offerings.length > 0,
    d.serves.length + d.considers.length > 0,
    true, true, true, true, true,
  ][step];

  const save = async (next: number) => {
    setSaving(true);
    setError(null);
    try {
      await edrak.saveOnboarding({ ...payload, step: next });
      setStep(next);
      window.scrollTo({ top: 0 });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const finish = async () => {
    setSaving(true);
    try {
      await edrak.saveOnboarding({ ...payload, step: STEPS });
      await edrak.completeOnboarding();
      await refresh();
      navigate("/", { replace: true });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const discover = async () => {
    setFinding(true);
    setError(null);
    try {
      await edrak.saveOnboarding({ ...payload, step });
      setFound(await edrak.discover(6));
    } catch (e) {
      setError((e as Error).message || L("Could not search right now.", "تعذر البحث الآن."));
    } finally {
      setFinding(false);
    }
  };

  const titles: Record<number, [string, string, string, string]> = {
    1: ["Your company", "شركتك", "Edrak compares everything against this baseline.", "يقارن إدراك كل شيء بهذا الأساس."],
    2: ["What you offer", "ما تقدمه", "Your products or services, and who they are for.", "منتجاتك أو خدماتك ولمن هي."],
    3: ["Your markets", "أسواقك", "Where you sell today, and where you are considering.", "أين تبيع اليوم وأين تفكر في التوسع."],
    4: ["Competitors", "المنافسون", "Name them, or let Edrak find them from your profile.", "اذكرهم، أو دع إدراك يجدهم من ملفك."],
    5: ["Web and social", "الموقع والتواصل", "Your website and official accounts. The customer worker reads what people say there.", "موقعك وحساباتك الرسمية. يقرأ عامل العملاء ما يقوله الناس هناك."],
    6: ["Strategic goals", "الأهداف الاستراتيجية", "What you are trying to achieve this year.", "ما تسعى لتحقيقه هذا العام."],
    7: ["Your documents", "مستنداتك", "Optional: strategy notes, tickets, pricing sheets. Only you can read them.", "اختياري: ملاحظات واستراتيجيات وتذاكر وأسعار. لا يراها غيرك."],
    8: ["Review and finish", "مراجعة وإنهاء", "Check what Edrak will work from. You can change it later in Data sources.", "راجع ما سيعتمد عليه إدراك. يمكنك تغييره لاحقًا."],
  };
  const [tEn, tAr, sEn, sAr] = titles[step];

  return (
    <div className="onb">
      <DotField opacity={0.8} />
      <div className="onb-top">
        <div className="onb-bar"><i style={{ width: `${(step / STEPS) * 100}%` }} /></div>
        <div className="onb-head">
          <Logo variant="full" height={24} />
          <div className="row g12">
            <span className="eyebrow">{L(`Step ${step} of ${STEPS}`, `الخطوة ${step} من ${STEPS}`)}</span>
            <LanguageSwitch />
          </div>
        </div>
      </div>

      <div className="onb-body">
        <div key={step} className="onb-slide">
          <span className="eyebrow">{L("Company setup", "إعداد الشركة")}</span>
          <h2>{L(tEn, tAr)}</h2>
          <p className="muted" style={{ marginBottom: 24 }}>{L(sEn, sAr)}</p>

          {step === 1 && (
            <div className="stack gap16">
              <label className="field"><span>{L("Company name", "اسم الشركة")}</span>
                <input className="input" autoFocus value={d.name} onChange={(e) => set("name", e.target.value)} dir="auto" /></label>
              <TagInput values={d.aliases} onChange={(v) => set("aliases", v)} label={L("Also known as", "يُعرف أيضًا باسم")} addLabel={L("Add a name", "أضف اسمًا")} />
              <div className="field"><span>{L("Industry", "المجال")}</span>
                <div className="row g8 wrap">
                  {INDUSTRIES.map(([key, en, ar]) => (
                    <button key={key} type="button" className={`pill ${d.industry === key ? "b6" : ""}`} style={d.industry === key ? { borderColor: "var(--blue)", color: "var(--blue)" } : undefined} onClick={() => set("industry", key)}>{L(en, ar)}</button>
                  ))}
                </div>
                {d.industry === "other" && <input className="input" placeholder={L("Which industry?", "أي مجال؟")} value={d.industry_other ?? ""} onChange={(e) => set("industry_other", e.target.value)} />}
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="stack gap16">
              <label className="field"><span>{L("What does the company do?", "ماذا تفعل الشركة؟")}</span>
                <textarea className="textarea" value={d.description} onChange={(e) => set("description", e.target.value)} dir="auto" /></label>
              <TagInput values={d.offerings} onChange={(v) => set("offerings", v)} label={L("Products and services", "المنتجات والخدمات")} addLabel={L("Add an offering", "أضف منتجًا")} />
              <label className="field"><span>{L("Target customers", "العملاء المستهدفون")}</span>
                <input className="input" value={d.target_customers} onChange={(e) => set("target_customers", e.target.value)} placeholder={L("e.g. SMEs in Egypt", "مثال: الشركات الصغيرة في مصر")} dir="auto" /></label>
            </div>
          )}

          {step === 3 && (
            <div className="stack gap16">
              <TagInput values={d.serves} onChange={(v) => set("serves", v)} label={L("Markets you serve", "الأسواق التي تخدمها")} addLabel={L("Add a market", "أضف سوقًا")} />
              <TagInput values={d.considers} onChange={(v) => set("considers", v)} label={L("Markets you are considering", "أسواق تفكر فيها")} addLabel={L("Add a market", "أضف سوقًا")} />
            </div>
          )}

          {step === 4 && (
            <div className="stack gap16">
              <div className="row g8">
                <input className="input" value={compName} onChange={(e) => setCompName(e.target.value)} placeholder={L("Competitor name", "اسم المنافس")} dir="auto"
                  onKeyDown={(e) => { if (e.key === "Enter" && compName.trim()) { e.preventDefault(); set("competitors", [...d.competitors, { name: compName.trim() }]); setCompName(""); } }} />
                <button type="button" className="btn" disabled={!compName.trim()} onClick={() => { set("competitors", [...d.competitors, { name: compName.trim() }]); setCompName(""); }}><Icon name="plus" size={15} /> {L("Add", "أضف")}</button>
              </div>
              <button type="button" className="btn blue" disabled={finding} onClick={() => void discover()} style={{ alignSelf: "flex-start" }}>
                <Icon name="sparkle" size={15} /> {finding ? L("Searching the web…", "جارٍ البحث…") : L("Find competitors for me", "ابحث عن المنافسين لي")}
              </button>
              {found && found.length === 0 && <div className="empty">{L("No clear competitors found. Add them by name.", "لم نجد منافسين واضحين. أضفهم بالاسم.")}</div>}
              {found && found.filter((c) => !d.competitors.some((x) => x.name.toLowerCase() === c.name.toLowerCase())).map((c) => (
                <div key={c.name} className="panel row g12" style={{ alignItems: "flex-start" }}>
                  <div className="grow stack gap4">
                    <div className="row g8"><b>{c.name}</b><span className="tag-agent">{L("Found by Edrak", "وجده إدراك")}</span><span className="s12 muted">{Math.round(c.confidence * 100)}%</span></div>
                    {c.website && <a className="s12" href={c.website} target="_blank" rel="noreferrer">{c.website}</a>}
                    <span className="s13 muted" dir="auto">{c.reason}</span>
                    {c.links.length > 0 && <span className="s12 muted">{L("Official accounts:", "الحسابات الرسمية:")} {c.links.map((l) => l.platform).join(", ")}</span>}
                  </div>
                  <button type="button" className="btn" onClick={() => set("competitors", [...d.competitors, { name: c.name, website: c.website, links: c.links, discovered_by: "agent", discovery_reason: c.reason, discovery_confidence: c.confidence }])}>{L("Keep", "احتفظ")}</button>
                </div>
              ))}
              {d.competitors.length > 0 && (
                <div className="stack gap8">
                  <span className="eyebrow">{L("You will track", "ستتابع")}</span>
                  <div className="row g8 wrap">
                    {d.competitors.map((c) => (
                      <span key={c.name} className="pill">
                        <bdi>{c.name}</bdi>{c.discovered_by === "agent" && <span className="tag-agent">AI</span>}
                        <button type="button" aria-label={L("Remove", "إزالة")} onClick={() => set("competitors", d.competitors.filter((x) => x.name !== c.name))}><Icon name="x" size={12} /></button>
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {step === 5 && (
            <div className="stack gap12">
              <label className="field"><span>{L("Website", "الموقع الإلكتروني")}</span>
                <input className="input" value={d.website} onChange={(e) => set("website", e.target.value)} placeholder="https://" dir="ltr" /></label>
              {PLATFORMS.map((p) => (
                <label key={p} className="field"><span style={{ textTransform: "capitalize" }}>{p === "x" ? "X" : p}</span>
                  <input className="input" dir="ltr" placeholder={`https://${p === "x" ? "x" : p}.com/…`} value={d.links[p] ?? ""} onChange={(e) => set("links", { ...d.links, [p]: e.target.value })} /></label>
              ))}
            </div>
          )}

          {step === 6 && (
            <TagInput values={d.goals} onChange={(v) => set("goals", v)} label={L("Goals", "الأهداف")} addLabel={L("Add a goal", "أضف هدفًا")} />
          )}

          {step === 7 && (
            <div className="stack gap12">
              <label className="panel row g12" style={{ borderStyle: "dashed", cursor: "pointer" }}>
                <Icon name="upload" size={20} />
                <span className="grow">{L("Choose files: PDF, Word, Excel, CSV, Markdown, text", "اختر ملفات: PDF أو Word أو Excel أو CSV أو نص")}</span>
                <input type="file" multiple hidden accept=".pdf,.docx,.xlsx,.csv,.md,.txt,.json"
                  onChange={(e) => { const files = Array.from(e.target.files ?? []); if (files.length) void edrak.upload(files).then(() => edrak.documents().then(setDocs)); }} />
              </label>
              {docs.map((doc) => (
                <div key={doc.document_id} className="list-row"><Icon name="file" size={16} /><bdi className="grow ellip">{doc.filename}</bdi><span className="s12 muted">{doc.status}</span></div>
              ))}
            </div>
          )}

          {step === 8 && (
            <div className="stack gap8">
              {[[L("Company", "الشركة"), d.name], [L("Offerings", "المنتجات"), d.offerings.join(", ")], [L("Target customers", "العملاء"), d.target_customers],
                [L("Markets", "الأسواق"), [...d.serves, ...d.considers].join(", ")], [L("Competitors", "المنافسون"), d.competitors.map((c) => c.name).join(", ")],
                [L("Goals", "الأهداف"), d.goals.join("; ")], [L("Documents", "المستندات"), String(docs.length)]].map(([k, v]) => (
                <div key={k} className="list-row"><span className="muted" style={{ width: 150 }}>{k}</span><bdi className="grow">{v || "—"}</bdi></div>
              ))}
            </div>
          )}

          {error && <p className="s13" style={{ color: "var(--red, #c0392b)", marginTop: 12 }}>{error}</p>}
          <div className="onb-actions">
            <button type="button" className="btn ghost" disabled={step === 1 || saving} onClick={() => void save(step - 1)}>{L("Back", "رجوع")}</button>
            <div className="row g8">
              {step >= 5 && step < STEPS && <button type="button" className="btn ghost" onClick={() => void save(step + 1)}>{L("Skip", "تخطَّ")}</button>}
              {step < STEPS ? (
                <button type="button" className="btn ink" disabled={!canNext || saving} onClick={() => void save(step + 1)}>{L("Continue", "متابعة")} <Icon name="arrow" size={15} flip /></button>
              ) : (
                <button type="button" className="btn blue" disabled={saving} onClick={() => void finish()}>{L("Finish setup", "إنهاء الإعداد")}</button>
              )}
            </div>
          </div>
        </div>

        <aside className="onb-preview">
          <div className="panel ink stack gap8" style={{ position: "sticky", top: 90 }}>
            <span className="eyebrow" style={{ color: "#9aa0d0" }}>{L("How Edrak will read this", "كيف سيقرأ إدراك هذا")}</span>
            <b style={{ fontSize: 20 }} dir="auto">{d.name || L("Your company", "شركتك")}</b>
            {d.description && <span className="s13" dir="auto" style={{ opacity: .85 }}>{d.description}</span>}
            {d.offerings.length > 0 && <span className="s13">{L("Offers", "يقدم")}: {d.offerings.join(", ")}</span>}
            {d.serves.length > 0 && <span className="s13">{L("Sells in", "يبيع في")}: {d.serves.join(", ")}</span>}
            {d.competitors.length > 0 && <span className="s13">{L("Compared with", "يُقارن مع")}: {d.competitors.map((c) => c.name).join(", ")}</span>}
            <span className="s12" style={{ opacity: .6, marginTop: 8 }}>{L("Baseline for the Internal worker and every comparison.", "الأساس لعامل البيانات الداخلية ولكل مقارنة.")}</span>
          </div>
        </aside>
      </div>
    </div>
  );
}
