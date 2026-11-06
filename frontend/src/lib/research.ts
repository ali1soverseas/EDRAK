/** Company research: the words, the rules and the small calculations behind the research screens. */
import type { FactCategory, FactsGrouped, FactStatus, FactTier, FactValueType, ResearchFact, ResearchRun } from "../services/edrak";
import { errorCode } from "./profileImport";

type L = (en: string, ar: string) => string;

export const isActive = (run: ResearchRun | undefined): boolean => run?.status === "queued" || run?.status === "running";
export const isFinished = (run: ResearchRun | undefined): boolean => !!run && !isActive(run);

/** The steps of a run in order, as the server names them. */
export const STAGES: ReadonlyArray<readonly [string, string, string]> = [
  ["seed", "Preparing", "التحضير"],
  ["crawl", "Reading your website", "قراءة موقعك"],
  ["pdfs", "Reading PDFs", "قراءة ملفات PDF"],
  ["extract", "Finding facts", "استخراج الحقائق"],
  ["gap_check", "Checking for gaps", "البحث عن النواقص"],
  ["own_social", "Reading social profiles", "قراءة حسابات التواصل"],
  ["third_party", "Searching the web", "البحث في الويب"],
  ["merge_and_save", "Saving", "الحفظ"],
];

export function stageLabel(stage: string | null | undefined, L: L): string {
  const known = STAGES.find(([key]) => key === stage);
  return known ? L(known[1], known[2]) : L("Starting", "جارٍ البدء");
}

/** 0 to 100, for the progress bar. The steps are not equal in length, so this is a guide, not a forecast. */
export function stageProgress(run: ResearchRun): number {
  if (run.status === "done") return 100;
  const index = STAGES.findIndex(([key]) => key === run.stage);
  return index < 0 ? 4 : Math.min(95, Math.round(((index + 0.5) / STAGES.length) * 100));
}

export const TIERS: Record<FactTier, readonly [string, string]> = {
  own_site: ["Company website", "موقع الشركة"],
  own_pdf: ["Company PDF", "ملف PDF للشركة"],
  own_social: ["Company social profile", "حساب الشركة"],
  third_party: ["Other website", "موقع آخر"],
};
export const tierLabel = (tier: FactTier, L: L): string => L(...TIERS[tier]);

export const CONFIDENCE: Record<ResearchFact["confidence"], readonly [string, string]> = {
  high: ["Clear", "واضح"], medium: ["Likely", "مرجّح"], low: ["Unsure", "غير مؤكد"],
};

export const STATUS_FILTERS: ReadonlyArray<readonly [FactStatus | "all", string, string]> = [
  ["suggested", "To review", "للمراجعة"],
  ["approved", "Approved", "المعتمدة"],
  ["rejected", "Rejected", "المرفوضة"],
  ["all", "All", "الكل"],
];

