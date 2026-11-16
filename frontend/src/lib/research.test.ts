import { describe, expect, it } from "vitest";
import type { FactsGrouped, ResearchFact, ResearchRun } from "../services/edrak";
import {
  activeRunId, allFacts, clearFactIds, countByStatus, filterGrouped, durationLabel, editPayload, editText, hostOf, isActive, keepOne, keyLabel, listItems, noticeFor,
  researchErrorMessage, runSeconds, runSummary, sourceLabel, stageLabel, stageProgress, toggleId,
} from "./research";

const en = (e: string) => e;
const ar = (_e: string, a: string) => a;

const run = (over: Partial<ResearchRun> = {}): ResearchRun => ({
  id: "r1", status: "running", stage: "crawl", input: {}, pages_crawled: 12, pdfs_read: 1, facts_found: 0, stats: {}, warnings: [], error: null,
  created_at: "2026-11-04T10:00:00.000Z", started_at: "2026-11-04T10:00:01.000Z", finished_at: null, ...over,
});
const fact = (over: Partial<ResearchFact> = {}): ResearchFact => ({
  id: "f1", category: "leadership", proposed_category: null, key: "ceo", key_label_en: "Chief executive officer", key_label_ar: "الرئيس التنفيذي",
  value: "Amal Nasr", value_type: "text", source_tier: "own_site", source_url: "https://www.acme.example/about/", pdf_page: null,
  quote: "Amal Nasr is the CEO", observed_at: "", confidence: "high", status: "suggested", conflict_group: null, run_id: "r1",
  edited_by_user: false, created_at: "", updated_at: "", ...over,
});

describe("run state", () => {
  it("is active while queued or running, and finished otherwise", () => {
    expect(["queued", "running"].map((status) => isActive(run({ status: status as ResearchRun["status"] })))).toEqual([true, true]);
    expect(["done", "failed", "cancelled"].map((status) => isActive(run({ status: status as ResearchRun["status"] })))).toEqual([false, false, false]);
    expect(isActive(undefined)).toBe(false);
  });

  it("names the step in both languages and falls back for an unknown one", () => {
    expect(stageLabel("pdfs", en)).toBe("Reading PDFs");
    expect(stageLabel("pdfs", ar)).toBe("قراءة ملفات PDF");
    expect(stageLabel(null, en)).toBe("Starting");
    expect(stageLabel("something_new", en)).toBe("Starting");
  });

  it("moves the bar forward with the step and never shows 100 before the run is done", () => {
    const at = (stage: string) => stageProgress(run({ stage }));
    expect(at("seed")).toBeLessThan(at("crawl"));
    expect(at("crawl")).toBeLessThan(at("extract"));
    expect(at("merge_and_save")).toBeLessThan(100);
    expect(stageProgress(run({ status: "done", stage: null }))).toBe(100);
    expect(stageProgress(run({ stage: null }))).toBeGreaterThan(0);
  });

  it("summarizes a run in the person's language", () => {
    expect(runSummary(run(), en)).toBe("Reading your website · 12 pages, 1 PDFs, 0 facts");
    expect(runSummary(run({ status: "done", facts_found: 40 }), en)).toBe("Finished: 12 pages, 1 PDFs, 40 facts");
    expect(runSummary(run({ status: "failed" }), en)).toContain("Nothing was saved");
    expect(runSummary(run({ status: "cancelled" }), ar)).toContain("لم يُحفظ");
    expect(runSummary(run({ status: "queued" }), en)).toBe("Waiting to start");
  });

  it("measures how long a finished run took", () => {
    expect(runSeconds(run())).toBeNull();
    expect(runSeconds(run({ finished_at: "2026-11-04T10:04:31.000Z" }))).toBe(270);
    expect(durationLabel(45, en)).toBe("45 s");
    expect(durationLabel(270, en)).toBe("5 min");
  });
});

