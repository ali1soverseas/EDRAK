/**
 * Seed data for the mock database. It reproduces the six analyses and the Nile Ledger
 * workspace from the design handoff, with dates relative to now so "12 min ago" and
 * "Yesterday" stay true whenever the app is opened.
 *
 * Demo sign-in: layla@nileledger.example / edrak-demo
 */
import type { AnalysisForm, CompanyFields } from "../../types/app";
import type { UseCase } from "../../types/contracts";
import { defaultForm, formToRequest, deriveTitle } from "../../lib/form";
import type { AnalysisRecord, MockDb, StoredCompany, StoredDocument } from "./db";
import { planFor } from "./planner";
import type { RunRecord } from "./simulate";

export const DEMO_EMAIL = "layla@nileledger.example";
export const DEMO_PASSWORD = "edrak-demo";
export const WORKSPACE_ID = "ws-nile-ledger";
const USER_ID = "user-layla";

/** A small non-cryptographic hash. The mock has no real authentication. */
export function mockHash(text: string): string {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ code, 2654435761);
    h2 = Math.imul(h2 ^ code, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16);
}

export function passwordHash(email: string, password: string): string {
  return mockHash(`${email.trim().toLowerCase()}:${password}`);
}

export function emptyCompany(): StoredCompany {
  return {
    name: "",
    aliases: [],
    industry: null,
    description: "",
    offerings: [],
    markets: [],
    strategic_goals: "",
    website: "",
    socials: [],
    documents: [],
    completed: false,
  };
}

const nileLedger = (): CompanyFields => ({
  name: "Nile Ledger",
  aliases: ["NileLedger", "NL Pay"],
  industry: "fintech",
  description: "Invoicing and payments software for small and mid-sized businesses in Egypt.",
  offerings: ["Invoicing platform", "Payments API", "Reconciliation"],
  markets: ["Egypt", "Saudi Arabia"],
  strategic_goals:
    "Grow SME accounts. Reduce churn in the second year. Explore adjacent products for existing customers.",
  website: "https://nileledger.example",
  socials: [
    { link_id: "link-fb", platform: "facebook", url: "https://facebook.com/nileledger" },
    { link_id: "link-ig", platform: "instagram", url: "https://instagram.com/nileledger" },
  ],
});

function documents(): StoredDocument[] {
  return [
    { document_id: "doc-1", filename: "product-overview-2026.pdf", size: { unit: "pages", value: 14 }, status: "indexed", ready_at: null, will_fail: false },
    { document_id: "doc-2", filename: "support-tickets-2026.csv", size: { unit: "rows", value: 3420 }, status: "indexed", ready_at: null, will_fail: false },
    { document_id: "doc-3", filename: "pricing-sheet.xlsx", size: null, status: "failed", ready_at: null, will_fail: true },
  ];
}

function form(useCase: UseCase, patch: Partial<AnalysisForm>): AnalysisForm {
  return { ...defaultForm(useCase), ...patch };
}

