/**
 * Builds the Brief view model from contract objects.
 *
 * Inputs are the contract results (OrchestrationResult, VerificationResult) plus the
 * parts the contracts do not cover (summary text, options, synthesis). Everything
 * the screens derive, such as reliability dots and gap lists, is derived here so
 * the rules live in one place and are tested.
 */
import type {
  BusinessRequest,
  Evidence,
  EvidenceQuality,
  OrchestrationResult,
  SourceType,
  VerificationResult,
  WorkerResult,
  WorkerType,
} from "../types/contracts";
import {
  evidenceKey,
  type Brief,
  type BriefEvidence,
  type BriefFinding,
  type BriefGap,
  type BriefLens,
  type BriefOption,
  type BriefSummaryText,
  type BriefSynthesis,
  type Reliability,
  type SourceNoteKind,
  type UseCase,
} from "../types/app";

/** The order workers appear in, everywhere in the UI. */
export const WORKER_ORDER: readonly WorkerType[] = [
  "internal_intelligence",
  "competitor_intelligence",
  "market_intelligence",
  "customer_trends",
];

/** Reliability marks come from the verdict's evidence_quality: high is three, medium two, low one. */
export function reliabilityFromQuality(quality: EvidenceQuality): Reliability {
  switch (quality) {
    case "high":
      return 3;
    case "medium":
      return 2;
    case "low":
      return 1;
  }
}

const INTERNAL_SOURCES: ReadonlySet<SourceType> = new Set(["internal_document", "synthetic_internal"]);

export function originOf(evidence: Evidence): "internal" | "external" {
  return INTERNAL_SOURCES.has(evidence.source_type) ? "internal" : "external";
}

/**
 * The small label in the Note column. The contracts carry no per-source flag, so it is
 * derived from the source type, and from the reliability for pages that say little.
 */
export function sourceNote(evidence: Evidence, reliability: Reliability): SourceNoteKind {
  switch (evidence.source_type) {
    case "internal_document":
    case "synthetic_internal":
      return "your_data";
    case "official_documentation":
    case "pricing_page":
    case "release_notes":
    case "regulatory":
      return "primary";
    case "announcement":
      return "vendor_claim";
    case "review_site":
      return "opinion";
    case "market_report":
    case "economic":
      return "open_data";
    case "news_article":
      return "news";
    case "web_page":
    case "search_result":
    case "other":
      return reliability >= 2 ? "vendor_claim" : "thin";
  }
}

export interface BuildBriefInput {
  brief_id: string;
  brief_no: number;
  analysis_id: string;
  analysis_title: string;
  title: string;
  use_case: UseCase;
  created_at: string;
  requested_by: string;
  request: BusinessRequest;
  orchestration: OrchestrationResult;
  verification: VerificationResult;
  summary: BriefSummaryText;
  options: BriefOption[];
  synthesis: BriefSynthesis | null;
}

