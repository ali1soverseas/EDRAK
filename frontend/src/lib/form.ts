/**
 * The New analysis form and how it turns into a contract BusinessRequest.
 */
import type { BusinessRequest, CompanyProfile, UseCase } from "../types/contracts";
import type {
  AnalysisForm,
  CompanyFields,
  FocusKey,
  SourceToggle,
} from "../types/app";
import { newId } from "./ids";

export const USE_CASES: readonly UseCase[] = [
  "competitive_intelligence",
  "market_entry_expansion",
  "product_launch",
];

export const SOURCE_TOGGLES: readonly SourceToggle[] = [
  "internal_files",
  "competitor_web",
  "news_open_data",
  "reviews_social",
];

/** Focus chips offered for each use case. */
export const FOCUS_BY_USE_CASE: Record<UseCase, readonly FocusKey[]> = {
  competitive_intelligence: ["features", "pricing", "positioning", "sentiment"],
  market_entry_expansion: ["demand", "regulation", "competition", "pricing"],
  product_launch: [],
};

/** Plain-language focus areas the workers receive. */
export const FOCUS_PHRASE: Record<FocusKey, string> = {
  features: "Product features",
  pricing: "Pricing and packaging",
  positioning: "Positioning",
  sentiment: "Customer sentiment",
  demand: "Demand and market size",
  regulation: "Regulation and licensing",
  competition: "Competition",
  customers: "Customers",
};

export const TIME_WINDOWS: readonly number[] = [30, 90, 365];

export function defaultForm(useCase: UseCase = "competitive_intelligence"): AnalysisForm {
  return {
    use_case: useCase,
    goal: "",
    competitors: [],
    market: "",
    offering: "",
    target_customers: null,
    focus: [],
    time_window_days: null,
    sources: {
      internal_files: true,
      competitor_web: true,
      news_open_data: true,
      reviews_social: true,
    },
    repeat_weekly: false,
  };
}

export function enabledSources(form: AnalysisForm): SourceToggle[] {
  return SOURCE_TOGGLES.filter((source) => form.sources[source]);
}

/** The form can be submitted when there is a question and at least one place to look. */
export function isFormReady(form: AnalysisForm): boolean {
  return form.goal.trim().length > 0 && enabledSources(form).length > 0;
}

export function toCompanyProfile(company: CompanyFields): CompanyProfile {
  return {
    name: company.name.trim() || "Unnamed company",
    aliases: company.aliases,
    products: company.offerings,
    notes: company.description.trim() || null,
  };
}

function unique(values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of values) {
    const value = raw.trim();
    const key = value.toLowerCase();
    if (!value || seen.has(key)) continue;
    seen.add(key);
    out.push(value);
  }
  return out;
}

export function formToRequest(
  form: AnalysisForm,
  company: CompanyFields,
  requestId: string = newId(),
): BusinessRequest {
  const extras: Record<string, unknown> = {
    allowed_sources: enabledSources(form),
    repeat_weekly: form.repeat_weekly,
  };
  if (form.offering.trim()) extras.offering = form.offering.trim();
  if (form.target_customers) extras.target_customers = form.target_customers;

  return {
    request_id: requestId,
    goal: form.goal.trim(),
    company_profile: toCompanyProfile(company),
    business_context: {
      use_case: form.use_case,
      targets: unique([...form.competitors, form.market]),
      focus_areas: form.focus.map((key) => FOCUS_PHRASE[key]),
      trigger: "on_demand",
      time_window_days: form.time_window_days,
      constraints: [],
    },
    extras,
    created_at: new Date().toISOString(),
  };
}

function truncate(text: string, max: number): string {
  const clean = text.trim().replace(/\s+/g, " ");
  return clean.length <= max ? clean : `${clean.slice(0, max - 1).trimEnd()}…`;
}

/** The short name shown in the Analyses list and the breadcrumb. */
export function deriveTitle(form: AnalysisForm): string {
  switch (form.use_case) {
    case "product_launch":
      if (form.offering.trim()) return truncate(form.offering, 48);
      break;
    case "market_entry_expansion":
      if (form.market.trim()) return `Expansion into ${truncate(form.market, 36)}`;
      break;
    case "competitive_intelligence":
      if (form.competitors.length > 0) {
        const extra = form.competitors.length - 2;
        const names = form.competitors.slice(0, 2).join(", ");
        return truncate(`Competitors: ${names}${extra > 0 ? ` +${extra}` : ""}`, 56);
      }
      break;
  }
  return truncate(form.goal, 48) || "Untitled analysis";
}