export function createSeedDb(now: number = Date.now()): MockDb {
  const iso = (ms: number) => new Date(ms).toISOString();
  const minutesAgo = (n: number) => now - n * 60000;
  /** Local time `hour:minute` on the day that was `days` days ago. */
  const dayAt = (days: number, hour: number, minute: number, second = 0) => {
    const d = new Date(now);
    d.setDate(d.getDate() - days);
    d.setHours(hour, minute, second, 0);
    return d.getTime();
  };

  const company = nileLedger();
  const user = "Layla Hassan";

  const make = (
    id: string,
    f: AnalysisForm,
    status: AnalysisRecord["status"],
    updatedAt: number,
    run: RunRecord | null,
    briefNo: number | null,
    withPlan: boolean,
    title?: string,
  ): AnalysisRecord => {
    const request = formToRequest(f, company, id);
    return {
      analysis_id: id,
      title: title ?? deriveTitle(f),
      form: f,
      request,
      plan: withPlan ? planFor(request, f) : null,
      status,
      rejection_reason: null,
      updated_at: iso(updatedAt),
      run,
      brief_no: briefNo,
    };
  };

  const approvedBy = user;
  const analyses: AnalysisRecord[] = [
    make(
      "an-ai-support",
      form("product_launch", {
        goal: "Should we add an AI customer-support product to our SaaS platform for SMEs in Egypt, and how would it differ from what exists?",
        offering: "AI customer-support assistant for our SaaS platform",
        target_customers: "smes",
        market: "Egypt",
        competitors: ["Ansar Desk", "Nabra AI"],
      }),
      "awaiting_approval",
      minutesAgo(12),
      null,
      null,
      true,
      "AI customer-support product",
    ),
    make(
      "an-fintech-live",
      form("competitive_intelligence", {
        goal: "What are Egypt's fintech competitors doing on pricing and onboarding, and what does it mean for us?",
        competitors: ["Misr Pay", "Delta Invoice", "Karim Ledger"],
        market: "Egypt",
        focus: ["pricing", "features"],
        time_window_days: 90,
      }),
      "running",
      minutesAgo(25),
      // A slow mock clock keeps this seeded run in progress for a while.
      { approved_at: iso(minutesAgo(25)), approved_by: approvedBy, speed: 0.018, scenario: "normal", cancelled_at: null },
      4,
      true,
      "Egypt fintech competitors",
    ),
    make(
      "an-saudi",
      form("market_entry_expansion", {
        goal: "Is Saudi Arabia the right next market for Nile Ledger?",
        market: "Saudi Arabia",
        competitors: ["Tamam Books", "Riyada Invoice"],
        focus: ["demand", "regulation"],
      }),
      "running",
      dayAt(1, 16, 18, 30),
      { approved_at: iso(dayAt(1, 16, 18, 30)), approved_by: approvedBy, speed: 1, scenario: "market_fails", cancelled_at: null },
      3,
      true,
    ),
    make(
      "an-fintech-w39",
      form("competitive_intelligence", {
        goal: "What are Egypt's fintech competitors doing on pricing and onboarding, week 39?",
        competitors: ["Misr Pay", "Delta Invoice", "Karim Ledger"],
        market: "Egypt",
        focus: ["pricing", "features"],
        time_window_days: 30,
        repeat_weekly: true,
      }),
      "running",
      dayAt(8, 8, 13),
      { approved_at: iso(dayAt(8, 8, 13)), approved_by: approvedBy, speed: 1, scenario: "normal", cancelled_at: null },
      2,
      true,
      "Egypt fintech competitors, week 39",
    ),
    make(
      "an-retail",
      form("competitive_intelligence", {
        goal: "How do retail loyalty programs in Egypt compare with ours?",
        market: "Egypt",
      }),
      "running",
      dayAt(11, 11, 1, 30),
      { approved_at: iso(dayAt(11, 11, 1, 30)), approved_by: approvedBy, speed: 1, scenario: "run_fails", cancelled_at: null },
      null,
      true,
      "Retail loyalty benchmark",
    ),
    make(
      "an-cairo",
      form("market_entry_expansion", {
        goal: "How should we price our invoicing product for logistics companies in Cairo?",
        market: "Cairo logistics",
      }),
      "draft",
      dayAt(13, 10, 5),
      null,
      null,
      false,
      "Cairo logistics pricing",
    ),
  ];

  return {
    version: 1,
    users: [
      {
        user_id: USER_ID,
        email: DEMO_EMAIL,
        password_hash: passwordHash(DEMO_EMAIL, DEMO_PASSWORD),
        name: user,
        title: "Head of Strategy",
        workspace_id: WORKSPACE_ID,
      },
    ],
    workspaces: [{ workspace_id: WORKSPACE_ID, name: "Nile Ledger" }],
    companies: { [WORKSPACE_ID]: { ...company, documents: documents(), completed: true } },
    analyses: { [WORKSPACE_ID]: analyses },
    next_brief_no: { [WORKSPACE_ID]: 5 },
    session_user_id: null,
  };
}
