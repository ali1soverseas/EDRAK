/**
 * Demo content for the mock API. Nothing here is real: companies, sources, quotes and
 * numbers are invented so every screen has data of the right shape. The company in the
 * seed data (Nile Ledger) and the example domains come from the design handoff.
 *
 * Each pack is the content of one finished analysis for one use case, written as the
 * pieces the backend would produce: worker evidence and findings, verification
 * verdicts, and the brief layer's summary, options and synthesis.
 */
import type {
  EvidenceQuality,
  FindingCategory,
  FindingCheckStatus,
  SourceType,
  UseCase,
  WorkerType,
} from "../../types/contracts";
import type { BriefSummaryText, BriefSynthesis, RichSegment } from "../../types/app";

export interface PackEvidence {
  id: string;
  type: SourceType;
  title: string;
  publisher: string;
  url: string | null;
  fact: string;
  excerpt: string | null;
  /** File name for the person's own documents. Goes in the evidence metadata. */
  file?: string;
}

export interface PackFinding {
  id: string;
  statement: string;
  category: FindingCategory;
  refs: string[];
  confidence: number;
  quality: EvidenceQuality;
  check: FindingCheckStatus;
  missing?: string[];
}

export interface PackWorker {
  evidence: PackEvidence[];
  findings: PackFinding[];
  gaps: string[];
  /** Current-activity lines, shown in order as the worker's progress grows. */
  activity: string[];
  done: string;
  /** Log lines that appear once progress passes `at` (0 to 1). */
  log: Array<{ at: number; text: string }>;
}

export interface PackOption {
  key: "A" | "B" | "C";
  title: string;
  description: string;
  supported_by: string;
  would_change_if: string;
}

export interface Pack {
  headline: string;
  workers: Record<WorkerType, PackWorker>;
  summary: BriefSummaryText;
  /** The summary when the Market worker failed. */
  summary_partial: BriefSummaryText;
  options: PackOption[];
  synthesis: BriefSynthesis;
  synthesis_partial: BriefSynthesis;
  missing_information: string[];
  next_steps: string[];
  /** The Supervisor adds one targeted task while the Competitor worker runs. */
  replan: { gap: string; task: string; reason: string };
}

const T = (text: string): RichSegment => ({ t: "text", text });
const M = (text: string): RichSegment => ({ t: "mark", text });
const E = (worker: WorkerType, evidence_id: string): RichSegment => ({ t: "ev", worker, evidence_id });
const INT: WorkerType = "internal_intelligence";
const COMP: WorkerType = "competitor_intelligence";
const MKT: WorkerType = "market_intelligence";
const CUST: WorkerType = "customer_trends";

/** Which of the company's files an internal source came from. */
const FILE_BY_TITLE: Record<string, string> = {
  "Arabic language pipeline": "product-overview-2026.pdf",
  "Support tickets tagged by language": "support-tickets-2026.csv",
  "Human handoff in the support console": "product-overview-2026.pdf",
  "WhatsApp Business integration status": "product-overview-2026.pdf",
  "Hosting and data residency": "architecture-notes.pdf",
  "Complaints about automated replies": "support-tickets-2026.csv",
  "Handoff complaints in tickets": "support-tickets-2026.csv",
  "Pricing sheet, Q3 2026": "pricing-sheet-q3.pdf",
  "Onboarding funnel report": "onboarding-funnel-2026.csv",
  "Win and loss notes": "win-loss-notes.docx",
  "Churned customer exit notes": "exit-notes-2026.csv",
  "Product overview 2026": "product-overview-2026.pdf",
  "Support coverage": "support-handbook.pdf",
};

const ev = (
  id: string,
  type: SourceType,
  title: string,
  publisher: string,
  url: string | null,
  fact: string,
  excerpt: string | null = null,
): PackEvidence => ({ id, type, title, publisher, url, fact, excerpt, file: type === "internal_document" ? FILE_BY_TITLE[title] : undefined });

/* ------------------------------------------------------------ product launch */

