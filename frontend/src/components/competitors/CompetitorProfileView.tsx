import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Icon } from "../ui/Icon";
import { useL } from "../../lib/useL";
import {
  edrak,
  type CompetitorKnowledge,
  type KbAspect,
  type KbAspectFact,
  type KbHit,
  type KbNews,
  type KbOffering,
  type KbPassage,
  type KbPlan,
  type KbPolicy,
  type KbProfile,
  type KbSource,
  type PolicyTopic,
  type Provenance,
  type SourcedField,
} from "../../services/edrak";

type Lfn = (en: string, ar: string) => string;
type Tab = "overview" | "profile" | "offerings" | "pricing" | "news" | "sources" | "ask";

const PAGE_TYPE: Record<string, [string, string]> = {
  home: ["Homepage", "الصفحة الرئيسية"],
  pricing: ["Pricing", "الأسعار"],
  product: ["Products", "المنتجات"],
  policy: ["Policies", "السياسات"],
  help: ["Help", "المساعدة"],
  about: ["About", "من نحن"],
  news: ["News", "الأخبار"],
  market: ["Market pages", "صفحات السوق"],
  sales: ["How to buy", "طريقة الشراء"],
  other: ["Article", "مقال"],
};

const TOPIC: Record<PolicyTopic, [string, string]> = {
  delivery: ["Delivery", "التوصيل"],
  returns: ["Returns & refunds", "الإرجاع والاسترداد"],
  warranty: ["Warranty", "الضمان"],
  payment: ["Payment", "الدفع"],
  support: ["Support", "الدعم"],
  other: ["Other terms", "شروط أخرى"],
};

/** The pages a competitor profile is expected to have; missing ones are shown as gaps. */
const EXPECTED: [string, string, string][] = [
  ["pricing", "No pricing page on their site", "لا توجد صفحة أسعار في موقعهم"],
  ["product", "No product pages on their site", "لا توجد صفحات منتجات في موقعهم"],
  ["policy", "No policy pages (returns, terms)", "لا توجد صفحات سياسات (إرجاع، شروط)"],
  ["help", "No help or FAQ pages", "لا توجد صفحات مساعدة أو أسئلة شائعة"],
];

const PLATFORM: Record<string, string> = {
  x: "X", linkedin: "LinkedIn", youtube: "YouTube", facebook: "Facebook", instagram: "Instagram", tiktok: "TikTok",
};

const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