describe("fact text", () => {
  it("labels a key with its canonical name, else with the key written out", () => {
    expect(keyLabel(fact(), "en")).toBe("Chief executive officer");
    expect(keyLabel(fact(), "ar")).toBe("الرئيس التنفيذي");
    expect(keyLabel(fact({ key: "annual_revenue", key_label_en: null, key_label_ar: null }), "en")).toBe("Annual revenue");
    expect(keyLabel(fact({ key: "annual_revenue", key_label_en: "Revenue", key_label_ar: null }), "ar")).toBe("Annual revenue");
  });

  it("reads list values, and treats anything else as one item", () => {
    expect(listItems('["Egypt", "KSA", " "]')).toEqual(["Egypt", "KSA"]);
    expect(listItems("Egypt")).toEqual(["Egypt"]);
    expect(listItems("")).toEqual([]);
    expect(listItems('{"a":1}')).toEqual(['{"a":1}']);
  });

  it("shortens a source to its site and path, with the PDF page", () => {
    expect(sourceLabel(fact(), en)).toBe("acme.example/about");
    expect(sourceLabel(fact({ source_url: "https://acme.example/files/annual-report-2024-final-version-english.pdf", pdf_page: 7 }), en))
      .toBe("acme.example…2024-final-version-english.pdf, page 7");
    expect(sourceLabel(fact({ source_url: "https://acme.example/r.pdf", pdf_page: 2 }), ar)).toBe("acme.example/r.pdf, صفحة 2");
    expect(hostOf("https://www.acme.example/about")).toBe("acme.example");
    expect(hostOf("acme.example")).toBe("acme.example");
  });
});

describe("editing a value", () => {
  it("starts a list as one item per line", () => {
    expect(editText(fact({ value_type: "list", value: '["A","B"]' }))).toBe("A\nB");
    expect(editText(fact())).toBe("Amal Nasr");
  });

  it("sends nothing for a blank or unchanged value", () => {
    expect(editPayload(fact(), "  ")).toBeNull();
    expect(editPayload(fact(), " Amal Nasr ")).toBeNull();
    expect(editPayload(fact({ value_type: "list", value: '["A","B"]' }), "A\nB\n")).toBeNull();
    expect(editPayload(fact({ value_type: "list", value: '["A"]' }), "\n\n")).toBeNull();
  });

  it("sends a changed text value trimmed, and a changed list as a JSON array of its lines", () => {
    expect(editPayload(fact(), "  Amal Nasr, Jr. ")).toEqual({ value: "Amal Nasr, Jr." });
    expect(editPayload(fact({ value_type: "list", value: '["A"]' }), "A\n B \n\nC")).toEqual({ value: '["A","B","C"]', valueType: "list" });
  });
});

describe("choosing among facts that disagree", () => {
  it("keeps one and rejects the others", () => {
    expect(keepOne(["a", "b", "c"], "b")).toEqual({ approve: ["b"], reject: ["a", "c"] });
    expect(keepOne(["a"], "a")).toEqual({ approve: ["a"], reject: [] });
  });

  it("toggles a selection without changing the original set", () => {
    const first = new Set(["a"]);
    const second = toggleId(first, "b");
    expect([...first]).toEqual(["a"]);
    expect([...second].sort()).toEqual(["a", "b"]);
    expect([...toggleId(second, "a")]).toEqual(["b"]);
  });

  it("counts facts by status for the tabs", () => {
    const counts = countByStatus([fact(), fact({ status: "approved" }), fact({ status: "approved" }), fact({ status: "rejected" })]);
    expect(counts).toEqual({ suggested: 1, approved: 2, rejected: 1, all: 4 });
  });
});

