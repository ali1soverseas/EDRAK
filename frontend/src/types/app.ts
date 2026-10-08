/**
 * App-level types: everything the UI needs that the shared contracts do not define.
 *
 * Contract objects (BusinessRequest, WorkerResult, VerificationResult, ...) come from
 * the generated ./contracts file and are never re-declared here. Types in this file
 * either wrap contract objects or describe app-only data (users, company setup,
 * analysis rows, run progress, brief layout). See edrak-ui-handoff/docs/
 * mvp_screens_to_tables.md for which table each one maps to.
 */
import type {
  BusinessRequest,
  Evidence,
  EvidenceRelation,
  Finding,
  FindingCheckStatus,
  FindingVerdict,
  OrchestrationResult,
  ResearchPlan,
  UseCase,
  VerificationResult,
  WorkerResult,
  WorkerType,
} from "./contracts";

export type Lang = "en" | "ar";

/* ------------------------------------------------------------------ session */

export interface User {
  user_id: string;
  email: string;
  name: string;
  /** Free text such as "Head of Strategy". Roles are not part of the MVP. */
  title: string | null;
}

export interface Workspace {
  workspace_id: string;
  name: string;
}

export interface Session {
  user: User;
  workspace: Workspace;
}

/* ------------------------------------------------------------ company setup */

export type Industry = "fintech" | "saas" | "retail" | "logistics" | "other";

export type SocialPlatform = "facebook" | "instagram" | "x" | "youtube" | "linkedin";

export interface CompanyLink {
  link_id: string;
  platform: SocialPlatform;
  url: string;
}

export type DocumentStatus = "indexed" | "indexing" | "failed";

export interface CompanyDocument {
  document_id: string;
  filename: string;
  /** "14 pages", "3,420 rows". Null until the file has been read. */
  size: { unit: "pages" | "rows" | "kb"; value: number } | null;
  status: DocumentStatus;
}

/** Fields the company form edits. Maps to company_profiles and company_links. */
export interface CompanyFields {
  name: string;
  /** Contract: CompanyProfile.aliases */
  aliases: string[];
  industry: Industry | null;
  /** Contract: CompanyProfile.notes */
  description: string;
  /** Contract: CompanyProfile.products */
  offerings: string[];
  markets: string[];
  strategic_goals: string;
  website: string;
  socials: CompanyLink[];
}

export interface CompanySetup extends CompanyFields {
  documents: CompanyDocument[];
  /** False until the person has saved the form once. */
  completed: boolean;
}

/* ----------------------------------------------------------- analysis form */

export type SourceToggle =
  | "internal_files"
  | "competitor_web"
  | "news_open_data"
  | "reviews_social";

export type TargetCustomers = "smes" | "enterprise" | "both";

/** Keys for the "what to look at" chips. Mapped to plain-language focus areas on submit. */
export type FocusKey =
  | "features"
  | "pricing"
  | "positioning"
  | "sentiment"
  | "demand"
  | "regulation"
  | "competition"
  | "customers";

/** Flat form state for the New analysis screen. Which fields show depends on use_case. */
export interface AnalysisForm {
  use_case: UseCase;
  goal: string;
  /** Competitors to compare, known players, or known alternatives. */
  competitors: string[];
  /** Target market (market entry, product launch). */
  market: string;
  /** Proposed offering (product launch). */
  offering: string;
  target_customers: TargetCustomers | null;
  focus: FocusKey[];
  time_window_days: number | null;
  sources: Record<SourceToggle, boolean>;
  repeat_weekly: boolean;
}

/* ---------------------------------------------------------------- analyses */

export type AnalysisStatus =
  | "draft"
  | "awaiting_approval"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";

/** One row of the Analyses list. */
export interface AnalysisSummary {
  /** Equals the contract request_id (the run id). */
  analysis_id: string;
  title: string;
  use_case: UseCase;
  status: AnalysisStatus;
  /** ISO time the row last changed (plan drafted, run started, brief written, draft saved). */
  updated_at: string;
  repeat_weekly: boolean;
  tasks_total: number;
  tasks_done: number;
  tasks_failed: number;
  brief_id: string | null;
  brief_no: number | null;
  /** Verification passed on a completed run. */
  verified: boolean;
}

/** What Plan review needs. */
export interface AnalysisDetail {
  analysis_id: string;
  title: string;
  status: AnalysisStatus;
  request: BusinessRequest;
  plan: ResearchPlan | null;
  form: AnalysisForm;
  rejection_reason: string | null;
  /** Workers the person allowed, by the source toggles. */
  allowed_sources: SourceToggle[];
}