/** A stable hue per name, so each competitor keeps its colour everywhere. */
const hueOf = (name: string) => [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const isOfficial = (p: { status?: string }) => p.status === "verified";

function since(iso: string | null | undefined, L: Lfn): string {
  if (!iso) return "";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (Number.isNaN(days)) return "";
  if (days <= 0) return L("Updated today", "حُدّث اليوم");
  if (days === 1) return L("Updated yesterday", "حُدّث أمس");
  return L(`Updated ${days} days ago`, `حُدّث منذ ${days} يومًا`);
}

/** Plain-language reason a site could not be read. */
function issueText(h: string, why: string, L: Lfn): string {
  if (/40[13]|refused|429|503/.test(why)) {
    return L(`${h} blocks automated reading, so some facts come from other sites.`,
      `يحظر ${h} القراءة الآلية، لذا تأتي بعض المعلومات من مواقع أخرى.`);
  }
  if (/robots/.test(why)) return L(`${h} asks crawlers not to read these pages.`, `يطلب ${h} من الزواحف عدم قراءة هذه الصفحات.`);
  return L(`Some pages on ${h} could not be read (${why}).`, `تعذرت قراءة بعض صفحات ${h} (${why}).`);
}

/* ------------------------------------------------------------------ small parts */

/** "From their site": opens to the exact words the item rests on. */
function Evidence({ quote, url, official, asOf, quiet = false }: {
  quote: string; url: string; official: boolean; asOf?: string | null;
  /** A small "Source" link, where the colour around the item already says who published it. */
  quiet?: boolean;
}) {
  const L = useL();
  const [open, setOpen] = useState(false);
  const label = quiet
    ? L("Source", "المصدر")
    : official ? L("From their site", "من موقعهم") : <span>{L("From", "من")} <bdi dir="ltr">{host(url)}</bdi></span>;
  return (
    <span className="kb-ev">
      <button type="button" className={`kb-ev-btn ${official ? "is-official" : "is-third"} ${quiet ? "is-quiet" : ""}`}
        aria-expanded={open} title={quiet ? (official ? L("From their site", "من موقعهم") : host(url)) : undefined} onClick={() => setOpen(!open)}>
        <i className="kb-mark" aria-hidden="true" />
        {label}
      </button>
      {open && (
        <span className="kb-ev-body">
          <q dir="auto">{quote}</q>
          <span className="kb-ev-meta">
            <a href={url} target="_blank" rel="noreferrer">{L("Open the page", "افتح الصفحة")}</a>
            {asOf && <span>{L("read on", "قُرئت في")} {asOf}</span>}
          </span>
        </span>
      )}
    </span>
  );
}

const ev = (p: Omit<Provenance, "status"> & { status?: string }, quiet = false) =>
  <Evidence quote={p.quote} url={p.source_url} official={isOfficial(p)} asOf={p.as_of} quiet={quiet} />;

/* ------------------------------------------------------------------ full profile: the 14 aspects */

const aspectsFound = (p: KbProfile) =>
  Object.values(p.aspects ?? {}).reduce((n, a) => n + Object.values(a.coverage).filter((c) => c !== "not_found").length, 0);

/** "last 3 months" / "last 6 months" / "last 12 months" / older, from the fact's date or when it was read. */
function recency(f: KbAspectFact): [string, string] | null {
  const when = Date.parse(f.date ?? "") || Date.parse(f.as_of ?? "");
  if (!when) return null;
  const months = (Date.now() - when) / (30.4 * 24 * 3600 * 1000);
  if (months <= 3) return ["Last 3 months", "آخر 3 أشهر"];
  if (months <= 6) return ["Last 6 months", "آخر 6 أشهر"];
  if (months <= 12) return ["Last 12 months", "آخر 12 شهرًا"];
  return ["Older", "أقدم"];
}

function AspectCard({ id, aspect, target }: { id: string; aspect: KbAspect; target?: string }) {
  const L = useL();
  const outside = aspect.facts.filter((f) => f.relevance === "outside_scope");
  const total = aspect.items.length;
  const official = aspect.items.filter((i) => aspect.coverage[i.id] === "official").length;
  const reported = aspect.items.filter((i) => aspect.coverage[i.id] === "reported").length;
  const missing = aspect.items.filter((i) => aspect.coverage[i.id] === "not_found");
  const dated = id === "recent_developments";
  return (
    <details className="kb-aspect">
      <summary>
        <span className="kb-aspect-title">{aspect.label}</span>
        <span className="kb-aspect-bar" aria-hidden="true">
          <i className="is-official" style={{ width: `${(official / total) * 100}%` }} />
          <i className="is-third" style={{ width: `${(reported / total) * 100}%` }} />
        </span>
        <span className="kb-aspect-count">{L(`${official + reported} of ${total}`, `${official + reported} من ${total}`)}</span>
      </summary>
      <div className="kb-aspect-body">
        {aspect.items.filter((i) => aspect.coverage[i.id] !== "not_found" ||
          aspect.facts.some((f) => f.item === i.id && f.relevance !== "outside_scope")).map((item) => {
          const facts = aspect.facts.filter((f) => f.item === item.id && f.relevance !== "outside_scope")
            .sort((a, b) => (Date.parse(b.date ?? b.as_of ?? "") || 0) - (Date.parse(a.date ?? a.as_of ?? "") || 0));
          return (
            <div key={item.id} className="kb-aspect-item">
              <h4>{item.label}</h4>
              <ul>
                {facts.map((f, i) => {
                  const r = dated ? recency(f) : null;
                  return (
                    <li key={i}>
                      <span dir="auto">{f.statement}</span>
                      {f.date && <span className="kb-tag">{f.date}</span>}
                      {r && <span className="kb-tag">{L(r[0], r[1])}</span>}
                      {f.domain && <span className="kb-tag kb-domain">{f.domain}</span>}
                      {f.mock || f.status === "mock"
                        ? <span className="kb-tag">{L("Mock data · unverified", "بيانات تجريبية · غير موثقة")}</span>
                        : ev(f, true)}
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
        {missing.filter((m) => !aspect.facts.some((f) => f.item === m.id && f.relevance !== "outside_scope")).length > 0 && (
          <p className="kb-muted kb-small">
            {L("Not found", "لم يُعثر عليه")}{aspect.searches ? L(` (after ${aspect.searches} searches)`, ` (بعد ${aspect.searches} عمليات بحث)`) : ""}:{" "}
            {missing.filter((m) => !aspect.facts.some((f) => f.item === m.id && f.relevance !== "outside_scope"))
              .map((m) => m.label).join(" · ")}
          </p>
        )}
        {outside.length > 0 && (
          <details className="kb-outside">
            <summary>{target
              ? L(`${outside.length} fact${outside.length > 1 ? "s" : ""} outside ${target}'s field`,
                `${outside.length} حقيقة خارج مجال ${target}`)
              : L(`${outside.length} fact${outside.length > 1 ? "s" : ""} outside the field`, `${outside.length} حقيقة خارج المجال`)}</summary>
            <ul>{outside.map((f, i) => (
              <li key={i}>
                <span dir="auto">{f.statement}</span>
                {f.mock || f.status === "mock"
                  ? <span className="kb-tag">{L("Mock data · unverified", "بيانات تجريبية · غير موثقة")}</span>
                  : ev(f, true)}
              </li>
            ))}</ul>
          </details>
        )}
      </div>
    </details>
  );
}

/** Every aspect of the competitor: what is established for each item, with its source, and what is not. */
function FullProfile({ p }: { p: KbProfile }) {
  const L = useL();
  const aspects = Object.entries(p.aspects ?? {});
  if (!aspects.length) {
    return <Empty>{L("The full profile has not been researched yet. Rebuild this competitor to research it.",
      "لم يُبحث الملف الكامل بعد. أعد بناء هذا المنافس للبحث فيه.")}</Empty>;
  }
  const total = aspects.reduce((n, [, a]) => n + a.items.length, 0);
  const scope = p.scope;
  return (
    <div className="kb-stack">
      {scope && scope.domains.length > 0 && (
        <p className="kb-scope-line">
          {L(`Researched as a competitor of ${scope.target}, in its field: `, `بُحث كمنافس لـ ${scope.target} في مجاله: `)}
          {scope.domains.map((d) => <span key={d.name} className="kb-tag kb-domain">{d.name}</span>)}
        </p>
      )}
      <p className="kb-status-line">
        {L(`${aspectsFound(p)} of ${total} profile items established across ${aspects.length} areas.`,
          `${aspectsFound(p)} من ${total} عنصرًا ثابتًا في ${aspects.length} مجالًا.`)}{" "}
        <span className="kb-legend"><i className="is-official" />{L("their site", "موقعهم")}<i className="is-third" />{L("other sites", "مواقع أخرى")}</span>
      </p>
      <div className="kb-aspects">{aspects.map(([id, a]) => <AspectCard key={id} id={id} aspect={a} target={scope?.target} />)}</div>
    </div>
  );
}

/** A titled group inside a tab. Titles name what the group answers, in the user's words. */
function Group({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="kb-group">
      <div className="kb-group-head"><h3>{title}</h3>{aside}</div>
      {children}
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="kb-none">{children}</p>;
}

/* ------------------------------------------------------------------ evidence strip */

/** One bar, one segment per page read: width is how much was kept, colour is who published it. */
function EvidenceStrip({ sources, onPick }: { sources: KbSource[]; onPick?: (url: string) => void }) {
  const L = useL();
  if (!sources.length) return null;
  const ordered = [...sources].sort((a, b) =>
    a.source_type === b.source_type ? b.chunks - a.chunks : a.source_type === "official" ? -1 : 1);
  const own = sources.filter((s) => s.source_type === "official").length;
  const other = sources.length - own;
  return (
    <section className="kb-strip" aria-label={L("Where this profile comes from", "مصادر هذا الملف")}>
      <div className="kb-strip-bar">
        {ordered.map((s) => (
          <button key={s.source_url} type="button" style={{ flexGrow: Math.max(1, s.chunks) }}
            className={`kb-seg ${s.source_type === "official" ? "is-official" : "is-third"}`}
            title={`${s.title || host(s.source_url)} (${host(s.source_url)})`} aria-label={s.title || host(s.source_url)}
            disabled={!onPick} onClick={() => onPick?.(s.source_url)} />
        ))}
      </div>
      <div className="kb-strip-legend">
        <span className="kb-key is-official"><i className="kb-mark" aria-hidden="true" />
          {own === 1 ? L("1 page from their own site", "صفحة واحدة من موقعهم") : L(`${own} pages from their own site`, `${own} صفحات من موقعهم`)}
        </span>
        <span className="kb-key is-third"><i className="kb-mark" aria-hidden="true" />
          {other === 1 ? L("1 page from other sites", "صفحة واحدة من مواقع أخرى") : L(`${other} pages from other sites`, `${other} صفحات من مواقع أخرى`)}
        </span>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ overview */

/** Short facts as tiles: label, value, and where it came from. Unknown facts share one quiet line. */
function KeyFacts({ p }: { p: KbProfile }) {
  const L = useL();
  const kf = p.key_facts ?? {};
  const joined = (items?: SourcedField[]): SourcedField | null =>
    items && items.length ? { ...items[0], value: items.map((i) => i.value).join(", ") } : null;
  const all: [string, SourcedField | null | undefined][] = [
    [L("Founded", "التأسيس"), kf.founded],
    [L("Based in", "المقر"), kf.headquarters],
    [L("People", "الموظفون"), kf.employees],
    [L("Led by", "يقودها"), kf.ceo],
    [L("Ownership", "الملكية"), p.ownership ?? p.parent_company],
    [L("Sells through", "قنوات البيع"), joined(p.channels)],
    [L("Operates in", "تعمل في"), joined(p.markets_served)],
  ];
  const known = all.filter(([, f]) => f) as [string, SourcedField][];
  const unknown = all.filter(([, f]) => !f).map(([label]) => label);
  return (
    <div className="kb-stack-tight">
      {known.length > 0 && (
        <div className={`kb-facts n-${Math.min(known.length, 3)}`}>
          {known.map(([label, f]) => (
            <div key={label} className={`kb-fact-tile ${isOfficial(f) ? "is-official" : "is-third"}`}>
              <span className="kb-fact-label">{label}</span>
              <span className="kb-fact-value" dir="auto">{f.value}</span>
              {ev(f, true)}
            </div>
          ))}
        </div>
      )}
      {unknown.length > 0 && (
        <p className="kb-none">
          {known.length === 0
            ? L("None of these were stated in what we read: ", "لم يُذكر أيٌّ من هذه فيما قرأناه: ")
            : L("Not stated: ", "غير مذكور: ")}
          {unknown.join(", ")}
        </p>
      )}
    </div>
  );
}

/** What they say about themselves, kept visibly as their claims. */
function InTheirWords({ p }: { p: KbProfile }) {
  const L = useL();
  const claims = p.claims ?? [];
  if (!p.tagline && !claims.length) return <Empty>{L("Their pages make no claims about themselves that we could quote.", "لم نجد في صفحاتهم ادعاءات عن أنفسهم يمكن اقتباسها.")}</Empty>;
  return (
    <div className="kb-words">
      {p.tagline && (
        <blockquote className="kb-tagline">
          <p dir="auto">{p.tagline.value}</p>
          {ev(p.tagline, true)}
        </blockquote>
      )}
      {claims.length > 0 && (
        <ul className="kb-claims">
          {claims.map((c) => (
            <li key={c.claim}><span dir="auto">{c.claim}</span>{ev(c, true)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

type Known = "official" | "third" | "missing";
const knownFrom = (items: (Provenance | null | undefined)[]): Known => {
  const real = items.filter(Boolean) as Provenance[];
  if (!real.length) return "missing";
  return real.some(isOfficial) ? "official" : "third";
};

function Row({ label, known, onOpen, children }: { label: string; known: Known; onOpen?: () => void; children?: ReactNode }) {
  const L = useL();
  return (
    <div className={`kb-know is-${known}`}>
      <span className="kb-know-dot" aria-hidden="true">{known !== "missing" && <Icon name="check" size={12} />}</span>
      <span className="kb-know-label">{label}</span>
      <div className="kb-know-value">
        {known === "missing" ? <span className="kb-muted">{L("Not found in what we read", "لم يُذكر فيما قرأناه")}</span> : children}
        {known !== "missing" && onOpen && <button type="button" className="kb-link" onClick={onOpen}>{L("See details", "التفاصيل")}</button>}
      </div>
    </div>
  );
}

function WhatWeKnow({ p, go }: { p: KbProfile; go?: (t: Tab) => void }) {
  const L = useL();
  const offerings = p.offerings ?? [];
  const plans = p.plans ?? [];
  const policies = p.policies ?? [];
  const segments = p.customer_segments ?? [];
  const partners = p.partners ?? [];
  const topics = [...new Set(policies.map((x) => x.topic))];
  return (
    <section className="kb-know-list" aria-label={L("What we know", "ما نعرفه")}>
      <Row label={L("What it sells", "ماذا تبيع")} known={knownFrom(offerings.map((o) => ({ ...o, source_url: o.doc_url, status: o.status ?? "verified" })))}
        onOpen={go && (() => go("offerings"))}>
        <span className="kb-value" dir="auto">
          {offerings.slice(0, 4).map((o) => o.official_name).join(", ")}
          {offerings.length > 4 && <span className="kb-muted"> {L(`and ${offerings.length - 4} more`, `و${offerings.length - 4} أخرى`)}</span>}
        </span>
      </Row>
      <Row label={L("How it charges", "كيف تحاسب")} known={knownFrom([p.pricing_model, ...plans])} onOpen={go && (() => go("pricing"))}>
        {p.pricing_model && <span className="kb-value" dir="auto">{p.pricing_model.value}</span>}
        {plans.length > 0 && <span className="kb-muted">{plans.length === 1 ? L("1 named plan", "باقة واحدة") : L(`${plans.length} named plans`, `${plans.length} باقات`)}</span>}
      </Row>
      <Row label={L("Service terms", "شروط الخدمة")} known={knownFrom(policies)} onOpen={go && (() => go("pricing"))}>
        <span className="kb-value">{topics.map((t) => L(...TOPIC[t])).join(", ")}</span>
      </Row>
      <Row label={L("Who it sells to", "لمن تبيع")} known={knownFrom(segments)}>
        <span className="kb-value" dir="auto">{segments.map((s) => s.value).join("; ")}</span>
      </Row>
      <Row label={L("Partners and customers", "الشركاء والعملاء")} known={knownFrom(partners)}>
        <span className="kb-value" dir="auto">{partners.map((s) => s.value).join(", ")}</span>
      </Row>
    </section>
  );
}

function LatestMoves({ news, limit }: { news: KbNews[]; limit?: number }) {
  const L = useL();
  const items = limit ? news.slice(0, limit) : news;
  if (!items.length) return <Empty>{L("No dated news about them in the pages we read.", "لا أخبار مؤرخة عنهم في الصفحات التي قرأناها.")}</Empty>;
  return (
    <ol className="kb-timeline">
      {items.map((n, i) => (
        <li key={`${n.headline}-${i}`} className={isOfficial(n) ? "is-official" : "is-third"}>
          <span className="kb-when"><bdi>{n.date ?? L("Undated", "بلا تاريخ")}</bdi></span>
          <div className="kb-move">
            <h4 dir="auto">{n.headline}</h4>
            <p dir="auto">{n.summary}</p>
            {ev(n)}
          </div>
        </li>
      ))}
    </ol>
  );
}

/* ------------------------------------------------------------------ offerings */

function OfferingCard({ o }: { o: KbOffering }) {
  const L = useL();
  const features = o.features ?? [];
  return (
    <article className="kb-off">
      <span className="kb-off-cat" dir="auto">{o.category}</span>
      <h4 className="kb-off-name" dir="auto">{o.official_name}</h4>
      {o.summary && <p className="kb-off-sum" dir="auto">{o.summary}</p>}
      {features.length > 0 && (
        <ul className="kb-feats">
          {features.map((f) => <li key={f} dir="auto"><Icon name="check" size={12} />{f}</li>)}
        </ul>
      )}
      {(o.price || o.for_whom) && (
        <dl className="kb-off-facts">
          {o.price && <div><dt>{L("Price", "السعر")}</dt><dd dir="auto">{o.price}</dd></div>}
          {o.for_whom && <div><dt>{L("For", "لـ")}</dt><dd dir="auto">{o.for_whom}</dd></div>}
        </dl>
      )}
      <Evidence quote={o.quote} url={o.doc_url} official={o.status !== "third_party_only"} asOf={o.as_of} />
    </article>
  );
}

/* ------------------------------------------------------------------ AI accelerator intelligence */

type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => !!v && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): string =>
  Array.isArray(v) ? v.map(text).filter(Boolean).join("; ")
  : isRec(v) ? Object.entries(v).map(([k, x]) => [k, text(x)]).filter(([, x]) => x).map(([k, x]) => `${k}: ${x}`).join("; ")
  : v == null ? "" : String(v);
const humanize = (k: string) => k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const isClaim = (v: unknown): v is Rec => isRec(v) && typeof v.claim === "string";
const AI_PRODUCT_KEYS = ["product_catalog", "products_and_platforms"];
const AI_HIDDEN_KEYS = new Set([...AI_PRODUCT_KEYS, "scope_note", "source_urls", "enrichment_metadata"]);

function AiClaim({ c }: { c: Rec }) {
  const meta = [c.relationship, c.deployment_status, c.timing].map(text).filter(Boolean).map((m) => m.replace(/_/g, " "));
  return (
    <article className="kb-plan">
      <p dir="auto">{text(c.claim)}</p>
      {meta.length > 0 && <p className="kb-plan-basis" dir="auto">{meta.join(" · ")}</p>}
      {typeof c.caveat === "string" && c.caveat && <Conflict text={c.caveat} />}
      <Evidence quote={text(c.quote)} url={text(c.source_url)} official={c.status !== "third_party_only"} asOf={text(c.as_of) || null} />
    </article>
  );
}

function AiBlock({ title, value }: { title: string; value: unknown }) {
  let body: ReactNode = null;
  if (Array.isArray(value) && value.length && value.every(isClaim)) {
    body = <div className="kb-off-grid">{value.map((c, i) => <AiClaim key={i} c={c} />)}</div>;
  } else if (isClaim(value)) {
    body = <div className="kb-off-grid"><AiClaim c={value} /></div>;
  } else if (Array.isArray(value)) {
    const items = value.map(text).filter(Boolean);
    body = items.length ? <ul className="kb-feats">{items.map((x) => <li key={x} dir="auto"><Icon name="check" size={12} />{x}</li>)}</ul> : null;
  } else if (isRec(value)) {
    const rows = Object.entries(value).map(([k, x]) => [k, text(x)] as const).filter(([, x]) => x);
    body = rows.length ? (
      <dl className="kb-off-facts">
        {rows.map(([k, x]) => <div key={k}><dt>{humanize(k)}</dt><dd dir="auto">{x}</dd></div>)}
      </dl>
    ) : null;
  } else if (text(value)) {
    body = <p dir="auto">{text(value)}</p>;
  }
  if (!body) return null;
  return <div><h3>{title}</h3>{body}</div>;
}

function AiAccelerator({ data }: { data: Rec }) {
  const L = useL();
  const products = AI_PRODUCT_KEYS.flatMap((k) => (Array.isArray(data[k]) ? (data[k] as unknown[]) : [])).filter(isRec);
  const review = (isRec(data.enrichment_metadata) && Array.isArray(data.enrichment_metadata.review_required)
    ? data.enrichment_metadata.review_required : []).map(text).filter(Boolean);
  return (
    <div className="kb-stack">
      <h3>{L("AI accelerators", "مسرّعات الذكاء الاصطناعي")}</h3>
      {typeof data.scope_note === "string" && data.scope_note && <p className="kb-muted" dir="auto">{data.scope_note}</p>}
      {products.length > 0 && (
        <div className="kb-off-grid">
          {products.map((o, i) => (
            <article key={i} className="kb-off">
              <span className="kb-off-cat" dir="auto">{text(o.category)}</span>
              <h4 className="kb-off-name" dir="auto">{text(o.name)}</h4>
              {text(o.summary) && <p className="kb-off-sum" dir="auto">{text(o.summary)}</p>}
              {isRec(o.specifications) && Object.keys(o.specifications).length > 0 && (
                <dl className="kb-off-facts">
                  {Object.entries(o.specifications).filter(([, x]) => text(x)).map(([k, x]) => (
                    <div key={k}><dt>{humanize(k)}</dt><dd dir="auto">{text(x)}</dd></div>
                  ))}
                </dl>
              )}
              {text(o.availability_status) && (
                <dl className="kb-off-facts"><div><dt>{L("Availability", "التوفر")}</dt><dd dir="auto">{text(o.availability_status)}</dd></div></dl>
              )}
              {Array.isArray(o.notes) && o.notes.length > 0 && (
                <ul className="kb-feats">{o.notes.map(text).filter(Boolean).map((n) => <li key={n} dir="auto"><Icon name="check" size={12} />{n}</li>)}</ul>
              )}
              {text(o.source_url) && <a className="kb-chip" href={text(o.source_url)} target="_blank" rel="noreferrer">{host(text(o.source_url))}</a>}
            </article>
          ))}
        </div>
      )}
      {Object.entries(data).filter(([k]) => !AI_HIDDEN_KEYS.has(k)).map(([k, v]) => <AiBlock key={k} title={humanize(k)} value={v} />)}
      {review.length > 0 && <AiBlock title={L("Still to verify", "قيد التحقق")} value={review} />}
    </div>
  );
}

/* ------------------------------------------------------------------ pricing and terms */

function PlanCard({ plan }: { plan: KbPlan }) {
  const L = useL();
  return (
    <article className="kb-plan">
      <h4 dir="auto">{plan.name}</h4>
      <p className="kb-plan-price" dir="auto">
        {plan.price ?? <span className="kb-muted">{L("Price not public", "السعر غير معلن")}</span>}
        {plan.price && plan.period && <small> {plan.period}</small>}
      </p>
      {!plan.price && plan.period && <p className="kb-plan-basis" dir="auto">{plan.period}</p>}
      {plan.includes.length > 0 && (
        <ul className="kb-feats">{plan.includes.map((x) => <li key={x} dir="auto"><Icon name="check" size={12} />{x}</li>)}</ul>
      )}
      {plan.conflict && <Conflict text={plan.conflict} />}
      {ev(plan)}
    </article>
  );
}

function Terms({ policies }: { policies: KbPolicy[] }) {
  const L = useL();
  const order: PolicyTopic[] = ["returns", "warranty", "payment", "support", "other"];
  const present = order.filter((t) => policies.some((p) => p.topic === t));
  const absent = order.filter((t) => t !== "other" && !present.includes(t));
  return (
    <>
      {present.length === 0 ? (
        <Empty>{L("No returns, warranty, payment or support terms in what we read.", "لا شروط إرجاع أو ضمان أو دفع أو دعم فيما قرأناه.")}</Empty>
      ) : (
        <div className="kb-terms">
          {present.map((t) => (
            <section key={t} className={`kb-term is-${t}`}>
              <h4>{L(...TOPIC[t])}</h4>
              <ul>
                {policies.filter((p) => p.topic === t).map((p, i) => (
                  <li key={i}><span dir="auto">{p.summary}</span>{p.conflict && <Conflict text={p.conflict} />}{ev(p)}</li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
      {present.length > 0 && absent.length > 0 && (
        <p className="kb-muted kb-small">{L("Not stated:", "غير مذكور:")} {absent.map((t) => L(...TOPIC[t])).join(", ")}</p>
      )}
    </>
  );
}

function Conflict({ text }: { text: string }) {
  const L = useL();
  return <span className="kb-conflict" title={text}><Icon name="warning" size={12} /> {L("Sources disagree", "المصادر مختلفة")}</span>;
}

const COVERAGE: Record<string, [string, string, string]> = {
  official: ["is-official", "From their site", "من موقعهم"],
  reported: ["is-third", "Other sites only", "من مواقع أخرى فقط"],
  partial: ["is-partial", "How they charge, no price", "طريقة المحاسبة بلا سعر"],
  not_found: ["is-missing", "Not found", "لم يُعثر عليه"],
};
const PRICING_STATUS: Record<string, [string, string]> = {
  official_price: ["They publish prices.", "ينشرون أسعارهم."],
  reported_price: ["They publish no prices; the prices below are reported by other sites.", "لا ينشرون أسعارًا؛ الأسعار أدناه من مواقع أخرى."],
  quote_required: ["Prices are given on request (a quote).", "الأسعار عند الطلب (عرض سعر)."],
  terms_only: ["How they charge is public; the prices are not.", "طريقة المحاسبة معلنة، والأسعار غير معلنة."],
  not_found: ["No prices or pricing terms were found.", "لم نجد أسعارًا أو شروط تسعير."],
};

/** Pricing, returns and warranty at a glance: answered by them, by others, or not found. */
function Coverage({ p }: { p: KbProfile }) {
  const L = useL();
  const cov = p.terms_coverage;
  if (!cov) return null;
  const rows: [keyof NonNullable<KbProfile["terms_coverage"]>, string, string][] = [
    ["pricing", "Pricing", "الأسعار"], ["returns", "Returns & refunds", "الإرجاع والاسترداد"],
    ["warranty", "Warranty", "الضمان"],
  ];
  return (
    <div className="kb-coverage">
      {rows.map(([key, en, ar]) => {
        const st = cov[key] ?? "not_found";
        const [cls, sen, sar] = COVERAGE[st] ?? COVERAGE.not_found;
        const research = p.terms_research?.[key];
        return (
          <div key={key} className={`kb-cov ${cls}`}>
            <span className="kb-cov-label">{L(en, ar)}</span>
            <span className="kb-cov-state"><i className="kb-mark" aria-hidden="true" />{L(sen, sar)}</span>
            {st === "not_found" && research && (
              <span className="kb-muted kb-small">{L(`after ${research.searches} searches`, `بعد ${research.searches} عمليات بحث`)}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** How they charge, how to buy, plans and terms. When nothing is public, says what was checked. */
function PricingTerms({ p, ask }: { p: KbProfile; ask: (question: string) => void }) {
  const L = useL();
  const conflicts = p.conflicts ?? [];
  const plans = p.plans ?? [];
  const policies = p.policies ?? [];
  const channels = p.channels ?? [];
  const read = p.terms_pages_read ?? [];
  const buyPages = read.filter((x) => x.page_type === "sales" || x.page_type === "pricing");
  const nothing = !p.pricing_model && !plans.length && !policies.length && !channels.length;

  if (nothing) {
    return (
      <div className="kb-stack">
      <Coverage p={p} />
      <div className="kb-terms-empty">
        <h3>{L("No prices or terms found", "لم نجد أسعارًا أو شروطًا")}</h3>
        {read.length > 0 ? (
          <>
            <p>{L("None of these pages states a price, a licensing model or terms of sale:", "لا تذكر أي من هذه الصفحات سعرًا أو نموذج ترخيص أو شروط بيع:")}</p>
            <ul>
              {read.map((x) => (
                <li key={x.url}>
                  <a href={x.url} target="_blank" rel="noreferrer"><bdi>{x.title || host(x.url)}</bdi></a>
                  <span className="kb-tag">{L(...(PAGE_TYPE[x.page_type] ?? PAGE_TYPE.other))}</span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p>{L("We found no how-to-buy, pricing, licensing or policy pages on their site.", "لم نجد في موقعهم صفحات للشراء أو الأسعار أو الترخيص أو السياسات.")}</p>
        )}
        <div className="kb-chips">
          <button type="button" className="kb-chip" onClick={() => ask(L("pricing licensing subscription", "الأسعار الترخيص الاشتراك"))}>
            {L("Search what we stored about pricing", "ابحث فيما خزّناه عن الأسعار")}
          </button>
          <button type="button" className="kb-chip" onClick={() => ask(L("warranty support terms", "الضمان الدعم الشروط"))}>
            {L("Search for warranty and support terms", "ابحث عن شروط الضمان والدعم")}
          </button>
        </div>
      </div>
      </div>
    );
  }

  const status = PRICING_STATUS[p.pricing_status ?? ""];
  return (
    <div className="kb-stack">
      <Coverage p={p} />
      {conflicts.length > 0 && (
        <div className="kb-callout" role="note">
          <Icon name="warning" size={15} />
          <div>
            {conflicts.map((c) => (
              <p key={c.about}>
                {L("Sources disagree on ", "المصادر مختلفة حول ")}<b>{c.about}</b>:{" "}
                {c.claims.map((x, i) => (
                  <span key={i}>{i > 0 && "; "}{x.value} ({x.source_url ? host(x.source_url) : "?"}{x.official ? L(", their site", "، موقعهم") : ""})</span>
                ))}.
              </p>
            ))}
          </div>
        </div>
      )}
      <Group title={L("How they charge", "كيف يحاسبون")}>
        {status && <p className="kb-status-line">{L(status[0], status[1])}</p>}
        {p.pricing_model ? (
          <div className="kb-model"><p dir="auto">{p.pricing_model.value}</p>{ev(p.pricing_model)}</div>
        ) : <Empty>{L("Their pages do not say how they charge.", "صفحاتهم لا تذكر طريقة المحاسبة.")}</Empty>}
      </Group>
      <Group title={L("How to buy", "طريقة الشراء")}>
        {channels.length ? (
          <ul className="kb-buy">
            {channels.map((c) => <li key={c.value}><span dir="auto">{c.value}</span>{ev(c, true)}</li>)}
          </ul>
        ) : <Empty>{L("Their pages do not say how to buy.", "صفحاتهم لا تذكر طريقة الشراء.")}</Empty>}
        {buyPages.length > 0 && (
          <p className="kb-muted kb-small">
            {L("Read: ", "قرأنا: ")}
            {buyPages.map((x, i) => (
              <span key={x.url}>{i > 0 && ", "}<a href={x.url} target="_blank" rel="noreferrer"><bdi>{x.title || host(x.url)}</bdi></a></span>
            ))}
          </p>
        )}
      </Group>
      <Group title={L("Plans, licences and fees", "الباقات والتراخيص والرسوم")}>
        {plans.length ? <div className="kb-plans">{plans.map((pl) => <PlanCard key={pl.name} plan={pl} />)}</div>
          : <Empty>{L("No named plans, licences or fees in what we read.", "لا باقات أو تراخيص أو رسوم مسماة فيما قرأناه.")}</Empty>}
      </Group>
      <Group title={L("Service terms", "شروط الخدمة")}><Terms policies={policies} /></Group>
    </div>
  );
}

/* ------------------------------------------------------------------ sources */

function SourceRow({ entityId, source, startOpen }: { entityId: string; source: KbSource; startOpen: boolean }) {
  const L = useL();
  const [open, setOpen] = useState(startOpen);
  const [passages, setPassages] = useState<KbPassage[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open && passages === null && !error) {
      edrak.competitorKbPassages(entityId, source.source_url).then(setPassages).catch((e: Error) => setError(e.message));
    }
  }, [open, passages, error, entityId, source.source_url]);

  const label = PAGE_TYPE[source.page_type ?? "other"] ?? PAGE_TYPE.other;
  const official = source.source_type === "official";
  return (
    <div className={`kb-src ${official ? "is-official" : "is-third"} ${open ? "is-open" : ""}`}>
      <button type="button" className="kb-src-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <i className="kb-mark" aria-hidden="true" />
        <span className="kb-src-title">
          <bdi>{source.title || host(source.source_url)}</bdi>
          <span className="kb-src-sub"><bdi dir="ltr">{host(source.source_url)}</bdi><span className="kb-tag">{L(label[0], label[1])}</span></span>
        </span>
        <span className="kb-src-count">{source.chunks}<small>{L("passages", "مقاطع")}</small></span>
        <Icon name={open ? "chevronDown" : "chevron"} size={14} />
      </button>
      {open && (
        <div className="kb-src-body">
          {error && <p className="kb-error">{error}</p>}
          {passages === null && !error && <div className="shim" style={{ height: 56, borderRadius: 8 }} />}
          {passages?.map((p) => <p key={p.id} className="kb-passage" dir="auto">{p.content}</p>)}
          <a className="kb-link" href={source.source_url} target="_blank" rel="noreferrer">{L("Open the original page", "افتح الصفحة الأصلية")}</a>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ ask */

function Ask({ entityId, suggestions, initial }: { entityId: string; suggestions: string[]; initial?: string | null }) {
  const L = useL();
  const [q, setQ] = useState(initial ?? "");
  const [hits, setHits] = useState<KbHit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initial) void run(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);

  const run = async (text: string) => {
    if (text.trim().length < 2) return;
    setQ(text);
    setBusy(true);
    setError(null);
    try {
      setHits(await edrak.competitorKbSearch(entityId, text.trim()));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="kb-ask">
      <p className="kb-muted">{L("Ask about this competitor. Answers are passages from the pages above, exactly as the research agent will see them.",
        "اسأل عن هذا المنافس. الإجابات مقاطع من الصفحات أعلاه، كما سيراها وكيل البحث تمامًا.")}</p>
      <div className="kb-ask-bar">
        <input className="input" value={q} dir="auto" placeholder={L("Return policy, pricing plans…", "سياسة الإرجاع، الباقات…")}
          onChange={(e: { target: { value: string } }) => setQ(e.target.value)}
          onKeyDown={(e: { key: string }) => { if (e.key === "Enter") void run(q); }} />
        <button type="button" className="btn ink" disabled={busy || q.trim().length < 2} onClick={() => void run(q)}>
          {busy ? L("Searching…", "جارٍ البحث…") : L("Search", "ابحث")}
        </button>
      </div>
      <div className="kb-chips">
        {suggestions.map((s) => <button key={s} type="button" className="kb-chip" onClick={() => void run(s)} dir="auto">{s}</button>)}
      </div>
      {error && <p className="kb-error">{error}</p>}
      {hits && hits.length === 0 && <p className="kb-muted">{L("Nothing stored matches this. Try other words, or rebuild the profile.", "لا شيء مخزّن يطابق هذا. جرّب كلمات أخرى أو أعد بناء الملف.")}</p>}
      {hits?.map((h, i) => (
        <article key={`${h.source_url}-${i}`} className={`kb-hit ${h.source_type === "official" ? "is-official" : "is-third"}`}>
          <header>
            {h.source_url
              ? <a href={h.source_url} target="_blank" rel="noreferrer"><bdi>{h.title || host(h.source_url)}</bdi></a>
              : <bdi>{h.title || L("Stored passage", "مقطع مخزّن")}</bdi>}
            {typeof h.score === "number" && (
              <span className="kb-match" role="img" aria-label={L(`${Math.round(h.score * 100)}% match`, `تطابق ${Math.round(h.score * 100)}%`)}>
                <i style={{ width: `${Math.round(h.score * 100)}%` }} />
              </span>
            )}
          </header>
          <p className="kb-passage" dir="auto">{h.text}</p>
        </article>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ the view */

interface Props {
  entityId: string;
  /** Inside the onboarding panel: header, key facts, sources strip and what we know. */
  compact?: boolean;
}

/** One competitor in depth: who they are, what they sell and charge, their terms and latest moves, and where each fact came from. */
export function CompetitorProfileView({ entityId, compact = false }: Props) {
  const L = useL();
  const [data, setData] = useState<CompetitorKnowledge | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [picked, setPicked] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);
  const [askFor, setAskFor] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    setTab("overview");
    setQueued(false);
    edrak.competitorKnowledge(entityId)
      .then((d) => { if (alive) setData(d); })
      .catch((e: Error) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [entityId]);

  const offeringNames = (data?.profile.offerings ?? []).slice(0, 2).map((o) => o.official_name).join("|");
  const suggestions = useMemo(
    () => [...offeringNames.split("|").filter(Boolean), L("Pricing", "الأسعار"), L("Returns", "الإرجاع")].slice(0, 5),
    [offeringNames, L],
  );

  if (error) {
    return (
      <div className="kb-empty">
        {error === "not_built" ? L("This competitor's profile has not been built yet. It will appear here when the build finishes.",
          "لم يُبنَ ملف هذا المنافس بعد. سيظهر هنا عند انتهاء البناء.") : error}
      </div>
    );
  }
  if (!data) return <div className="shim" style={{ height: compact ? 160 : 420, borderRadius: 14 }} aria-busy="true" />;

  // Offerings, plans, news and claims outside the target company's field (VMware, for an NVIDIA
  // workspace) are set aside here, so every tab shows only what the comparison needs.
  const raw = data.profile;
  const setAside: string[] = [];
  const p: KbProfile = {
    ...raw,
    policies: (raw.policies ?? []).filter((policy) => policy.topic !== "delivery"),
  };
  const sources = data.sources;
  const offerings = p.offerings ?? [];
  const plans = p.plans ?? [];
  const policies = p.policies ?? [];
  const aiIntel = p.ai_accelerator_intelligence && Object.keys(p.ai_accelerator_intelligence).length ? p.ai_accelerator_intelligence : null;
  const news = p.recent_news ?? [];
  const found = new Set(Object.keys(p.evidence_surfaces ?? {}));
  const gaps = EXPECTED.filter(([kind]) => !found.has(kind));
  const issues = Object.entries(p.fetch_issues ?? {});

  const pick = (url: string) => {
    setPicked(url);
    setTab("sources");
  };
  const rebuild = async () => {
    try {
      await edrak.buildCompetitorKb([entityId], true);
      setQueued(true);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const header = (
    <header className="kb-head" style={{ ["--kb-hue" as string]: hueOf(data.name) }}>
      <span className="kb-mono" aria-hidden="true">{data.name.trim()[0]?.toUpperCase()}</span>
      <div className="kb-head-main">
        <h2 className="kb-name"><bdi>{data.name}</bdi></h2>
        {p.description ? (
          <div className="kb-lede"><p dir="auto">{p.description.value}</p>{ev(p.description)}</div>
        ) : (
          <p className="kb-lede kb-muted">{L("Their own pages do not say what they do.", "صفحاتهم لا تذكر ما يفعلونه.")}</p>
        )}
        <div className="kb-meta">
          {p.website && <a href={p.website} target="_blank" rel="noreferrer" dir="ltr">{host(p.website)}</a>}
          {data.markets.map((m) => <span key={m} className="kb-tag">{m}</span>)}
          <span className="kb-muted">{since(data.built_at, L)}</span>
        </div>
      </div>
      {!compact && (
        <button type="button" className="btn ghost sm kb-rebuild" disabled={queued} onClick={() => void rebuild()}>
          {queued ? L("Rebuild queued", "أُضيفت إعادة البناء") : L("Rebuild", "أعد البناء")}
        </button>
      )}
    </header>
  );

  const notice = p.profile_basis && p.profile_basis !== "official" && (
    <div className="kb-callout" role="note">
      <Icon name="warning" size={15} />
      <div>
        {issues.length > 0
          ? issues.map(([h, why]) => <p key={h}>{issueText(h, why, L)}</p>)
          : <p>{L("Few of their own pages could be read, so some facts come from other sites. Each fact shows where it came from.",
              "تعذرت قراءة معظم صفحاتهم، لذا تأتي بعض المعلومات من مواقع أخرى. تظهر كل معلومة مصدرها.")}</p>}
      </div>
    </div>
  );

  if (compact) {
    return (
      <div className="kb-profile is-compact">
        {header}
        {notice}
        <KeyFacts p={p} />
        <EvidenceStrip sources={sources} />
        <WhatWeKnow p={p} />
        <a className="kb-link" href={`/competitors/${entityId}`}>{L("Open the full profile", "افتح الملف الكامل")}</a>
      </div>
    );
  }

  const tabs: [Tab, string, number | null][] = [
    ["overview", L("Overview", "نظرة عامة"), null],
    ["profile", L("Full profile", "الملف الكامل"), aspectsFound(p)],
    ["offerings", L("Offerings", "المنتجات"), offerings.length],
    ["pricing", L("Pricing & terms", "الأسعار والشروط"),
      plans.length + policies.length + (p.pricing_model ? 1 : 0) + ((p.channels ?? []).length ? 1 : 0)],
    ["news", L("Latest moves", "آخر التحركات"), news.length],
    ["sources", L("Sources", "المصادر"), sources.length],
    ["ask", L("Ask", "اسأل"), null],
  ];

  return (
    <div className="kb-profile">
      {header}
      {notice}
      <EvidenceStrip sources={sources} onPick={pick} />

      <div className="kb-tabs" role="tablist" aria-label={L("Profile sections", "أقسام الملف")}>
        {tabs.map(([key, label, n]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={`kb-tab ${tab === key ? "is-on" : ""}`} onClick={() => setTab(key)}>
            {label}{n !== null && <span className="kb-count">{n}</span>}
          </button>
        ))}
      </div>

      <div className="kb-panel" role="tabpanel">
        {setAside.length > 0 && raw.scope && tab !== "profile" && tab !== "sources" && tab !== "ask" && (
          <details className="kb-outside kb-setaside">
            <summary>{L(`${setAside.length} item${setAside.length > 1 ? "s" : ""} outside ${raw.scope.target}'s field set aside`,
              `${setAside.length} عنصرًا خارج مجال ${raw.scope.target} مستبعدة`)}</summary>
            <ul>{setAside.map((name, i) => <li key={i}><span dir="auto">{name}</span></li>)}</ul>
          </details>
        )}
        {tab === "overview" && (
          <div className="kb-stack">
            <Group title={L("Key facts", "حقائق أساسية")}><KeyFacts p={p} /></Group>
            <div className="kb-overview">
              <div className="kb-stack">
                <Group title={L("What we know", "ما نعرفه")}><WhatWeKnow p={p} go={setTab} /></Group>
                <Group title={L("Latest moves", "آخر التحركات")}
                  aside={news.length > 2 && <button type="button" className="kb-link" onClick={() => setTab("news")}>{L(`All ${news.length}`, `الكل (${news.length})`)}</button>}>
                  <LatestMoves news={news} limit={2} />
                </Group>
              </div>
              <aside className="kb-stack">
                <Group title={L("In their own words", "بكلماتهم")}><InTheirWords p={p} /></Group>
                <div className="kb-gaps">
                  <h3>{L("Missing from their site", "غير موجود في موقعهم")}</h3>
                  {gaps.length === 0 ? (
                    <p className="kb-muted">{L("Pricing, product, policy and help pages were all found.", "عُثر على صفحات الأسعار والمنتجات والسياسات والمساعدة.")}</p>
                  ) : (
                    <>
                      <ul>{gaps.map(([kind, en, ar]) => <li key={kind}>{L(en, ar)}</li>)}</ul>
                      <p className="kb-muted">{L("The research agent looks for these on other sites and says when it cannot find them.",
                        "يبحث وكيل البحث عن هذه في مواقع أخرى ويوضح متى لا يجدها.")}</p>
                    </>
                  )}
                  {(p.socials ?? []).length > 0 && (
                    <>
                      <h3>{L("Official accounts", "الحسابات الرسمية")}</h3>
                      <div className="kb-chips">
                        {(p.socials ?? []).map((s) => (
                          <a key={s.url} className="kb-chip" href={s.url} target="_blank" rel="noreferrer">{PLATFORM[s.platform] ?? s.platform}</a>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              </aside>
            </div>
          </div>
        )}

        {tab === "offerings" && (offerings.length === 0 && !aiIntel ? (
          <div className="kb-empty">{L("No products or plans were named on the pages we could read.", "لم تُذكر منتجات أو باقات في الصفحات التي قرأناها.")}</div>
        ) : (
          <div className="kb-stack">
            {offerings.length > 0 && <div className="kb-off-grid">{offerings.map((o) => <OfferingCard key={o.official_name} o={o} />)}</div>}
            {aiIntel && <AiAccelerator data={aiIntel} />}
          </div>
        ))}

        {tab === "pricing" && (
          <PricingTerms p={p} ask={(q) => { setAskFor(q); setTab("ask"); }} />
        )}

        {tab === "profile" && <FullProfile p={p} />}
        {tab === "news" && <LatestMoves news={news} />}

        {tab === "sources" && (
          <div className="kb-stack">
            {(p.build_notes ?? []).length > 0 && (
              <details className="kb-notes">
                <summary>{L("How this profile was built", "كيف بُني هذا الملف")}</summary>
                <ul>{(p.build_notes ?? []).map((n) => <li key={n}>{n}</li>)}</ul>
              </details>
            )}
            <div className="kb-src-list">
              {[...sources.filter((s) => s.source_type === "official"), ...sources.filter((s) => s.source_type !== "official")].map((s) => (
                <SourceRow key={s.source_url} entityId={entityId} source={s} startOpen={s.source_url === picked} />
              ))}
            </div>
          </div>
        )}

        {tab === "ask" && <Ask entityId={entityId} suggestions={suggestions} initial={askFor} />}
      </div>
    </div>
  );
}