describe("errors", () => {
  const server = (code: string, extra: object = {}) => Object.assign(new Error("x"), { status: 422, detail: { code, message: "m", ...extra } });

  it("explains the start errors in both languages", () => {
    expect(researchErrorMessage(server("no_website"), en)).toBe("Add your company's website first.");
    expect(researchErrorMessage(server("invalid_url"), en)).toContain("not a public website address");
    expect(researchErrorMessage(server("run_active"), ar)).toContain("البحث جارٍ");
    expect(researchErrorMessage(server("other"), en)).toBe("Something went wrong. Try again.");
    expect(researchErrorMessage(new TypeError("Failed to fetch"), en)).toContain("Could not reach the server");
  });

  it("finds the running run's id in a 409", () => {
    expect(activeRunId(server("run_active", { run_id: "r9" }))).toBe("r9");
    expect(activeRunId(server("run_active", { run_id: null }))).toBeNull();
    expect(activeRunId(server("no_website"))).toBeNull();
    expect(activeRunId(new Error("x"))).toBeNull();
  });
});

describe("the top-bar notice", () => {
  it("says research is running while it runs, even if it was seen", () => {
    expect(noticeFor(run(), 0, "r1")).toEqual({ kind: "running", stage: "crawl" });
  });

  it("asks for a review when a finished run left facts to review, until the person has looked", () => {
    const done = run({ status: "done" });
    expect(noticeFor(done, 12, null)).toEqual({ kind: "review", count: 12 });
    expect(noticeFor(done, 12, "r1")).toBeNull();
    expect(noticeFor(done, 0, null)).toBeNull();
  });

  it("reports a failed run once, and says nothing for a cancelled one or no run", () => {
    expect(noticeFor(run({ status: "failed" }), 0, null)).toEqual({ kind: "failed" });
    expect(noticeFor(run({ status: "failed" }), 0, "r1")).toBeNull();
    expect(noticeFor(run({ status: "cancelled" }), 3, null)).toBeNull();
    expect(noticeFor(undefined, 5, null)).toBeNull();
  });
});

describe("the grouped list", () => {
  const data: FactsGrouped = {
    total: 6,
    categories: [
      {
        category: "leadership", label_en: "Leadership", label_ar: "القيادة",
        facts: [fact({ id: "cfo", key: "cfo", status: "approved" }), fact({ id: "founder", key: "founder", confidence: "medium" })],
        conflicts: [{ conflict_group: "g1", key: "ceo", facts: [fact({ id: "a", conflict_group: "g1" }), fact({ id: "b", conflict_group: "g1", status: "rejected" })] }],
      },
      {
        category: "financials", label_en: "Financials", label_ar: "البيانات المالية",
        facts: [fact({ id: "rev", category: "financials", key: "annual_revenue", source_tier: "own_pdf" }), fact({ id: "tp", source_tier: "third_party" })],
        conflicts: [],
      },
    ],
  };
  const ids = (cats: ReturnType<typeof filterGrouped>) => cats.map((c) => [c.category, c.facts.map((f) => f.id), c.conflicts.map((g) => g.facts.map((f) => f.id))]);

  it("lists every fact once, conflicts included", () => {
    expect(allFacts(data).map((f) => f.id).sort()).toEqual(["a", "b", "cfo", "founder", "rev", "tp"]);
    expect(allFacts(undefined)).toEqual([]);
  });

  it("keeps everything for all, and a conflict as a conflict", () => {
    expect(ids(filterGrouped(data, "all"))).toEqual([
      ["leadership", ["cfo", "founder"], [["a", "b"]]], ["financials", ["rev", "tp"], []],
    ]);
  });

  it("narrows to a status, dropping empty categories and unpairing a conflict that has lost a member", () => {
    expect(ids(filterGrouped(data, "suggested"))).toEqual([["leadership", ["founder", "a"], []], ["financials", ["rev", "tp"], []]]);
    expect(ids(filterGrouped(data, "approved"))).toEqual([["leadership", ["cfo"], []]]);
    expect(ids(filterGrouped(data, "rejected"))).toEqual([["leadership", ["b"], []]]);
    expect(filterGrouped(undefined, "all")).toEqual([]);
  });

  it("finds the facts that are safe to approve together: own sources, clear, not disputed, still to review", () => {
    expect(clearFactIds(data)).toEqual(["rev"]); // "founder" is only likely, "tp" is another website, "cfo" is already approved
    expect(clearFactIds(undefined)).toEqual([]);
  });
});
