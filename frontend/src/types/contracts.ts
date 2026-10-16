/* eslint-disable */
/**
 * GENERATED FILE. Do not edit by hand.
 * Source: backend/src/edrak/contracts (Pydantic models).
 * Regenerate: .venv/bin/python scripts/export_contract_schemas.py && npm run gen:types
 */

export type SocialPlatform = "website" | "x" | "facebook" | "instagram" | "linkedin" | "youtube" | "tiktok";
export type TriggerType = "on_demand" | "scheduled";
export type UseCase = "competitive_intelligence" | "market_entry_expansion" | "product_launch";
export type WorkerType =
  "internal_intelligence" | "competitor_intelligence" | "market_intelligence" | "customer_trends";
export type SourceType =
  | "web_page"
  | "search_result"
  | "official_documentation"
  | "pricing_page"
  | "release_notes"
  | "announcement"
  | "news_article"
  | "review_site"
  | "market_report"
  | "regulatory"
  | "economic"
  | "internal_document"
  | "synthetic_internal"
  | "other";
export type FindingCategory =
  | "product_feature"
  | "pricing_packaging"
  | "positioning"
  | "target_customer"
  | "strength"
  | "gap"
  | "market_signal"
  | "customer_sentiment"
  | "risk"
  | "opportunity"
  | "other";
export type EvidenceRelation = "supports" | "contradicts" | "contextualizes";
export type WorkerStatus = "completed" | "partial" | "no_evidence" | "failed";
export type RunStatus = "completed" | "partial" | "failed";
export type VerificationStatus = "verified" | "retry_required" | "replan_required" | "cannot_complete";
export type EvidenceQuality = "high" | "medium" | "low";
export type FindingCheckStatus = "verified" | "insufficient";

export interface EdrakContracts {
  BusinessRequest: BusinessRequest;
  ResearchPlan: ResearchPlan;
  ResearchTask: ResearchTask;
  WorkerResult: WorkerResult;
  OrchestrationResult: OrchestrationResult;
  VerificationResult: VerificationResult;
}
/**
 * High-level business request entering the EDRAK orchestration workflow.
 */
export interface BusinessRequest {
  business_context: BusinessContext;
  company_profile: CompanyProfile;
  /**
   * When the request was created in UTC.
   */
  created_at: string;
  /**
   * Optional request-specific information not part of the core contract.
   */
  extras: {
    [k: string]: unknown;
  };
  /**
   * The main business question or objective EDRAK must address.
   */
  goal: string;
  /**
   * Unique request identifier.
   */
  request_id: string;
}
/**
 * Context that frames how the business goal should be analyzed.
 */
export interface BusinessContext {
  /**
   * Companies to compare against, the same idea in every use case.
   */
  competitors: CompetitorRef[];
  /**
   * Explicit analysis constraints or requirements.
   */
  constraints: string[];
  /**
   * Dimensions the analysis should emphasize.
   */
  focus_areas: string[];
  /**
   * Places the analysis is about: countries, regions or cities.
   */
  markets: string[];
  /**
   * The product or service being assessed or launched.
   */
  offering: string | null;
  /**
   * Who the offering is for.
   */
  target_customers: string | null;
  /**
   * Names of the competitors to compare against. Same names as `competitors`; kept for workers that read a plain list.
   */
  targets: string[];
  /**
   * Optional research recency window in days.
   */
  time_window_days: number | null;
  trigger: TriggerType;
  use_case: UseCase;
}
/**
 * A company to compare against, with what is known about where it publishes.
 */
export interface CompetitorRef {
  /**
   * 'user' when the person named it, 'agent' when Edrak found it.
   */
  discovered_by: string;
  /**
   * Watchlist entity id in the app.
   */
  entity_id: string | null;
  /**
   * The competitor's name.
   */
  name: string;
  /**
   * Official social accounts, read from the competitor's own website.
   */
  social_accounts: SocialAccount[];
  /**
   * Official website, when known.
   */
  website: string | null;
}
/**
 * One official account of a company, e.g. its X or Instagram profile.
 */
export interface SocialAccount {
  platform: SocialPlatform;
  /**
   * Full URL of the account or page.
   */
  url: string;
}
/**
 * Baseline describing the company under analysis (the user's own company).
 */
