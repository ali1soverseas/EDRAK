/**
 * Turns a content pack into the contract objects a finished run would have produced:
 * a WorkerResult per task, an OrchestrationResult, a VerificationResult. The brief is
 * then built from those exactly as it would be from the backend's.
 */
import type {
  Evidence,
  Finding,
  FindingVerdict,
  OrchestrationResult,
  ResearchPlan,
  VerificationResult,
  WorkerResult,
} from "../../types/contracts";
import type { Brief, BriefOption } from "../../types/app";
import { buildBrief } from "../../lib/brief";
import type { BusinessRequest } from "../../types/contracts";
import type { Pack, PackWorker } from "./packs";
import { FAILURE_TEXT, WORKER_SECONDS, failFraction, timeline, type RunRecord } from "./simulate";

function workerResult(
  task: ResearchPlan["tasks"][number],
  content: PackWorker,
  run: RunRecord,
): WorkerResult {
  const approved = Date.parse(run.approved_at);
  const seconds = WORKER_SECONDS[task.worker];
  const at = (fraction: number) => new Date(approved + (2 + fraction * seconds) * 1000).toISOString();
  const fraction = failFraction(run.scenario, task.worker);

  if (fraction !== null) {
    return {
      task_id: task.task_id,
      worker: task.worker,
      status: "failed",
      attempt: 1,
      findings: [],
      evidence: [],
      gaps: [],
      conflicts: [],
      confidence: null,
      started_at: at(0),
      completed_at: at(fraction),
      error: task.worker === "market_intelligence" ? FAILURE_TEXT.market : FAILURE_TEXT.all,
      metadata: {},
    };
  }

  const evidence: Evidence[] = content.evidence.map((item, index) => ({
    evidence_id: item.id,
    source_type: item.type,
    source_title: item.title,
    source_url: item.url,
    publisher: item.publisher,
    extracted_fact: item.fact,
    excerpt: item.excerpt,
    retrieved_at: at(((index + 1) / (content.evidence.length + 1)) * 0.9),
    is_synthetic: false,
    metadata: item.file ? { file: item.file } : {},
  }));

  const findings: Finding[] = content.findings.map((finding) => ({
    finding_id: finding.id,
    statement: finding.statement,
    category: finding.category,
    evidence_refs: finding.refs.map((id) => ({ evidence_id: id, relation: "supports" as const })),
    confidence: finding.confidence,
    limitations: finding.missing ?? [],
  }));

  const confidences = content.findings.map((finding) => finding.confidence);
  return {
    task_id: task.task_id,
    worker: task.worker,
    status: content.gaps.length > 0 ? "partial" : "completed",
    attempt: 1,
    findings,
    evidence,
    gaps: content.gaps,
    conflicts: [],
    confidence: confidences.length ? confidences.reduce((sum, value) => sum + value, 0) / confidences.length : null,
    started_at: at(0),
    completed_at: at(1),
    error: null,
    metadata: {},
  };
}

export function orchestrationFor(plan: ResearchPlan, pack: Pack, run: RunRecord): OrchestrationResult {
  const results = plan.tasks.map((task) => workerResult(task, pack.workers[task.worker], run));
  const workers = plan.tasks.map((task) => task.worker);
  const line = timeline(workers, run.scenario);
  return {
    request_id: plan.request_id,
    status: run.scenario === "normal" ? "completed" : "partial",
    plan,
    results,
    error: null,
    completed_at: new Date(Date.parse(run.approved_at) + (line.finish / run.speed) * 1000).toISOString(),
  };
}

export function verificationFor(plan: ResearchPlan, orchestration: OrchestrationResult, pack: Pack): VerificationResult {
  const verdicts: FindingVerdict[] = [];
  for (const result of orchestration.results) {
    if (result.status === "failed") continue;
    for (const finding of pack.workers[result.worker].findings) {
      verdicts.push({
        finding_id: finding.id,
        worker: result.worker,
        statement: finding.statement,
        verification_status: finding.check,
        evidence_quality: finding.quality,
        evidence_ids: finding.refs,
        contradictions: [],
        missing_information: finding.missing ?? [],
        confidence: finding.confidence,
      });
    }
  }
  const failures = orchestration.results
    .filter((result) => result.status === "failed")
    .map((result) => `${result.worker} failed: ${result.error ?? "no detail"}`);
  const reported = orchestration.results.length - failures.length;

  return {
    research_run_id: plan.request_id,
    decision: {
      status: "verified",
      targeted_actions: [],
      summary:
        failures.length > 0
          ? `Verified ${reported} of ${orchestration.results.length} workers. Evidence is partial.`
          : "Evidence checked across all workers. Information gaps are listed.",
    },
    findings: verdicts,
    control_summary: {
      missing_information: pack.missing_information,
      conflicts: [],
      failures,
      next_research_targets: pack.next_steps,
    },
    metadata: {},
    completed_at: orchestration.completed_at,
  };
}

export interface BriefContext {
  briefId: string;
  briefNo: number;
  analysisId: string;
  title: string;
  requestedBy: string;
  request: BusinessRequest;
  plan: ResearchPlan;
  pack: Pack;
  run: RunRecord;
}

export function briefFor(context: BriefContext): Brief {
  const { pack, plan, run, request } = context;
  const orchestration = orchestrationFor(plan, pack, run);
  const verification = verificationFor(plan, orchestration, pack);
  const partial = orchestration.status !== "completed";
  const options: BriefOption[] = pack.options.map((option) => ({ ...option, option_id: `${context.briefId}:${option.key}` }));

  return buildBrief({
    brief_id: context.briefId,
    brief_no: context.briefNo,
    analysis_id: context.analysisId,
    analysis_title: context.title,
    title: request.goal.length <= 90 ? request.goal : pack.headline,
    use_case: request.business_context.use_case,
    created_at: orchestration.completed_at,
    requested_by: context.requestedBy,
    request,
    orchestration,
    verification,
    summary: partial ? pack.summary_partial : pack.summary,
    options,
    synthesis: partial ? pack.synthesis_partial : pack.synthesis,
  });
}