const productLaunch: Pack = {
  headline: "Is there a case for adding AI customer support?",
  workers: {
    internal_intelligence: {
      evidence: [
        ev("i1", "internal_document", "Arabic language pipeline", "Nile Ledger", null,
          "Nile Ledger already runs an in-house Arabic language pipeline that normalises Egyptian and Gulf dialect text.",
          "The Arabic NLP service normalises dialect spellings before routing. It covers Egyptian and Gulf Arabic."),
        ev("i2", "internal_document", "Support tickets tagged by language", "Nile Ledger", null,
          "Every support ticket in 2026 carries a language tag. 38% of 3,420 tickets are tagged Egyptian Arabic.",
          "language: ar-EG, count: 1,300 of 3,420"),
        ev("i3", "internal_document", "Human handoff in the support console", "Nile Ledger", null,
          "The support console already hands a conversation to a person with the full history attached.",
          "Escalate to agent keeps the transcript and customer record."),
        ev("i4", "internal_document", "WhatsApp Business integration status", "Nile Ledger", null,
          "A WhatsApp Business integration is in pilot with 40 customer accounts.",
          null),
        ev("i5", "internal_document", "Hosting and data residency", "Nile Ledger", null,
          "Customer data is stored in an Egyptian region. Model inference would run outside Egypt.",
          "Primary region: Cairo. Inference providers: none in region."),
      ],
      findings: [
        { id: "f1", statement: "An in-house Arabic language pipeline already exists.", category: "strength",
          refs: ["i1", "i3"], confidence: 0.86, quality: "high", check: "verified" },
        { id: "f2", statement: "Support tickets are already tagged by language.", category: "strength",
          refs: ["i2"], confidence: 0.72, quality: "medium", check: "verified" },
        { id: "f3", statement: "No data on how support quality affects churn.", category: "gap",
          refs: [], confidence: 0.3, quality: "low", check: "insufficient",
          missing: ["Churn by support-quality segment"] },
      ],
      gaps: [],
      activity: [
        "Reading the product overview and architecture notes",
        "Searching support tickets for language and handoff",
        "Checking hosting and data residency",
        "Writing findings from 5 sources",
      ],
      done: "Finished searching 3 documents and the support-ticket export",
      log: [
        { at: 0.1, text: "Indexed files found: 3 documents, 1 export" },
        { at: 0.5, text: "Searched: Arabic, dialect, handoff, WhatsApp" },
        { at: 0.9, text: "Kept 5 passages as usable" },
      ],
    },
    competitor_intelligence: {
      evidence: [
        ev("c1", "official_documentation", "Ansar Desk language docs", "Ansar Desk", "https://ansardesk.example/docs/languages",
          "Ansar Desk's assistant understands Modern Standard Arabic and English. Regional dialects are planned.",
          "The assistant understands Modern Standard Arabic and English. Regional dialects are planned."),
        ev("c2", "web_page", "Nabra AI feature page", "Nabra AI", "https://nabra.example/features",
          "Nabra AI lists Arabic as standard-only and mentions limited dialect handling.",
          "Arabic (standard). Dialect support: limited beta."),
        ev("c3", "web_page", "Sada Support homepage", "Sada Support", "https://sada.example",
          "Sada Support's homepage does not say which languages it supports or what it costs.",
          null),
        ev("c4", "pricing_page", "Ansar Desk pricing", "Ansar Desk", "https://ansardesk.example/pricing",
          "Ansar Desk publishes tiered pricing from EGP 1,200 a month.",
          "Starter EGP 1,200 / month. Growth EGP 3,400 / month."),
        ev("c5", "pricing_page", "Nabra AI pricing", "Nabra AI", "https://nabra.example/pricing",
          "Nabra AI charges per agent seat from EGP 900 a month.",
          "From EGP 900 per agent per month."),
        ev("c6", "official_documentation", "Ansar Desk WhatsApp channel", "Ansar Desk", "https://ansardesk.example/docs/whatsapp",
          "Ansar Desk includes a WhatsApp channel on every plan.",
          "WhatsApp is available on all plans."),
        ev("c7", "web_page", "Nabra AI supported channels", "Nabra AI", "https://nabra.example/channels",
          "Nabra AI supports WhatsApp, Messenger and web chat.",
          null),
        ev("c8", "web_page", "Sada Support channels", "Sada Support", "https://sada.example/channels",
          "Sada Support lists WhatsApp among its channels.",
          null),
      ],
      findings: [
        { id: "f1", statement: "Two of three alternatives cover standard Arabic only.", category: "positioning",
          refs: ["c1", "c2"], confidence: 0.84, quality: "high", check: "verified" },
        { id: "f2", statement: "WhatsApp support is common across the alternatives.", category: "product_feature",
          refs: ["c6", "c7", "c8"], confidence: 0.88, quality: "high", check: "verified" },
        { id: "f3", statement: "Sada Support publishes neither Arabic support nor pricing.", category: "gap",
          refs: ["c3"], confidence: 0.35, quality: "low", check: "insufficient",
          missing: ["Public pricing for Sada Support"] },
      ],
      gaps: ["Pricing for Sada Support was not public"],
      activity: [
        "Searching for AI support products for SMEs in Egypt",
        "Comparing features and pricing across three alternatives",
        "Opening pricing and documentation pages",
        "Writing findings from 8 sources",
      ],
      done: "Finished comparing features and pricing across three alternatives",
      log: [
        { at: 0.15, text: "Searched: AI customer support, Egypt, SMEs" },
        { at: 0.45, text: "Opened 6 pages, kept 4 as usable" },
        { at: 0.75, text: "Pricing not found for one alternative, reported as a gap" },
      ],
    },
    market_intelligence: {
      evidence: [
        ev("m1", "market_report", "Search interest, Arabic support tools", "Trends dataset", "https://trends.example/arabic-support",
          "Search interest in Arabic customer-support tools rose 41% year on year.",
          "Index 62 to 88, 12 months to September 2026."),
        ev("m2", "news_article", "Egyptian SMEs move support to chat", "Business Daily", "https://news.example/sme-chat",
          "Egyptian SMEs are moving customer support from phone to chat apps.",
          null),
        ev("m3", "regulatory", "Rules on automated customer support", "Consumer regulator", "https://regulator.example/automated-support",
          "No Egyptian rule blocks automated customer support. A customer can ask for a person.",
          "A consumer may ask to speak with a person at any time."),
        ev("m4", "news_article", "Support vendors add AI features", "Tech Weekly", "https://news.example/vendors-ai",
          "Regional support vendors are adding AI features to their plans.",
          null),
        ev("m5", "economic", "SME digital spending outlook", "Economic institute", "https://economy.example/sme-outlook",
          "SME spending on digital tools is forecast to grow next year.",
          null),
      ],
      findings: [
        { id: "f1", statement: "Interest in Arabic-language support tools is rising.", category: "market_signal",
          refs: ["m1", "m2"], confidence: 0.74, quality: "medium", check: "verified" },
        { id: "f2", statement: "No rule found that blocks automated customer support.", category: "risk",
          refs: ["m3"], confidence: 0.9, quality: "high", check: "verified" },
        { id: "f3", statement: "Competitors are adding AI features to their support plans.", category: "market_signal",
          refs: ["m4"], confidence: 0.7, quality: "medium", check: "verified" },
      ],
      gaps: ["No evidence on hosting for banking clients"],
      activity: [
        "Reading category news and open datasets",
        "Checking rules on automated customer support",
        "Reading spending outlooks",
        "Writing findings from 5 sources",
      ],
      done: "Finished reading category news and open datasets",
      log: [
        { at: 0.2, text: "Searched news: Arabic support tools, SME chat" },
        { at: 0.55, text: "Checked regulator pages for automated support" },
        { at: 0.85, text: "No hosting rules found for banking clients, reported as a gap" },
      ],
    },
    customer_trends: {
      evidence: [
        ev("u1", "internal_document", "Complaints about automated replies", "Nile Ledger", null,
          "212 of 3,420 tickets say an automated reply misread Arabic phrasing.",
          "tag: bot-misread, count: 212"),
        ev("u2", "review_site", "Forum threads on bot replies", "Community forum", "https://forum.example/threads/bot-replies",
          "Forum members say chatbot replies miss Egyptian phrasing.",
          "The bot understands formal Arabic but not how people actually write."),
        ev("u3", "review_site", "App store reviews of support bots", "App reviews", "https://reviews.example/support-bots",
          "Reviewers ask for support on WhatsApp and complain about replies in the wrong dialect.",
          null),
        ev("u4", "review_site", "Review thread on slow handoff", "App reviews", "https://reviews.example/handoff",
          "Reviewers report waiting too long to reach a person.",
          null),
        ev("u5", "internal_document", "Handoff complaints in tickets", "Nile Ledger", null,
          "96 tickets mention a slow handoff from the bot to a person.",
          "tag: slow-handoff, count: 96"),
        ev("u6", "review_site", "Social posts on support wait times", "Social posts", "https://social.example/support-waits",
          "Posts describe repeated handoffs between support channels.",
          null),
        ev("u7", "review_site", "Forum request for WhatsApp support", "Community forum", "https://forum.example/threads/whatsapp",
          "A forum thread asks vendors to offer support on WhatsApp.",
          null),
      ],
      findings: [
        { id: "f1", statement: "Customers say automated replies miss Egyptian phrasing.", category: "customer_sentiment",
          refs: ["u1", "u2", "u3"], confidence: 0.85, quality: "high", check: "verified" },
        { id: "f2", statement: "Complaints about slow handoff to a person recur.", category: "customer_sentiment",
          refs: ["u4", "u5"], confidence: 0.7, quality: "medium", check: "verified" },
        { id: "f3", statement: "Reviews ask for support on WhatsApp.", category: "customer_sentiment",
          refs: ["u7"], confidence: 0.66, quality: "medium", check: "verified" },
      ],
      gaps: [],
      activity: [
        "Reading reviews and forum threads",
        "Clustering complaints from reviews and tickets",
        "Counting repeated requests",
        "Writing findings from 7 sources",
      ],
      done: "Finished clustering complaints from reviews and tickets",
      log: [
        { at: 0.2, text: "Collected 38 review and forum posts" },
        { at: 0.6, text: "Grouped complaints into three themes" },
        { at: 0.9, text: "Kept 7 sources as usable" },
      ],
    },
  },
  summary: {
    found: [
      T("Small businesses in Egypt keep reporting slow, repeated handoffs in support, and many describe automated replies that "),
      M("miss Arabic dialects"), E(CUST, "u1"), E(CUST, "u2"),
      T(". Two of the three alternatives we reviewed handle standard Arabic only"), E(COMP, "c1"), E(COMP, "c2"),
      T(". Our own documents show an in-house Arabic language pipeline already exists"), E(INT, "i1"), T("."),
    ],
    matters: [
      T("Customer demand, competitor coverage and our own capability line up in one area. Category interest is rising"),
      E(MKT, "m1"),
      T(". We have no pricing evidence for one alternative and no data on how support quality affects churn."),
    ],
    options: [
      T("Scope a small pilot, close the gaps before deciding, or defer. Edrak lays out the evidence for each. "),
      M("The decision stays with you."),
    ],
    limits:
      "Pricing for Sada Support was not found, and there is no data on how support quality affects churn. Both are listed under Options and next steps.",
  },
  summary_partial: {
    found: [
      T("Small businesses in Egypt keep reporting slow, repeated handoffs in support, and many describe automated replies that "),
      M("miss Arabic dialects"), E(CUST, "u1"), E(CUST, "u2"),
      T(". Two of the three alternatives we reviewed handle standard Arabic only"), E(COMP, "c1"), E(COMP, "c2"),
      T(". Our own documents show an in-house Arabic language pipeline already exists"), E(INT, "i1"), T("."),
    ],
    matters: [
      T("Customer demand, competitor coverage and our own capability line up in one area. "),
      M("We have no market evidence"),
      T(", so category growth and regulation are untested. Pricing for one alternative and the link to churn are also missing."),
    ],
    options: [
      T("Scope a small pilot, close the gaps before deciding, or defer. Edrak lays out the evidence for each. "),
      M("The decision stays with you."),
    ],
    limits: null,
  },
  options: [
    { key: "A", title: "Scope a pilot",
      description: "Test Arabic-first support with a small group of existing customers.",
      supported_by: "Demand, competitor gap, in-house capability",
      would_change_if: "An alternative ships dialect support" },
    { key: "B", title: "Close the gaps first",
      description: "Find Sada Support pricing and churn data, then revisit this brief.",
      supported_by: "Two open information gaps",
      would_change_if: "The gaps prove immaterial" },
    { key: "C", title: "Defer",
      description: "Keep an eye on the category and the competitors.",
      supported_by: "Rising interest, unresolved pricing",
      would_change_if: "Demand signals weaken" },
  ],
  synthesis: {
    statement: "Arabic dialect handling is a potential point of differentiation worth investigating.",
    highlight: "potential point of differentiation",
    support: "A hypothesis backed by four independent lines of evidence. Not a recommendation.",
  },
  synthesis_partial: {
    statement: "Arabic dialect handling is a potential point of differentiation worth investigating.",
    highlight: "potential point of differentiation",
    support: "A hypothesis backed by three lines of evidence. Market evidence is missing. Not a recommendation.",
  },
  missing_information: ["No data on support quality and churn"],
  next_steps: [
    "Find public pricing for Sada Support, then start a new analysis.",
    "Upload retention data in Company so the churn link can be checked.",
  ],
  replan: {
    gap: "Gap reported: pricing not found for one alternative.",
    task: "find pricing pages for Sada Support",
    reason: "pricing is needed for the comparison",
  },
};