export interface CompanyProfile {
  /**
   * Alternative names or commonly used company names.
   */
  aliases: string[];
  /**
   * Canonical name of the company under analysis.
   */
  name: string;
  /**
   * Optional high-level company baseline notes.
   */
  notes: string | null;
  /**
   * Known products or offerings relevant to the analysis.
   */
  products: string[];
  /**
   * The company's own social accounts. The Customer worker reads what people say there.
   */
  social_accounts: SocialAccount[];
  /**
   * The company's official website.
   */
  website: string | null;
  /**
   * Workspace whose uploaded documents the Internal worker may search. When unset the whole knowledge base is searched.
   */
  workspace_id: string | null;
}
/**
 * An ordered set of research assignments produced by the planner.
 */
export interface ResearchPlan {
  /**
   * When the plan was created (UTC).
   */
  created_at: string;
  /**
   * Unique plan identifier.
   */
  plan_id: string;
  /**
   * Why these tasks, in plain language.
   */
  rationale: string | null;
  /**
   * request_id of the originating BusinessRequest.
   */
  request_id: string;
  /**
   * Tasks to dispatch.
   */
  tasks: ResearchTask[];
}
/**
 * One bounded research assignment handed to a single worker.
 *
 * The orchestrator produces this; a worker consumes it. It carries no
 * questions list, dependency graph, or required-evidence list on purpose:
 * ``goal`` and ``focus`` are the whole instruction surface, and the shared
 * company and business context travel with the task so the worker never needs
 * the original request.
 */
export interface ResearchTask {
  /**
   * Starts at 1 and increments on targeted retries.
   */
  attempt: number;
  business_context: BusinessContext;
  company_profile: CompanyProfile;
  /**
   * What this task must concentrate on.
   */
  focus: string;
  /**
   * What this task must achieve.
   */
  goal: string;
  /**
   * request_id of the originating BusinessRequest.
   */
  parent_request_id: string;
  /**
   * Typed figures this worker must look for. Filled by code from the use-case template.
   */
  requirements: MetricRequirement[];
  /**
   * Unique task identifier.
   */
  task_id: string;
  worker: WorkerType;
}
/**
 * What the planner asks a worker to find.
 */
export interface MetricRequirement {
  check_rule: string | null;
  definition: string | null;
  entity: string;
  metric_id: string;
  preferred_source_types: string[];
  query_hints: string[];
  required: boolean;
}
/**
 * The single contract every worker returns for a ResearchTask.
 *
 * Referential integrity is enforced here: every evidence reference and every
 * conflict must resolve inside this result. Workers are therefore forced to
 * carry provenance, and a dangling id fails at the boundary instead of
 * surfacing later inside verification or synthesis.
 */
export interface WorkerResult {
  /**
   * Attempt number that produced this result.
   */
  attempt: number;
  /**
   * Completion time (UTC).
   */
  completed_at: string | null;
  /**
   * Optional overall confidence.
   */
  confidence: number | null;
  /**
   * Contradictions found.
   */
  conflicts: Conflict[];
  /**
   * Failure detail when status is failed.
   */
  error: string | null;
  /**
   * Evidence collected.
   */
  evidence: Evidence[];
  /**
   * Claims produced.
   */
  findings: Finding[];
  /**
   * Known information gaps.
   */
  gaps: string[];
  /**
   * Worker-specific extras.
   */
  metadata: {
    [k: string]: unknown;
  };
  /**
   * Start time (UTC).
   */
  started_at: string | null;
  status: WorkerStatus;
  /**
   * task_id of the ResearchTask this answers.
   */
  task_id: string;
  worker: WorkerType;
}
/**
 * A contradiction discovered between findings or between a finding and its evidence.
 */
export interface Conflict {
  /**
   * Evidence that refutes the claim.
   */
  contradicting_evidence_ids: string[];
  /**
   * What the contradiction is.
   */
  description: string;
  /**
   * finding_id of the claim in question.
   */
  finding_id: string;
}
/**
 * Traceable support for one or more findings.
 *
 * Workers are the only producers. Verification and synthesis consume it;
 * the orchestrator must treat it as opaque.
 */
