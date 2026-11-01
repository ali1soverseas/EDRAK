/**
 * Turning an imported draft into changes to the company form, and saying why an import failed.
 * Pure functions, so the rules (what is added, what is replaced, what is left alone) are tested.
 */
import type { ProfileDraft } from "../services/edrak";

export type ImportField = "name" | "aliases" | "notes" | "products" | "industry" | "markets" | "goals" | "links";

/** The part of the onboarding form an import can fill. */
export interface ImportableForm {
  name: string;
  aliases: string[];
  industries: string[];
  industry_other: string | null;
  description: string;
  offerings: string[];
  goals: string[];
  website: string;
  serves: string[];
  considers: string[];
  links: Record<string, string>;
}

export const ROW_ORDER: readonly ImportField[] = ["name", "aliases", "notes", "products", "industry", "markets", "goals", "links"];
/** The accounts the form has a box for. */
export const SOCIAL_PLATFORMS = ["x", "facebook", "instagram", "linkedin", "youtube", "tiktok"] as const;

export interface ImportRow {
  key: ImportField;
  kind: "text" | "list" | "links";
  /** Everything the draft says for this field, as shown. */
  proposed: string[];
  /** What choosing the row would change. Empty means the profile already has it. */
  adds: string[];
  /** For a text field the person already filled in differently: what is there now. */
  replaces: string | null;
  sources: string[];
  defaultOn: boolean;
  /** For the links row: each website or account, and whether choosing the row would fill its box. */
  linkItems?: { platform: string; url: string; isNew: boolean }[];
}

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();
const has = (list: readonly string[], value: string) => list.some((x) => same(x, value));

/** `base` followed by the items of `extra` it does not have yet, ignoring case. */
export function union(base: readonly string[], extra: readonly string[]): string[] {
  const out = [...base];
  for (const item of extra) if (item.trim() && !has(out, item)) out.push(item.trim());
  return out;
}

/** The website and account links of a draft that would fill an empty box of the form. */
export function newLinks(form: Pick<ImportableForm, "website" | "links">, draft: ProfileDraft): { platform: string; url: string }[] {
  const out: { platform: string; url: string }[] = [];
  for (const link of draft.links) {
    if (link.platform === "website") {
      if (!form.website.trim() && !out.some((l) => l.platform === "website")) out.push(link);
    } else if ((SOCIAL_PLATFORMS as readonly string[]).includes(link.platform)) {
      if (!(form.links[link.platform] ?? "").trim() && !out.some((l) => l.platform === link.platform)) out.push(link);
    }
  }
  return out;
}

/** One row for each field the draft has something for, in the order the form asks for them. */
export function importRows(form: ImportableForm, draft: ProfileDraft, industryLabel: (key: string) => string = (k) => k): ImportRow[] {
  const p = draft.profile;
  const src = (field: string) => draft.field_sources[field] ?? [];
  const rows: ImportRow[] = [];

  const text = (key: "name" | "notes", value: string | null, current: string) => {
    const v = (value ?? "").trim();
    if (!v) return;
    const filled = current.trim().length > 0;
    const identical = same(current, v);
    rows.push({
      key, kind: "text", proposed: [v], adds: identical ? [] : [v],
      replaces: filled && !identical ? current.trim() : null, sources: src(key),
      defaultOn: !identical && !filled,
    });
  };
  const list = (key: "aliases" | "products" | "markets" | "goals", items: string[], current: readonly string[]) => {
    const proposed = union([], items);
    if (!proposed.length) return;
    const adds = proposed.filter((x) => !has(current, x));
    rows.push({ key, kind: "list", proposed, adds, replaces: null, sources: src(key), defaultOn: adds.length > 0 });
  };

  text("name", p.name, form.name);
  list("aliases", p.aliases, form.aliases);
  text("notes", p.notes, form.description);
  list("products", p.products, form.offerings);
  if (p.industry) {
    rows.push({
      key: "industry", kind: "list", proposed: [industryLabel(p.industry)],
      adds: form.industries.includes(p.industry) ? [] : [industryLabel(p.industry)],
      replaces: null, sources: src("industry"), defaultOn: !form.industries.includes(p.industry),
    });
  }
  list("markets", p.markets, [...form.serves, ...form.considers]);
  list("goals", p.goals, form.goals);

  const links = draft.links.filter((l) => l.platform === "website" || (SOCIAL_PLATFORMS as readonly string[]).includes(l.platform));
  if (links.length) {
    const fresh = newLinks(form, draft);
    rows.push({
      key: "links", kind: "links", proposed: links.map((l) => `${l.platform}: ${l.url}`),
      adds: fresh.map((l) => `${l.platform}: ${l.url}`), replaces: null, sources: [], defaultOn: fresh.length > 0,
      linkItems: links.map((l) => ({ ...l, isNew: fresh.some((f) => f.platform === l.platform && f.url === l.url) })),
    });
  }
  return rows.sort((a, b) => ROW_ORDER.indexOf(a.key) - ROW_ORDER.indexOf(b.key));
}

