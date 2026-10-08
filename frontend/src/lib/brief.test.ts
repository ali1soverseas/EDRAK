import { describe, expect, it } from "vitest";
import { PACKS } from "../services/mock/packs";
import { planFor } from "../services/mock/planner";
import { briefFor } from "../services/mock/results";
import type { RunRecord } from "../services/mock/simulate";
import { defaultForm, formToRequest } from "./form";
import { WORKER_ORDER, hostOf, originOf, reliabilityFromQuality, sourceNote } from "./brief";
import type { CompanyFields } from "../types/app";
import type { Evidence } from "../types/contracts";

const company: CompanyFields = {
  name: "Nile Ledger",
  aliases: [],
  industry: "fintech",
  description: "",
  offerings: [],
  markets: [],
  strategic_goals: "",
  website: "",
  socials: [],
};

function brief(scenario: RunRecord["scenario"], useCase: "product_launch" | "market_entry_expansion" = "product_launch") {
  const form = { ...defaultForm(useCase), goal: "Is there a case for this?", market: "Egypt" };
  const request = formToRequest(form, company, "req-1");
  const plan = planFor(request, form);
  const run: RunRecord = { approved_at: "2026-10-08T09:00:00.000Z", approved_by: "Layla", speed: 1, scenario, cancelled_at: null };
  return briefFor({
    briefId: "brief-1",
    briefNo: 1,
    analysisId: "req-1",
    title: "Test",
    requestedBy: "Layla",
    request,
    plan,
    pack: PACKS[useCase],
    run,
  });
}

const evidence = (source_type: Evidence["source_type"]): Evidence => ({
  evidence_id: "e",
  source_type,
  source_title: null,
  source_url: null,
  publisher: null,
  extracted_fact: "fact",
  excerpt: null,
  retrieved_at: "2026-10-08T09:00:00.000Z",
  is_synthetic: false,
  metadata: {},
});

describe("reliability", () => {
  it("maps evidence quality to marks: high three, medium two, low one", () => {
    expect(reliabilityFromQuality("high")).toBe(3);
    expect(reliabilityFromQuality("medium")).toBe(2);
    expect(reliabilityFromQuality("low")).toBe(1);
  });
});

describe("source notes", () => {
  it("treats the person's own files as internal and as their data", () => {
    expect(originOf(evidence("internal_document"))).toBe("internal");
    expect(originOf(evidence("synthetic_internal"))).toBe("internal");
    expect(originOf(evidence("web_page"))).toBe("external");
    expect(sourceNote(evidence("internal_document"), 3)).toBe("your_data");
  });

  it("calls official sources primary and reviews opinion", () => {
    expect(sourceNote(evidence("official_documentation"), 3)).toBe("primary");
    expect(sourceNote(evidence("regulatory"), 3)).toBe("primary");
    expect(sourceNote(evidence("review_site"), 1)).toBe("opinion");
  });

  it("marks a low-reliability web page as thin and a better one as a vendor claim", () => {
    expect(sourceNote(evidence("web_page"), 1)).toBe("thin");
    expect(sourceNote(evidence("web_page"), 2)).toBe("vendor_claim");
  });
});

describe("hostOf", () => {
  it("returns the host without www, and null with no address", () => {
    expect(hostOf("https://www.example.com/a/b")).toBe("example.com");
    expect(hostOf(null)).toBeNull();
  });
});

describe("buildBrief", () => {
  it("numbers evidence once, in worker order, with no gaps or repeats", () => {
    const result = brief("normal");
    const numbers = result.evidence.map((item) => item.display_no);
    expect(numbers).toEqual(numbers.map((_, index) => index + 1));
    const workerRuns = result.evidence.map((item) => item.worker);
    const order = workerRuns.map((worker) => WORKER_ORDER.indexOf(worker));
    expect(order).toEqual([...order].sort((a, b) => a - b));
  });

  it("resolves every finding reference to a numbered source", () => {
    const result = brief("normal");
    for (const lens of result.lenses) {
      for (const finding of lens.findings) {
        expect(finding.refs.length).toBe(finding.finding.evidence_refs.length);
        for (const ref of finding.refs) expect(result.evidence.some((item) => item.display_no === ref.display_no)).toBe(true);
      }
    }
  });

  it("takes a finding's reliability and status from its verdict", () => {
    const result = brief("normal");
    const insufficient = result.lenses.flatMap((lens) => lens.findings).filter((finding) => finding.status === "insufficient");
    expect(insufficient.length).toBeGreaterThan(0);
    expect(insufficient.every((finding) => finding.reliability === 1)).toBe(true);
    const verified = result.lenses.flatMap((lens) => lens.findings).find((finding) => finding.status === "verified" && finding.verdict?.evidence_quality === "high");
    expect(verified?.reliability).toBe(3);
  });

  it("is complete when every worker reported, and partial when one failed", () => {
    expect(brief("normal").partial).toBe(false);
    const partial = brief("market_fails");
    expect(partial.partial).toBe(true);
    const market = partial.lenses.find((lens) => lens.worker === "market_intelligence");
    expect(market?.failed).toBe(true);
    expect(market?.findings).toHaveLength(0);
    expect(partial.lenses.filter((lens) => !lens.failed)).toHaveLength(3);
  });

  it("lists worker gaps, then verification gaps, without repeats", () => {
    const result = brief("normal");
    const texts = result.gaps.map((gap) => gap.text.toLowerCase());
    expect(new Set(texts).size).toBe(texts.length);
    expect(result.gaps.some((gap) => gap.reported_by === "competitor_intelligence")).toBe(true);
    expect(result.gaps[result.gaps.length - 1].reported_by).toBe("verification");
  });

  it("only includes workers that were in the plan", () => {
    const form = { ...defaultForm("product_launch"), goal: "Question", sources: { internal_files: true, competitor_web: false, news_open_data: false, reviews_social: false } };
    const request = formToRequest(form, company, "req-2");
    const plan = planFor(request, form);
    expect(plan.tasks.map((task) => task.worker)).toEqual(["internal_intelligence"]);
  });
});