export interface Evidence {
  /**
   * Unique evidence identifier.
   */
  evidence_id: string;
  /**
   * Supporting passage from the source.
   */
  excerpt: string | null;
  /**
   * The specific fact drawn from the source.
   */
  extracted_fact: string;
  /**
   * True when the content is synthetic or adapted, not authentic.
   */
  is_synthetic: boolean;
  /**
   * Source-specific extras such as pricing tier or publish date.
   */
  metadata: {
    [k: string]: unknown;
  };
  /**
   * Who published the source.
   */
  publisher: string | null;
  /**
   * When the source was retrieved (UTC).
   */
  retrieved_at: string;
  /**
   * Title of the source.
   */
  source_title: string | null;
  source_type: SourceType;
  /**
   * URL when the source is public.
   */
  source_url: string | null;
}
/**
 * One evidence-backed claim.
 *
 * Support and contradiction are derived from ``evidence_refs`` rather than
 * stored, so they cannot drift away from the actual links.
 */
export interface Finding {
  category: FindingCategory;
  /**
   * Optional self-reported confidence.
   */
  confidence: number | null;
  /**
   * Evidence backing or refuting this claim.
   */
  evidence_refs: EvidenceRef[];
  /**
   * Unique finding identifier.
   */
  finding_id: string;
  /**
   * Known caveats attached to this claim.
   */
  limitations: string[];
  /**
   * The claim being made.
   */
  statement: string;
}
/**
 * A finding's link to one piece of evidence, carrying what the link means.
 *
 * A bare id list cannot distinguish "this source backs the claim" from "this
 * source refutes it", so the relation travels with the reference.
 */
export interface EvidenceRef {
  /**
   * evidence_id of the referenced Evidence.
   */
  evidence_id: string;
  relation: EvidenceRelation;
}
/**
 * Terminal output of one orchestrator run.
 *
 * Carries worker results through to verification unchanged. The orchestrator
 * itself reads only the control fields of each WorkerResult.
 */
export interface OrchestrationResult {
  /**
   * When the run finished (UTC).
   */
  completed_at: string;
  /**
   * Validated CrossSignalOutput serialized as JSON, if verification succeeded.
   */
  cross_signal: {
    [k: string]: unknown;
  } | null;
  /**
   * DecisionAnalysisResult serialized as JSON, if that stage ran.
   */
  decision_analysis: {
    [k: string]: unknown;
  } | null;
  /**
   * Run-level failure detail.
   */
  error: string | null;
  /**
   * Plan that was executed.
   */
  plan: ResearchPlan | null;
  /**
   * request_id of the originating BusinessRequest.
   */
  request_id: string;
  /**
   * Worker results collected.
   */
  results: WorkerResult[];
  status: RunStatus;
}
/**
 * Full verification stage output.
 *
 * ``decision`` is the orchestrator control contract. ``findings`` are the
 * verified/insufficient claims passed to synthesis.
 */
export interface VerificationResult {
  completed_at: string;
  control_summary: ControlSummary;
  decision: VerificationDecision;
  findings: FindingVerdict[];
  metadata: {
    [k: string]: unknown;
  };
  /**
   * request_id of the originating BusinessRequest.
   */
  research_run_id: string;
}
/**
 * Compact retry/replan brief. Does not carry raw documents.
 */
export interface ControlSummary {
  conflicts: string[];
  failures: string[];
  missing_information: string[];
  next_research_targets: string[];
}
/**
 * Compact control-plane result used by the orchestrator for retry/replan.
 */
export interface VerificationDecision {
  status: VerificationStatus;
  /**
   * One-line explanation of the decision.
   */
  summary: string;
  /**
   * Concrete actions to take if action is required.
   */
  targeted_actions: TargetedAction[];
}
export interface TargetedAction {
  /**
   * What action to take in plain language.
   */
  reason: string;
  worker: WorkerType;
}
/**
 * Per-finding verification result consumed by synthesis.
 */
export interface FindingVerdict {
  /**
   * Verifier confidence in this verdict.
   */
  confidence: number;
  /**
   * Conflicts found in the backing evidence or across findings.
   */
  contradictions: string[];
  /**
   * Evidence ids inspected for this finding.
   */
  evidence_ids: string[];
  evidence_quality: EvidenceQuality;
  /**
   * finding_id from the source WorkerResult.
   */
  finding_id: string;
  /**
   * What is still needed to support the claim.
   */
  missing_information: string[];
  /**
   * The claim being verified.
   */
  statement: string;
  verification_status: FindingCheckStatus;
  worker: WorkerType;
}