/* ------------------------------------------------------ competitive intelligence */

const competitive: Pack = {
  headline: "How are Egypt's fintech competitors moving on pricing and onboarding?",
  workers: {
    internal_intelligence: {
      evidence: [
        ev("i1", "internal_document", "Pricing sheet, Q3 2026", "Nile Ledger", null,
          "Nile Ledger's Growth plan is EGP 2,900 a month, billed yearly.", "Growth: EGP 2,900 / month, annual."),
        ev("i2", "internal_document", "Onboarding funnel report", "Nile Ledger", null,
          "Self-serve onboarding takes a median of 3 days. 22% of sign-ups finish setup in the first hour.", null),
        ev("i3", "internal_document", "Win and loss notes", "Nile Ledger", null,
          "Lost deals in Q3 cite onboarding time more often than price.", null),
      ],
      findings: [
        { id: "f1", statement: "Our Growth plan is priced above two of the three competitors.", category: "pricing_packaging",
          refs: ["i1"], confidence: 0.8, quality: "high", check: "verified" },
        { id: "f2", statement: "Lost deals cite onboarding time more than price.", category: "gap",
          refs: ["i2", "i3"], confidence: 0.7, quality: "medium", check: "verified" },
      ],
      gaps: [],
      activity: ["Reading pricing and funnel reports", "Searching win and loss notes", "Writing findings from 3 sources"],
      done: "Finished searching 3 internal documents",
      log: [{ at: 0.3, text: "Indexed files found: 3 documents" }, { at: 0.8, text: "Kept 3 passages as usable" }],
    },
    competitor_intelligence: {
      evidence: [
        ev("c1", "pricing_page", "Misr Pay pricing", "Misr Pay", "https://misrpay.example/pricing",
          "Misr Pay cut its mid-tier plan to EGP 2,100 a month in September.", "Business: EGP 2,100 / month."),
        ev("c2", "pricing_page", "Delta Invoice pricing", "Delta Invoice", "https://deltainvoice.example/pricing",
          "Delta Invoice offers a free tier for up to 20 invoices a month.", "Free: 20 invoices / month."),
        ev("c3", "release_notes", "Karim Ledger release notes", "Karim Ledger", "https://karimledger.example/releases",
          "Karim Ledger shipped instant bank onboarding in August.", null),
        ev("c4", "announcement", "Misr Pay onboarding announcement", "Misr Pay", "https://misrpay.example/blog/onboarding",
          "Misr Pay says new customers can start invoicing in one day.", null),
      ],
      findings: [
        { id: "f1", statement: "Two competitors lowered or removed entry pricing this quarter.", category: "pricing_packaging",
          refs: ["c1", "c2"], confidence: 0.82, quality: "high", check: "verified" },
        { id: "f2", statement: "Faster onboarding is now a headline claim for competitors.", category: "positioning",
          refs: ["c3", "c4"], confidence: 0.7, quality: "medium", check: "verified" },
      ],
      gaps: ["Karim Ledger pricing was not public"],
      activity: ["Searching competitor pricing pages", "Comparing plans and onboarding claims", "Writing findings from 4 sources"],
      done: "Finished comparing pricing and onboarding across three competitors",
      log: [{ at: 0.3, text: "Searched: invoicing software Egypt pricing" }, { at: 0.7, text: "Pricing not found for one competitor, reported as a gap" }],
    },
    market_intelligence: {
      evidence: [
        ev("m1", "news_article", "Egyptian fintech funding round", "Business Daily", "https://news.example/fintech-round",
          "A competing invoicing platform raised new funding for regional expansion.", null),
        ev("m2", "regulatory", "Central bank notice on digital payments", "Central bank", "https://cbe.example/notice",
          "A new notice sets onboarding checks for digital payment providers.", null),
      ],
      findings: [
        { id: "f1", statement: "Competitors have fresh funding aimed at faster onboarding.", category: "market_signal",
          refs: ["m1"], confidence: 0.65, quality: "medium", check: "verified" },
        { id: "f2", statement: "A new notice adds onboarding checks for payment providers.", category: "risk",
          refs: ["m2"], confidence: 0.8, quality: "high", check: "verified" },
      ],
      gaps: [],
      activity: ["Reading category news", "Checking regulator notices", "Writing findings from 2 sources"],
      done: "Finished reading category news and notices",
      log: [{ at: 0.4, text: "Searched news: fintech funding, invoicing" }, { at: 0.8, text: "Checked central bank notices" }],
    },
    customer_trends: {
      evidence: [
        ev("u1", "review_site", "Reviews of Misr Pay", "App reviews", "https://reviews.example/misr-pay",
          "Reviewers praise Misr Pay's quick setup and criticise its reporting.", null),
        ev("u2", "review_site", "Reviews of Delta Invoice", "App reviews", "https://reviews.example/delta-invoice",
          "Reviewers say Delta Invoice is easy to start but hard to reconcile at scale.", null),
        ev("u3", "internal_document", "Churned customer exit notes", "Nile Ledger", null,
          "Exit notes from 31 churned accounts mention onboarding effort as a reason.", null),
      ],
      findings: [
        { id: "f1", statement: "Customers reward fast setup and punish weak reporting.", category: "customer_sentiment",
          refs: ["u1", "u2"], confidence: 0.72, quality: "medium", check: "verified" },
        { id: "f2", statement: "Onboarding effort shows up in our own exit notes.", category: "customer_sentiment",
          refs: ["u3"], confidence: 0.68, quality: "medium", check: "verified" },
      ],
      gaps: [],
      activity: ["Reading reviews", "Clustering praise and complaints", "Writing findings from 3 sources"],
      done: "Finished clustering praise and complaints",
      log: [{ at: 0.4, text: "Collected 54 review posts" }, { at: 0.85, text: "Grouped feedback into three themes" }],
    },
  },
  summary: {
    found: [
      T("Two competitors lowered or removed entry pricing this quarter"), E(COMP, "c1"), E(COMP, "c2"),
      T(", and faster onboarding is now a headline claim"), E(COMP, "c4"),
      T(". Our own notes show "), M("lost deals cite onboarding time more than price"), E(INT, "i3"), T("."),
    ],
    matters: [
      T("Customers reward fast setup"), E(CUST, "u1"),
      T(". Funding is flowing to competitors who are building for exactly that"), E(MKT, "m1"),
      T(". We have no pricing for Karim Ledger."),
    ],
    options: [
      T("Match entry pricing, speed up onboarding, or hold and watch. Edrak lays out the evidence for each. "),
      M("The decision stays with you."),
    ],
    limits: "Karim Ledger pricing was not public. It is listed under Options and next steps.",
  },
  summary_partial: {
    found: [T("Competitors moved on pricing and onboarding this quarter.")],
    matters: [T("The Market worker failed, so category news and regulation are untested.")],
    options: [T("Close the gaps first. "), M("The decision stays with you.")],
    limits: null,
  },
  options: [
    { key: "A", title: "Speed up onboarding", description: "Cut setup to one day for new SME accounts.",
      supported_by: "Win and loss notes, competitor claims", would_change_if: "Churn data shows price matters more" },
    { key: "B", title: "Match entry pricing", description: "Add a lower entry plan to meet Misr Pay and Delta Invoice.",
      supported_by: "Two competitors cut pricing", would_change_if: "Margins cannot support a lower plan" },
    { key: "C", title: "Hold and watch", description: "Keep tracking the three competitors weekly.",
      supported_by: "One pricing gap remains", would_change_if: "A fourth competitor cuts pricing" },
  ],
  synthesis: {
    statement: "Onboarding speed, not price, may be the deciding factor in lost deals.",
    highlight: "onboarding speed, not price",
    support: "A hypothesis backed by three independent lines of evidence. Not a recommendation.",
  },
  synthesis_partial: {
    statement: "Onboarding speed, not price, may be the deciding factor in lost deals.",
    highlight: "onboarding speed, not price",
    support: "A hypothesis backed by two lines of evidence. Market evidence is missing. Not a recommendation.",
  },
  missing_information: ["No churn data split by reason"],
  next_steps: ["Find public pricing for Karim Ledger.", "Upload churn data in Company so exit reasons can be checked."],
  replan: {
    gap: "Gap reported: pricing not found for one competitor.",
    task: "find pricing pages for Karim Ledger",
    reason: "pricing is needed for the comparison",
  },
};