/* ---------------------------------------------------------------- live run */

export type RunStage = "plan" | "dispatch" | "workers" | "verify" | "synthesize" | "brief";

export type RunState = "running" | "completed" | "partial" | "failed" | "cancelled";

export type TaskState = "queued" | "running" | "done" | "failed";

export interface RunTaskView {
  task_id: string;
  worker: WorkerType;
  state: TaskState;
  /** 0 to 100 */
  progress: number;
  sources: number;
  /** The current activity line. Text written by the worker. */
  activity: string;
  log: Array<{ at: string; text: string }>;
  error: string | null;
}

export type RunEventKind = "info" | "gap" | "replan" | "failure" | "decision";

export interface RunEvent {
  event_id: string;
  at: string;
  /** "supervisor" for control-plane events, otherwise the worker. */
  source: "supervisor" | WorkerType;
  kind: RunEventKind;
  message: string;
  /** Extra callout under the message (a re-plan, or a Supervisor decision). */
  callout: { text: string; highlight: string | null } | null;
}

export interface RunView {
  analysis_id: string;
  title: string;
  use_case: UseCase;
  state: RunState;
  stage: RunStage;
  approved_at: string;
  approved_by: string;
  started_at: string;
  finished_at: string | null;
  tasks: RunTaskView[];
  events: RunEvent[];
  verification: "waiting" | "running" | "done";
  brief_id: string | null;
}

/* ------------------------------------------------------------------- brief */

/** Inline text with highlights and evidence chips. Built by the brief layout layer. */
export type RichSegment =
  | { t: "text"; text: string }
  | { t: "mark"; text: string }
  | { t: "ev"; worker: WorkerType; evidence_id: string };

export interface BriefSummaryText {
  found: RichSegment[];
  matters: RichSegment[];
  options: RichSegment[];
  /** A short limits statement shown under the headline on a verified brief. */
  limits: string | null;
}

export interface BriefOption {
  option_id: string;
  key: "A" | "B" | "C";
  title: string;
  description: string;
  supported_by: string;
  would_change_if: string;
}

/** The cross-signal hypothesis. Optional: the contracts have no synthesis object yet. */
export interface BriefSynthesis {
  statement: string;
  highlight: string;
  /** Support line, for example "A hypothesis backed by four independent lines of evidence." */
  support: string;
}

export type SourceNoteKind =
  | "your_data"
  | "primary"
  | "vendor_claim"
  | "opinion"
  | "open_data"
  | "news"
  | "thin";

export type Reliability = 1 | 2 | 3;

export interface BriefEvidence {
  /** The number in the E-chip. Unique within the brief. */
  display_no: number;
  evidence: Evidence;
  worker: WorkerType;
  task_id: string;
  attempt: number;
  reliability: Reliability;
  note: SourceNoteKind;
  /** Internal when it is the person's own file, external otherwise. */
  origin: "internal" | "external";
}

export interface BriefEvidenceRef {
  display_no: number;
  relation: EvidenceRelation;
  /** Key into Brief.evidence_by_key. */
  key: string;
}

export interface BriefFinding {
  finding: Finding;
  worker: WorkerType;
  verdict: FindingVerdict | null;
  status: FindingCheckStatus | "unchecked";
  reliability: Reliability;
  refs: BriefEvidenceRef[];
}

export interface BriefLens {
  worker: WorkerType;
  result: WorkerResult | null;
  failed: boolean;
  findings: BriefFinding[];
  source_count: number;
}

export interface BriefGap {
  text: string;
  /** "verification" when it comes from the verifier's missing information. */
  reported_by: WorkerType | "verification";
}

export interface Brief {
  brief_id: string;
  brief_no: number;
  analysis_id: string;
  /** The analysis name, for the breadcrumb. */
  analysis_title: string;
  /** The brief's headline question. */
  title: string;
  use_case: UseCase;
  created_at: string;
  requested_by: string;
  partial: boolean;
  request: BusinessRequest;
  orchestration: OrchestrationResult;
  verification: VerificationResult;
  summary: BriefSummaryText;
  lenses: BriefLens[];
  evidence: BriefEvidence[];
  options: BriefOption[];
  gaps: BriefGap[];
  next_steps: string[];
  synthesis: BriefSynthesis | null;
}

/** Key for looking an evidence item up. Evidence ids are only unique within one result. */
export function evidenceKey(worker: WorkerType, evidenceId: string): string {
  return `${worker}:${evidenceId}`;
}

export type { UseCase, WorkerType };