/** The rows chosen by default: everything that changes something and does not overwrite what the person wrote. */
export function defaultChoices(rows: readonly ImportRow[]): Set<ImportField> {
  return new Set(rows.filter((r) => r.defaultOn).map((r) => r.key));
}

/**
 * The form with the chosen fields applied. Lists are added to, never replaced. A name or description
 * replaces the current one only when its row is chosen. Links only fill boxes that are empty.
 */
export function applyDraft<T extends ImportableForm>(form: T, draft: ProfileDraft, chosen: ReadonlySet<ImportField>): T {
  const p = draft.profile;
  const next: T = { ...form, links: { ...form.links } };

  if (chosen.has("name") && p.name?.trim()) next.name = p.name.trim();
  if (chosen.has("notes") && p.notes?.trim()) next.description = p.notes.trim();
  if (chosen.has("aliases")) next.aliases = union(next.aliases, p.aliases).filter((a) => !same(a, next.name));
  if (chosen.has("products")) next.offerings = union(next.offerings, p.products);
  if (chosen.has("industry") && p.industry && !next.industries.includes(p.industry)) next.industries = [...next.industries, p.industry];
  if (chosen.has("markets")) {
    next.serves = union(next.serves, p.markets);
    next.considers = next.considers.filter((m) => !has(next.serves, m)); // a market is in one list or the other
  }
  if (chosen.has("goals")) next.goals = union(next.goals, p.goals);
  if (chosen.has("links")) {
    for (const link of newLinks(form, draft)) {
      if (link.platform === "website") next.website = link.url;
      else next.links[link.platform] = link.url;
    }
  }
  return next;
}

/** How many separate things the chosen rows add, for the confirmation line. */
export function appliedCount(rows: readonly ImportRow[], chosen: ReadonlySet<ImportField>): number {
  return rows.filter((r) => chosen.has(r.key)).reduce((n, r) => n + r.adds.length, 0);
}

/* ------------------------------------------------------------------ errors */

/** [English, Arabic] for each error code the import endpoints return. */
const MESSAGES: Record<string, readonly [string, string]> = {
  invalid_url: ["That is not a web address Edrak can read.", "هذا ليس عنوان ويب يستطيع إدراك قراءته."],
  site_unreachable: ["Edrak could not read that website.", "تعذر على إدراك قراءة هذا الموقع."],
  model_unavailable: ["The language model could not be reached. Try again shortly.", "تعذر الوصول إلى النموذج اللغوي. حاول مجددًا بعد قليل."],
  extraction_failed: ["Edrak could not turn that into a profile. Try again.", "تعذر على إدراك تحويل ذلك إلى ملف. حاول مجددًا."],
  not_a_pdf: ["That file is not a PDF.", "هذا الملف ليس بصيغة PDF."],
  empty_file: ["That file is empty.", "هذا الملف فارغ."],
  too_large: ["That PDF is too large.", "ملف PDF هذا كبير جدًا."],
  too_many_pages: ["That PDF has too many pages.", "عدد صفحات ملف PDF هذا كبير جدًا."],
  encrypted: ["That PDF is password protected. Remove the password and try again.", "ملف PDF هذا محمي بكلمة مرور. أزلها وحاول مجددًا."],
  corrupt: ["That PDF could not be read. It may be damaged.", "تعذرت قراءة ملف PDF هذا. قد يكون تالفًا."],
  not_signed_in: ["Your session has ended. Sign in again.", "انتهت جلستك. سجّل الدخول مجددًا."],
};
const FALLBACK: readonly [string, string] = ["Something went wrong. Try again.", "حدث خطأ ما. حاول مجددًا."];
const OFFLINE: readonly [string, string] = ["Could not reach the server.", "تعذر الوصول إلى الخادم."];

/** The error code in a failed request: the `code` of the server's detail, or its detail when that is a plain string. */
export function errorCode(error: unknown): string {
  const detail = (error as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object") return String((detail as { code?: unknown }).code ?? "");
  return typeof detail === "string" ? detail : "";
}

/**
 * The text to show for a failed import. In English the server's own message is preferred because it
 * carries the specifics (the size limit, which page); in Arabic the matching sentence above is used.
 */
export function importErrorMessage(error: unknown, L: (en: string, ar: string) => string): string {
  if (error instanceof TypeError) return L(...OFFLINE);
  const known = MESSAGES[errorCode(error)] ?? FALLBACK;
  const detail = (error as { detail?: unknown } | null)?.detail;
  const server = detail && typeof detail === "object" ? String((detail as { message?: unknown }).message ?? "") : "";
  return L(server || known[0], known[1]);
}

/** The host of a typed address, for "Reading example.com". Falls back to what was typed. */
export function hostLabel(input: string): string {
  const text = input.trim();
  try {
    return new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`).hostname.replace(/^www\./, "") || text;
  } catch {
    return text;
  }
}