/** The name of a fact's key as the person reads it: the canonical label, else the key written out. */
export function keyLabel(fact: Pick<ResearchFact, "key" | "key_label_en" | "key_label_ar">, lang: "en" | "ar"): string {
  const label = lang === "ar" ? fact.key_label_ar : fact.key_label_en;
  if (label) return label;
  const words = fact.key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The items of a list value; a value that is not a JSON array of strings is one item. */
export function listItems(value: string): string[] {
  try {
    const parsed: unknown = JSON.parse(value);
    if (Array.isArray(parsed)) return parsed.map(String).filter((x) => x.trim());
  } catch {
    /* not JSON */
  }
  return value.trim() ? [value] : [];
}

/** What the editor starts with: a list as one item per line, anything else as written. */
export function editText(fact: Pick<ResearchFact, "value" | "value_type">): string {
  return fact.value_type === "list" ? listItems(fact.value).join("\n") : fact.value;
}

/** The value to send for an edit, or null when there is nothing to save (blank, or unchanged). */
export function editPayload(
  fact: Pick<ResearchFact, "value" | "value_type">, text: string,
): { value: string; valueType?: FactValueType } | null {
  if (fact.value_type === "list") {
    const items = text.split("\n").map((x) => x.trim()).filter(Boolean);
    if (!items.length) return null;
    const value = JSON.stringify(items);
    return value === JSON.stringify(listItems(fact.value)) ? null : { value, valueType: "list" };
  }
  const value = text.trim();
  return !value || value === fact.value.trim() ? null : { value };
}

/** Approving one of several facts that disagree: keep it, and reject the rest. */
export function keepOne(ids: string[], keep: string): { approve: string[]; reject: string[] } {
  return { approve: [keep], reject: ids.filter((id) => id !== keep) };
}

export function toggleId(selected: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(selected);
  if (!next.delete(id)) next.add(id);
  return next;
}

/** Counts by status, for the filter tabs. */
export function countByStatus(facts: ReadonlyArray<Pick<ResearchFact, "status">>): Record<FactStatus | "all", number> {
  const counts = { suggested: 0, approved: 0, rejected: 0, all: facts.length };
  facts.forEach((f) => { counts[f.status] += 1; });
  return counts;
}

/** A website address as the host alone: "https://www.acme.example/about" gives "acme.example". */
export function hostOf(url: string): string {
  try {
    return new URL(/^https?:\/\//i.test(url) ? url : `https://${url}`).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Where a fact was found, short enough for a row: "acme.example/about" or "acme.example/report.pdf, page 7". */
export function sourceLabel(fact: Pick<ResearchFact, "source_url" | "pdf_page">, L: L): string {
  let text = fact.source_url;
  try {
    const u = new URL(fact.source_url);
    const path = decodeURIComponent(u.pathname).replace(/\/$/, "");
    text = `${u.hostname.replace(/^www\./, "")}${path.length > 36 ? `…${path.slice(-30)}` : path}`;
  } catch {
    /* keep the address as it is */
  }
  return fact.pdf_page ? `${text}, ${L("page", "صفحة")} ${fact.pdf_page}` : text;
}

/** The sentence under a run's name: what it is doing, or what it found. */
export function runSummary(run: ResearchRun, L: L): string {
  const found = L(
    `${run.pages_crawled} pages, ${run.pdfs_read} PDFs, ${run.facts_found} facts`,
    `${run.pages_crawled} صفحة، ${run.pdfs_read} ملف PDF، ${run.facts_found} حقيقة`,
  );
  switch (run.status) {
    case "queued": return L("Waiting to start", "بانتظار البدء");
    case "running": return `${stageLabel(run.stage, L)} · ${found}`;
    case "done": return L(`Finished: ${found}`, `اكتمل: ${found}`);
    case "cancelled": return L("Stopped. Nothing was saved.", "تم الإيقاف. لم يُحفظ شيء.");
    default: return L("The run failed. Nothing was saved.", "فشلت المحاولة. لم يُحفظ شيء.");
  }
}

/** Seconds a run took, or null while it has not finished. */
export function runSeconds(run: ResearchRun): number | null {
  if (!run.started_at || !run.finished_at) return null;
  return Math.max(0, Math.round((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000));
}

export function durationLabel(seconds: number, L: L): string {
  return seconds < 90 ? L(`${seconds} s`, `${seconds} ث`) : L(`${Math.round(seconds / 60)} min`, `${Math.round(seconds / 60)} د`);
}

const START_MESSAGES: Record<string, readonly [string, string]> = {
  no_website: ["Add your company's website first.", "أضف موقع شركتك أولًا."],
  invalid_url: ["That is not a public website address. Check it and try again.", "هذا ليس عنوان موقع عامًا. تحقق منه وحاول مجددًا."],
  run_active: ["Research is already running for your company.", "البحث جارٍ بالفعل لشركتك."],
};

/** The message for a research call that failed. */
export function researchErrorMessage(error: unknown, L: L): string {
  if (error instanceof TypeError) return L("Could not reach the server. Check your connection.", "تعذر الوصول إلى الخادم. تحقق من اتصالك.");
  const known = START_MESSAGES[errorCode(error)];
  return known ? L(...known) : L("Something went wrong. Try again.", "حدث خطأ. حاول مجددًا.");
}

/** The id of the run that is already running, from a 409 `run_active` error. */
export function activeRunId(error: unknown): string | null {
  const detail = (error as { detail?: unknown } | null)?.detail as { code?: string; run_id?: string | null } | undefined;
  return detail?.code === "run_active" ? detail.run_id ?? null : null;
}

/* ------------------------------------------------------------ "seen" marker for the top-bar notice */

const SEEN_KEY = "edrak.research.seen";

export function seenRun(): string | null {
  try { return window.localStorage.getItem(SEEN_KEY); } catch { return null; }
}
export const SEEN_EVENT = "edrak-research-seen";
export function markSeen(runId: string): void {
  try { window.localStorage.setItem(SEEN_KEY, runId); } catch { /* storage is optional */ }
  window.dispatchEvent(new Event(SEEN_EVENT)); // the top bar listens, so its notice clears at once
}

export type Notice = { kind: "running"; stage: string | null } | { kind: "review"; count: number } | { kind: "failed" } | null;

/** What the top bar says about research: running, facts waiting for review, or a failure not yet looked at. */
export function noticeFor(run: ResearchRun | undefined, suggested: number, seen: string | null): Notice {
  if (!run) return null;
  if (isActive(run)) return { kind: "running", stage: run.stage };
  if (run.id === seen) return null;
  if (run.status === "failed") return { kind: "failed" };
  if (run.status === "done" && suggested > 0) return { kind: "review", count: suggested };
  return null;
}

/* ------------------------------------------------------------ the grouped list */

/** Every fact in the grouped answer, conflicts included. */
export function allFacts(data: FactsGrouped | undefined): ResearchFact[] {
  return (data?.categories ?? []).flatMap((c) => [...c.facts, ...c.conflicts.flatMap((g) => g.facts)]);
}

/**
 * The grouped answer narrowed to one status. The server groups all facts; the tabs filter here so
 * the counts stay known. A group that keeps fewer than two facts is no longer a disagreement on
 * screen, so its fact is listed on its own.
 */
export function filterGrouped(data: FactsGrouped | undefined, status: FactStatus | "all"): FactCategory[] {
  const keep = (f: ResearchFact) => status === "all" || f.status === status;
  const out: FactCategory[] = [];
  for (const category of data?.categories ?? []) {
    const facts = category.facts.filter(keep);
    const conflicts = [];
    for (const group of category.conflicts) {
      const members = group.facts.filter(keep);
      if (members.length > 1) conflicts.push({ ...group, facts: members });
      else facts.push(...members);
    }
    if (facts.length || conflicts.length) out.push({ ...category, facts, conflicts });
  }
  return out;
}

/** Ids of the facts that can be approved in one click: from the company's own site or PDFs, clearly stated, and not disputed. */
export function clearFactIds(data: FactsGrouped | undefined): string[] {
  return (data?.categories ?? [])
    .flatMap((c) => c.facts)
    .filter((f) => f.status === "suggested" && f.confidence === "high" && !f.conflict_group && (f.source_tier === "own_site" || f.source_tier === "own_pdf"))
    .map((f) => f.id);
}
