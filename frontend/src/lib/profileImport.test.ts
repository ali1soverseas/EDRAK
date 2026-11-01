import { describe, expect, it } from "vitest";
import type { ProfileDraft } from "../services/edrak";
import {
  applyDraft, appliedCount, defaultChoices, errorCode, hostLabel, importErrorMessage, importRows, newLinks, union,
  type ImportableForm,
} from "./profileImport";

const form: ImportableForm = {
  name: "", aliases: [], industries: [], industry_other: null, description: "", offerings: [],
  goals: [], website: "", serves: [], considers: [], links: {},
};

const draft: ProfileDraft = {
  profile: {
    name: "Nile Ledger", aliases: ["نايل ليدجر", "nile ledger"], notes: "Bookkeeping software for small shops.",
    products: ["Ledger Lite", "Ledger POS"], industry: "saas", markets: ["Egypt", "Jordan"], goals: ["Give every shop a clear view of its money."],
  },
  links: [
    { platform: "website", url: "https://nileledger.example" },
    { platform: "x", url: "https://x.com/nileledger" },
    { platform: "linkedin", url: "https://www.linkedin.com/company/nileledger" },
  ],
  field_sources: { name: ["S1"], aliases: ["S1"], notes: ["S2"], products: ["S2", "S3"], industry: ["S2"], markets: ["S2"], goals: ["S3"] },
  gaps: [],
  warnings: [],
  sources: [],
};

const label = (k: string) => ({ saas: "SaaS", fintech: "Fintech" })[k] ?? k;
const ar = (_english: string, arabic: string) => arabic;
const en = (e: string) => e;

describe("importRows", () => {
  it("makes a row for each field the draft has, in the order the form asks for them", () => {
    const rows = importRows(form, draft, label);
    expect(rows.map((r) => r.key)).toEqual(["name", "aliases", "notes", "products", "industry", "markets", "goals", "links"]);
    expect(rows.find((r) => r.key === "industry")!.proposed).toEqual(["SaaS"]);
    expect(rows.find((r) => r.key === "products")!.sources).toEqual(["S2", "S3"]);
  });

  it("leaves out fields the draft is empty for", () => {
    const sparse: ProfileDraft = { ...draft, profile: { ...draft.profile, name: null, aliases: [], notes: null, products: [], industry: null, markets: [], goals: [] }, links: [] };
    expect(importRows(form, sparse, label)).toEqual([]);
  });

  it("lists each website and account, marking the ones that would fill an empty box", () => {
    const row = importRows({ ...form, links: { x: "https://x.com/mine" } }, draft, label).find((r) => r.key === "links")!;
    expect(row.linkItems).toEqual([
      { platform: "website", url: "https://nileledger.example", isNew: true },
      { platform: "x", url: "https://x.com/nileledger", isNew: false },
      { platform: "linkedin", url: "https://www.linkedin.com/company/nileledger", isNew: true },
    ]);
  });

  it("selects everything by default on an empty form", () => {
    expect(defaultChoices(importRows(form, draft, label))).toEqual(new Set(["name", "aliases", "notes", "products", "industry", "markets", "goals", "links"]));
  });

  it("does not select a name or description that would overwrite what the person wrote, and says what is there", () => {
    const rows = importRows({ ...form, name: "Nile Ledger Co", description: "Our own words." }, draft, label);
    const name = rows.find((r) => r.key === "name")!;
    expect(name).toMatchObject({ replaces: "Nile Ledger Co", defaultOn: false, adds: ["Nile Ledger"] });
    expect(rows.find((r) => r.key === "notes")).toMatchObject({ replaces: "Our own words.", defaultOn: false });
    expect(defaultChoices(rows).has("name")).toBe(false);
    expect(defaultChoices(rows).has("products")).toBe(true); // lists are only added to
  });

  it("marks what the profile already has as adding nothing", () => {
    const full: ImportableForm = {
      ...form, name: "nile ledger", offerings: ["ledger lite", "Ledger POS"], industries: ["saas"], serves: ["egypt"], considers: ["Jordan"],
      goals: ["Give every shop a clear view of its money."], website: "https://mine.example", links: { x: "https://x.com/mine", linkedin: "https://linkedin.com/x" },
    };
    const rows = Object.fromEntries(importRows(full, draft, label).map((r) => [r.key, r]));
    expect(rows.name).toMatchObject({ adds: [], replaces: null, defaultOn: false });
    expect(rows.products.adds).toEqual([]);
    expect(rows.industry.adds).toEqual([]);
    expect(rows.markets.adds).toEqual([]); // Egypt is sold in, Jordan is being considered: both known
    expect(rows.goals.adds).toEqual([]);
    expect(rows.links.adds).toEqual([]);
    expect(rows.links.linkItems!.every((l) => !l.isNew)).toBe(true);
    expect(defaultChoices(Object.values(rows)).has("products")).toBe(false);
  });
});