/* --------------------------------------------------------- market entry */

const marketEntry: Pack = {
  headline: "Is Saudi Arabia the right next market?",
  workers: {
    internal_intelligence: {
      evidence: [
        ev("i1", "internal_document", "Product overview 2026", "Nile Ledger", null,
          "The product already has an Arabic interface and right-to-left invoices.", null),
        ev("i2", "internal_document", "Hosting and data residency", "Nile Ledger", null,
          "Data is hosted in Egypt only. There is no in-country hosting in Saudi Arabia.", null),
        ev("i3", "internal_document", "Support coverage", "Nile Ledger", null,
          "Support runs 9 to 18 Cairo time, Sunday to Thursday.", null),
      ],
      findings: [
        { id: "f1", statement: "The product already supports Arabic and right-to-left invoices.", category: "strength",
          refs: ["i1"], confidence: 0.85, quality: "high", check: "verified" },
        { id: "f2", statement: "There is no in-country hosting for Saudi customers.", category: "gap",
          refs: ["i2"], confidence: 0.8, quality: "high", check: "verified" },
      ],
      gaps: [],
      activity: ["Reading product and hosting documents", "Checking support coverage", "Writing findings from 3 sources"],
      done: "Finished searching 3 internal documents",
      log: [{ at: 0.3, text: "Indexed files found: 3 documents" }, { at: 0.8, text: "Kept 3 passages as usable" }],
    },
    competitor_intelligence: {
      evidence: [
        ev("c1", "web_page", "Tamam Books homepage", "Tamam Books", "https://tamambooks.example",
          "Tamam Books sells invoicing software to Saudi SMEs with local hosting.", null),
        ev("c2", "pricing_page", "Riyada Invoice pricing", "Riyada Invoice", "https://riyada.example/pricing",
          "Riyada Invoice starts at SAR 99 a month.", "From SAR 99 / month."),
        ev("c3", "official_documentation", "Riyada Invoice compliance page", "Riyada Invoice", "https://riyada.example/compliance",
          "Riyada Invoice states it meets national e-invoicing requirements.", null),
      ],
      findings: [
        { id: "f1", statement: "Local players already offer in-country hosting and e-invoicing compliance.", category: "positioning",
          refs: ["c1", "c3"], confidence: 0.78, quality: "medium", check: "verified" },
        { id: "f2", statement: "Entry pricing starts around SAR 99 a month.", category: "pricing_packaging",
          refs: ["c2"], confidence: 0.82, quality: "high", check: "verified" },
      ],
      gaps: ["Tamam Books pricing was not public"],
      activity: ["Searching for players in Saudi Arabia", "Comparing pricing and compliance claims", "Writing findings from 3 sources"],
      done: "Finished comparing the main Saudi players",
      log: [{ at: 0.3, text: "Searched: invoicing software Saudi Arabia" }, { at: 0.7, text: "Pricing not found for one player, reported as a gap" }],
    },
    market_intelligence: {
      evidence: [
        ev("m1", "market_report", "SME software market, Saudi Arabia", "Market research", "https://research.example/ksa-sme-software",
          "Saudi SME software spending is forecast to keep growing over the next three years.", null),
        ev("m2", "regulatory", "National e-invoicing requirements", "Tax authority", "https://tax.example/e-invoicing",
          "National rules require approved software for e-invoicing.", null),
        ev("m3", "regulatory", "Data residency guidance", "Data authority", "https://data.example/residency",
          "Guidance prefers in-country storage for some categories of customer data.", null),
      ],
      findings: [
        { id: "f1", statement: "The SME software market is growing.", category: "market_signal",
          refs: ["m1"], confidence: 0.7, quality: "medium", check: "verified" },
        { id: "f2", statement: "E-invoicing rules require approved software.", category: "risk",
          refs: ["m2"], confidence: 0.88, quality: "high", check: "verified" },
        { id: "f3", statement: "Some customer data may need to stay in the country.", category: "risk",
          refs: ["m3"], confidence: 0.6, quality: "medium", check: "verified" },
      ],
      gaps: [],
      activity: ["Reading market reports", "Checking e-invoicing and residency rules", "Writing findings from 3 sources"],
      done: "Finished reading market reports and rules",
      log: [{ at: 0.4, text: "Read 2 market reports" }, { at: 0.8, text: "Checked tax and data authority pages" }],
    },
    customer_trends: {
      evidence: [
        ev("u1", "review_site", "Saudi SME owner forum", "Community forum", "https://forum.example/ksa-sme",
          "Owners say their current invoicing tool is slow to adapt to new rules.", null),
        ev("u2", "review_site", "Reviews of local invoicing tools", "App reviews", "https://reviews.example/ksa-invoicing",
          "Reviewers want WhatsApp invoice delivery.", null),
      ],
      findings: [
        { id: "f1", statement: "Owners are frustrated by tools that lag new rules.", category: "customer_sentiment",
          refs: ["u1"], confidence: 0.62, quality: "medium", check: "verified" },
        { id: "f2", statement: "Reviewers ask for WhatsApp invoice delivery.", category: "customer_sentiment",
          refs: ["u2"], confidence: 0.6, quality: "medium", check: "verified" },
      ],
      gaps: [],
      activity: ["Reading forum posts and reviews", "Clustering requests", "Writing findings from 2 sources"],
      done: "Finished clustering requests from reviews and forums",
      log: [{ at: 0.4, text: "Collected 29 posts" }, { at: 0.85, text: "Grouped requests into two themes" }],
    },
  },
  summary: {
    found: [
      T("The market is growing"), E(MKT, "m1"),
      T(", but local players already offer in-country hosting and e-invoicing compliance"), E(COMP, "c1"), E(COMP, "c3"),
      T(". Our own documents show "), M("no in-country hosting"), E(INT, "i2"), T("."),
    ],
    matters: [
      T("Rules require approved e-invoicing software"), E(MKT, "m2"),
      T(" and may require local storage"), E(MKT, "m3"),
      T(". Owners want faster adaptation to rules"), E(CUST, "u1"), T("."),
    ],
    options: [
      T("Run a compliance study, partner with a local host, or defer. Edrak lays out the evidence for each. "),
      M("The decision stays with you."),
    ],
    limits: "Tamam Books pricing was not public. It is listed under Options and next steps.",
  },
  summary_partial: {
    found: [
      T("Local players already offer in-country hosting and e-invoicing compliance"), E(COMP, "c1"), E(COMP, "c3"),
      T(". Our own documents show "), M("no in-country hosting"), E(INT, "i2"), T("."),
    ],
    matters: [
      M("We have no market evidence"),
      T(", so market size and the rules that apply were not checked. Customer requests point to faster adaptation to rules."),
    ],
    options: [
      T("Close the gaps first, run a compliance study, or defer. Edrak lays out the evidence for each. "),
      M("The decision stays with you."),
    ],
    limits: null,
  },
  options: [
    { key: "A", title: "Run a compliance study", description: "Confirm what approved e-invoicing software and hosting require.",
      supported_by: "Rules on e-invoicing and residency", would_change_if: "The rules do not apply to our segment" },
    { key: "B", title: "Partner with a local host", description: "Serve Saudi customers from a partner's in-country hosting.",
      supported_by: "No in-country hosting, local players have it", would_change_if: "A partner cannot meet the rules" },
    { key: "C", title: "Defer", description: "Revisit when hosting and compliance are scoped.",
      supported_by: "Two open information gaps", would_change_if: "A competitor leaves the market" },
  ],
  synthesis: {
    statement: "In-country hosting and e-invoicing compliance are the gating items for Saudi Arabia.",
    highlight: "gating items",
    support: "A hypothesis backed by four independent lines of evidence. Not a recommendation.",
  },
  synthesis_partial: {
    statement: "In-country hosting looks like a gating item for Saudi Arabia.",
    highlight: "gating item",
    support: "A hypothesis backed by two lines of evidence. Market evidence is missing. Not a recommendation.",
  },
  missing_information: ["No data on the cost of in-country hosting"],
  next_steps: ["Find public pricing for Tamam Books.", "Get a quote for in-country hosting."],
  replan: {
    gap: "Gap reported: pricing not found for one player.",
    task: "find pricing pages for Tamam Books",
    reason: "pricing is needed for the comparison",
  },
};

export const PACKS: Record<UseCase, Pack> = {
  product_launch: productLaunch,
  competitive_intelligence: competitive,
  market_entry_expansion: marketEntry,
};