export function buildBrief(input: BuildBriefInput): Brief {
  const { orchestration, verification } = input;
  const resultByWorker = new Map<WorkerType, WorkerResult>();
  for (const result of orchestration.results) resultByWorker.set(result.worker, result);

  // The workers that were part of this run: the plan's workers, or the ones that reported.
  const plannedWorkers = new Set<WorkerType>(
    orchestration.plan ? orchestration.plan.tasks.map((task) => task.worker) : resultByWorker.keys(),
  );
  const workers = WORKER_ORDER.filter((worker) => plannedWorkers.has(worker));

  const verdictKey = (worker: WorkerType, findingId: string) => `${worker}:${findingId}`;
  const verdicts = new Map(verification.findings.map((verdict) => [verdictKey(verdict.worker, verdict.finding_id), verdict]));

  // Best quality any verdict gives to a piece of evidence.
  const evidenceReliability = new Map<string, Reliability>();
  for (const verdict of verification.findings) {
    const reliability = reliabilityFromQuality(verdict.evidence_quality);
    for (const id of verdict.evidence_ids) {
      const key = evidenceKey(verdict.worker, id);
      evidenceReliability.set(key, Math.max(reliability, evidenceReliability.get(key) ?? 0) as Reliability);
    }
  }

  // Number the evidence once, in worker order, so an E-number means the same thing everywhere.
  const evidence: BriefEvidence[] = [];
  const numbers = new Map<string, number>();
  for (const worker of workers) {
    const result = resultByWorker.get(worker);
    if (!result) continue;
    for (const item of result.evidence) {
      const key = evidenceKey(worker, item.evidence_id);
      const displayNo = evidence.length + 1;
      numbers.set(key, displayNo);
      const reliability = evidenceReliability.get(key) ?? 1;
      evidence.push({
        display_no: displayNo,
        evidence: item,
        worker,
        task_id: result.task_id,
        attempt: result.attempt,
        reliability,
        note: sourceNote(item, reliability),
        origin: originOf(item),
      });
    }
  }

  const lenses: BriefLens[] = workers.map((worker) => {
    const result = resultByWorker.get(worker) ?? null;
    const failed = result === null || result.status === "failed";
    const findings: BriefFinding[] = (result?.findings ?? []).map((finding) => {
      const verdict = verdicts.get(verdictKey(worker, finding.finding_id)) ?? null;
      return {
        finding,
        worker,
        verdict,
        status: verdict ? verdict.verification_status : "unchecked",
        reliability: verdict ? reliabilityFromQuality(verdict.evidence_quality) : 1,
        refs: finding.evidence_refs.flatMap((ref) => {
          const key = evidenceKey(worker, ref.evidence_id);
          const displayNo = numbers.get(key);
          return displayNo === undefined ? [] : [{ display_no: displayNo, relation: ref.relation, key }];
        }),
      };
    });
    return { worker, result, failed, findings, source_count: result?.evidence.length ?? 0 };
  });

  const gaps: BriefGap[] = [];
  const seen = new Set<string>();
  const addGap = (text: string, reportedBy: BriefGap["reported_by"]) => {
    const normalized = text.trim().toLowerCase();
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    gaps.push({ text: text.trim(), reported_by: reportedBy });
  };
  for (const worker of workers) {
    for (const gap of resultByWorker.get(worker)?.gaps ?? []) addGap(gap, worker);
  }
  for (const text of verification.control_summary.missing_information) addGap(text, "verification");

  return {
    brief_id: input.brief_id,
    brief_no: input.brief_no,
    analysis_id: input.analysis_id,
    analysis_title: input.analysis_title,
    title: input.title,
    use_case: input.use_case,
    created_at: input.created_at,
    requested_by: input.requested_by,
    partial: orchestration.status !== "completed",
    request: input.request,
    orchestration,
    verification,
    summary: input.summary,
    lenses,
    evidence,
    options: input.options,
    gaps,
    next_steps: [...verification.control_summary.next_research_targets],
    synthesis: input.synthesis,
  };
}

/** Looks an E-number up by the worker and id that a finding or summary segment carries. */
export function evidenceNumber(brief: Brief, worker: WorkerType, evidenceId: string): number | null {
  const found = brief.evidence.find((item) => item.worker === worker && item.evidence.evidence_id === evidenceId);
  return found ? found.display_no : null;
}

/** The findings that cite a piece of evidence, with how they relate to it. */
export function findingsUsing(brief: Brief, item: BriefEvidence) {
  const key = evidenceKey(item.worker, item.evidence.evidence_id);
  const used: Array<{ statement: string; relation: BriefFinding["refs"][number]["relation"] }> = [];
  for (const lens of brief.lenses) {
    for (const finding of lens.findings) {
      const ref = finding.refs.find((candidate) => candidate.key === key);
      if (ref) used.push({ statement: finding.finding.statement, relation: ref.relation });
    }
  }
  return used;
}

/** Host name for display, or null when the source has no usable URL. */
export function hostOf(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}
