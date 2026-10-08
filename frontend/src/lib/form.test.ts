import { describe, expect, it } from "vitest";
import { defaultForm, deriveTitle, enabledSources, formToRequest, isFormReady, toCompanyProfile } from "./form";
import type { CompanyFields } from "../types/app";

const company: CompanyFields = {
  name: "  Nile Ledger ",
  aliases: ["NL Pay"],
  industry: "fintech",
  description: "Invoicing software.",
  offerings: ["Invoicing platform"],
  markets: ["Egypt"],
  strategic_goals: "Grow.",
  website: "",
  socials: [],
};

describe("formToRequest", () => {
  it("builds a BusinessRequest the contract accepts", () => {
    const form = {
      ...defaultForm("competitive_intelligence"),
      goal: "  What are they doing?  ",
      competitors: ["Misr Pay", "misr pay", "Delta Invoice"],
      market: "Egypt",
      focus: ["pricing" as const, "features" as const],
      time_window_days: 90,
      repeat_weekly: true,
    };
    const request = formToRequest(form, company, "req-1");

    expect(request.request_id).toBe("req-1");
    expect(request.goal).toBe("What are they doing?");
    expect(request.company_profile).toEqual({
      name: "Nile Ledger",
      aliases: ["NL Pay"],
      products: ["Invoicing platform"],
      notes: "Invoicing software.",
    });
    expect(request.business_context.use_case).toBe("competitive_intelligence");
    // Duplicates are dropped ignoring case, and the market is a target too.
    expect(request.business_context.targets).toEqual(["Misr Pay", "Delta Invoice", "Egypt"]);
    expect(request.business_context.focus_areas).toEqual(["Pricing and packaging", "Product features"]);
    expect(request.business_context.time_window_days).toBe(90);
    expect(request.business_context.trigger).toBe("on_demand");
    expect(request.extras.repeat_weekly).toBe(true);
    expect(request.extras.allowed_sources).toEqual(["internal_files", "competitor_web", "news_open_data", "reviews_social"]);
  });

  it("keeps the offering and target customers for a product launch", () => {
    const form = { ...defaultForm("product_launch"), goal: "Q", offering: "AI support", target_customers: "smes" as const };
    const request = formToRequest(form, company);
    expect(request.extras.offering).toBe("AI support");
    expect(request.extras.target_customers).toBe("smes");
  });

  it("falls back when the company has no name", () => {
    expect(toCompanyProfile({ ...company, name: "   ", description: " " })).toMatchObject({ name: "Unnamed company", notes: null });
  });
});

describe("isFormReady", () => {
  it("needs a question and at least one place to look", () => {
    const form = defaultForm();
    expect(isFormReady(form)).toBe(false);
    expect(isFormReady({ ...form, goal: "A question" })).toBe(true);
    const none = { ...form, goal: "A question", sources: { internal_files: false, competitor_web: false, news_open_data: false, reviews_social: false } };
    expect(enabledSources(none)).toEqual([]);
    expect(isFormReady(none)).toBe(false);
  });
});

describe("deriveTitle", () => {
  it("names the analysis after what the person gave", () => {
    expect(deriveTitle({ ...defaultForm("product_launch"), goal: "Q", offering: "AI support" })).toBe("AI support");
    expect(deriveTitle({ ...defaultForm("market_entry_expansion"), goal: "Q", market: "Saudi Arabia" })).toBe("Expansion into Saudi Arabia");
    expect(deriveTitle({ ...defaultForm("competitive_intelligence"), goal: "Q", competitors: ["A", "B", "C", "D"] })).toBe("Competitors: A, B +2");
  });

  it("falls back to the question, shortened", () => {
    const long = "word ".repeat(30);
    const title = deriveTitle({ ...defaultForm("competitive_intelligence"), goal: long });
    expect(title.length).toBeLessThanOrEqual(48);
    expect(title.endsWith("…")).toBe(true);
  });
});