describe("applyDraft", () => {
  const all = (rows = importRows(form, draft, label)) => defaultChoices(rows);

  it("fills an empty form from the draft", () => {
    const next = applyDraft(form, draft, all());
    expect(next).toMatchObject({
      name: "Nile Ledger", description: "Bookkeeping software for small shops.", offerings: ["Ledger Lite", "Ledger POS"],
      industries: ["saas"], serves: ["Egypt", "Jordan"], goals: ["Give every shop a clear view of its money."],
      website: "https://nileledger.example",
      links: { x: "https://x.com/nileledger", linkedin: "https://www.linkedin.com/company/nileledger" },
    });
    expect(next.aliases).toEqual(["نايل ليدجر"]); // "nile ledger" is the company's own name
    expect(form.name).toBe(""); // the original is not changed
  });

  it("adds to lists without removing or repeating anything", () => {
    const next = applyDraft({ ...form, offerings: ["Payroll", "ledger lite"], serves: ["Saudi Arabia"], goals: ["Grow"] }, draft, new Set(["products", "markets", "goals"]));
    expect(next.offerings).toEqual(["Payroll", "ledger lite", "Ledger POS"]);
    expect(next.serves).toEqual(["Saudi Arabia", "Egypt", "Jordan"]);
    expect(next.goals).toEqual(["Grow", "Give every shop a clear view of its money."]);
  });

  it("applies only the rows that are chosen", () => {
    const next = applyDraft(form, draft, new Set(["name", "products"]));
    expect(next.name).toBe("Nile Ledger");
    expect(next.offerings).toEqual(["Ledger Lite", "Ledger POS"]);
    expect(next.description).toBe("");
    expect(next.industries).toEqual([]);
    expect(next.website).toBe("");
  });

  it("replaces a name or description only when its row is chosen", () => {
    const filled = { ...form, name: "Old name", description: "Old text." };
    expect(applyDraft(filled, draft, new Set(["products"]))).toMatchObject({ name: "Old name", description: "Old text." });
    expect(applyDraft(filled, draft, new Set(["name"]))).toMatchObject({ name: "Nile Ledger", description: "Old text." });
  });

  it("moves a market out of the considering list when it is now one the company sells in", () => {
    const next = applyDraft({ ...form, considers: ["Jordan", "Oman"] }, draft, new Set(["markets"]));
    expect(next.serves).toEqual(["Egypt", "Jordan"]);
    expect(next.considers).toEqual(["Oman"]);
  });

  it("keeps an industry already chosen and adds a new one", () => {
    expect(applyDraft({ ...form, industries: ["fintech"] }, draft, new Set(["industry"])).industries).toEqual(["fintech", "saas"]);
    expect(applyDraft({ ...form, industries: ["saas"] }, draft, new Set(["industry"])).industries).toEqual(["saas"]);
  });

  it("fills only empty link boxes", () => {
    const next = applyDraft({ ...form, website: "https://mine.example", links: { x: "https://x.com/mine" } }, draft, new Set(["links"]));
    expect(next.website).toBe("https://mine.example");
    expect(next.links).toEqual({ x: "https://x.com/mine", linkedin: "https://www.linkedin.com/company/nileledger" });
  });

  it("ignores accounts the form has no box for", () => {
    const odd: ProfileDraft = { ...draft, links: [{ platform: "myspace", url: "https://myspace.com/x" }] };
    expect(newLinks(form, odd)).toEqual([]);
    expect(importRows(form, odd).some((r) => r.key === "links")).toBe(false);
  });

  it("counts what was added, for the confirmation", () => {
    const rows = importRows(form, draft, label);
    expect(appliedCount(rows, new Set(["name", "products"]))).toBe(3);
    expect(appliedCount(rows, new Set())).toBe(0);
  });
});

describe("union", () => {
  it("appends new items, ignoring case and blanks", () => {
    expect(union(["A", "b"], ["B", "c", " ", "C"])).toEqual(["A", "b", "c"]);
  });
});

describe("import errors", () => {
  const server = (code: string, message: string) => Object.assign(new Error(message), { status: 422, detail: { code, message } });

  it("reads the code from the server's detail", () => {
    expect(errorCode(server("encrypted", "x"))).toBe("encrypted");
    expect(errorCode(Object.assign(new Error("not_signed_in"), { detail: "not_signed_in" }))).toBe("not_signed_in");
    expect(errorCode(new Error("plain"))).toBe("");
  });

  it("shows the server's own message in English, because it names the limit or the page", () => {
    const err = server("too_many_pages", "This PDF has 90 pages. The limit is 60.");
    expect(importErrorMessage(err, en)).toBe("This PDF has 90 pages. The limit is 60.");
  });

  it("shows the matching Arabic sentence in Arabic", () => {
    expect(importErrorMessage(server("encrypted", "This PDF is password protected."), ar)).toBe("ملف PDF هذا محمي بكلمة مرور. أزلها وحاول مجددًا.");
  });

  it("falls back for an unknown code, a missing session and a dropped connection", () => {
    expect(importErrorMessage(server("???", ""), en)).toBe("Something went wrong. Try again.");
    expect(importErrorMessage(server("???", "The server said this."), en)).toBe("The server said this."); // plain-language already
    expect(importErrorMessage(server("???", "The server said this."), ar)).toBe("حدث خطأ ما. حاول مجددًا.");
    expect(importErrorMessage(Object.assign(new Error("not_signed_in"), { detail: "not_signed_in" }), en)).toBe("Your session has ended. Sign in again.");
    expect(importErrorMessage(new TypeError("Failed to fetch"), en)).toBe("Could not reach the server.");
    expect(importErrorMessage(new TypeError("Failed to fetch"), ar)).toBe("تعذر الوصول إلى الخادم.");
  });
});

describe("hostLabel", () => {
  it("shows the host of what was typed", () => {
    expect(hostLabel("https://www.Example.com/about?x=1")).toBe("example.com");
    expect(hostLabel("example.com")).toBe("example.com");
    expect(hostLabel("  ")).toBe("");
  });
});
