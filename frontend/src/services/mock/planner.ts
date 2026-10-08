/**
 * Mock planner. Stands in for the Supervisor's planning step until the backend exposes
 * one. It returns a contract ResearchPlan with one task per allowed worker, which is
 * the shape LlmPlanner produces.
 */
import type { BusinessRequest, ResearchPlan, ResearchTask, UseCase, WorkerType } from "../../types/contracts";
import { WORKER_META } from "../../lib/workers";
import { WORKER_ORDER } from "../../lib/brief";
import { newId } from "../../lib/ids";
import type { AnalysisForm } from "../../types/app";

interface Vars {
  company: string;
  offering: string;
  market: string;
  competitors: string[];
  window: string;
}

const list = (items: string[]) =>
  items.length <= 1 ? (items[0] ?? "") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;

type Text = { goal: string; focus: string };

const TEXT: Record<UseCase, Record<WorkerType, (v: Vars) => Text>> = {
  product_launch: {
    internal_intelligence: () => ({
      goal: "Can we realistically support this?",
      focus: "Search the uploaded product documents and support tickets. Identify strengths, constraints and gaps.",
    }),
    competitor_intelligence: (v) => ({
      goal: "What alternatives already exist?",
      focus: `Find products that already do this${v.market ? ` in ${v.market}` : ""}. Compare features, pricing and positioning.${
        v.competitors.length ? ` Start from ${list(v.competitors)}.` : ""
      }`,
    }),
    market_intelligence: (v) => ({
      goal: "What is happening in this category?",
      focus: `Summarize trends, adoption signals and any regulation that affects this offering.${v.window}`,
    }),
    customer_trends: () => ({
      goal: "Is there evidence of customer need?",
      focus: "Look for repeated complaints, requests and unmet needs in reviews, forums and our own tickets.",
    }),
  },
  competitive_intelligence: {
    internal_intelligence: (v) => ({
      goal: "Where do we stand against them?",
      focus: `Search our product documents and support tickets for strengths, constraints and gaps compared with ${
        v.competitors.length ? list(v.competitors) : "the main competitors"
      }.`,
    }),
    competitor_intelligence: (v) => ({
      goal: "What are the competitors doing?",
      focus: `Track features, pricing and positioning for ${
        v.competitors.length ? list(v.competitors) : `the main competitors${v.market ? ` in ${v.market}` : ""}`
      }. Report what changed.${v.window}`,
    }),
    market_intelligence: (v) => ({
      goal: "What is shaping the category?",
      focus: `Summarize category news, funding and regulation that affect ${v.company} and its competitors.${v.window}`,
    }),
    customer_trends: (v) => ({
      goal: "What do customers say about each of them?",
      focus: `Look for repeated praise, complaints and switching signals about ${
        v.competitors.length ? list(v.competitors) : "the competitors"
      } in reviews, forums and our own tickets.`,
    }),
  },
  market_entry_expansion: {
    internal_intelligence: (v) => ({
      goal: `Can we serve ${v.market || "this market"}?`,
      focus: `Check our product, hosting and support capacity for ${v.market || "the target market"}. Identify strengths, constraints and gaps.`,
    }),
    competitor_intelligence: (v) => ({
      goal: `Who already serves ${v.market || "this market"}?`,
      focus: `Find the players in ${v.market || "the target market"}. Compare features, pricing and positioning.${
        v.competitors.length ? ` Start from ${list(v.competitors)}.` : ""
      }`,
    }),
    market_intelligence: (v) => ({
      goal: `What does entering ${v.market || "this market"} involve?`,
      focus: `Summarize market size, growth, regulation and licensing requirements for ${v.market || "the target market"}.${v.window}`,
    }),
    customer_trends: (v) => ({
      goal: `Is there demand in ${v.market || "this market"}?`,
      focus: `Look for unmet needs and buying signals in reviews, forums and social posts from ${v.market || "the target market"}.`,
    }),
  },
};

const RATIONALE: Record<UseCase, string> = {
  competitive_intelligence:
    "One task per worker, run in parallel. Verification then checks the evidence before synthesis.",
  market_entry_expansion:
    "One task per worker, run in parallel. Verification then checks the evidence before synthesis.",
  product_launch:
    "One task per worker, run in parallel. Verification then checks the evidence before synthesis.",
};

export function planFor(request: BusinessRequest, form: AnalysisForm): ResearchPlan {
  const allowed = new Set(request.extras.allowed_sources as string[] | undefined);
  const days = request.business_context.time_window_days;
  const vars: Vars = {
    company: request.company_profile.name,
    offering: form.offering.trim(),
    market: form.market.trim(),
    competitors: form.competitors,
    window: days ? ` Look back ${days === 365 ? "one year" : `${days} days`}.` : "",
  };

  const tasks: ResearchTask[] = WORKER_ORDER.filter((worker) => allowed.has(WORKER_META[worker].source)).map(
    (worker) => ({
      task_id: newId(),
      parent_request_id: request.request_id,
      worker,
      ...TEXT[request.business_context.use_case][worker](vars),
      company_profile: request.company_profile,
      business_context: request.business_context,
      attempt: 1,
    }),
  );

  return {
    plan_id: newId(),
    request_id: request.request_id,
    tasks,
    created_at: new Date().toISOString(),
    rationale: RATIONALE[request.business_context.use_case],
  };
}
