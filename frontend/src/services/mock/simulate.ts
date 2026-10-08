/**
 * Mock run simulation.
 *
 * A run is a pure function of time: given when it was approved, how fast the mock
 * clock runs and what scenario it plays, `simulateRun` says what every worker is
 * doing right now. Nothing needs a timer, and a page reload picks up where the run is.
 *
 * Demo behaviour, not product behaviour: market-entry runs play the `market_fails`
 * scenario so the failed-worker and partial-evidence states can be seen. Real runs
 * will come from the backend's run and task tables.
 */
import type { ResearchPlan, WorkerType } from "../../types/contracts";
import type {
  RunEvent,
  RunStage,
  RunState,
  RunTaskView,
  RunView,
  TaskState,
} from "../../types/app";
import type { Pack } from "./packs";
import { WORKER_ORDER } from "../../lib/brief";

export type Scenario = "normal" | "market_fails" | "run_fails";

export interface RunRecord {
  approved_at: string;
  approved_by: string;
  /** Mock clock speed. 1 is real time. */
  speed: number;
  scenario: Scenario;
  cancelled_at: string | null;
}

/** Seconds of mock time each worker needs. */
export const WORKER_SECONDS: Record<WorkerType, number> = {
  internal_intelligence: 24,
  competitor_intelligence: 52,
  market_intelligence: 60,
  customer_trends: 44,
};

const DISPATCH_SECONDS = 2;
const VERIFY_SECONDS = 8;
const SYNTHESIS_SECONDS = 6;

export const FAILURE_TEXT = {
  market: "The news source did not respond after 3 attempts.",
  all: "The workers could not reach their sources.",
};

/** Fraction of its time a worker reached before it failed, or null if it does not fail. */
export function failFraction(scenario: Scenario, worker: WorkerType): number | null {
  if (scenario === "run_fails") return 0.3;
  if (scenario === "market_fails" && worker === "market_intelligence") return 0.45;
  return null;
}

export interface Timeline {
  workerEnd: Map<WorkerType, number>;
  /** When every worker is finished or failed. */
  workersDone: number;
  verifyEnd: number;
  finish: number;
  allFailed: boolean;
}

export function timeline(workers: WorkerType[], scenario: Scenario): Timeline {
  const workerEnd = new Map<WorkerType, number>();
  let workersDone = 0;
  let allFailed = workers.length > 0;
  for (const worker of workers) {
    const fraction = failFraction(scenario, worker);
    const end = DISPATCH_SECONDS + WORKER_SECONDS[worker] * (fraction ?? 1);
    workerEnd.set(worker, end);
    workersDone = Math.max(workersDone, end);
    if (fraction === null) allFailed = false;
  }
  return {
    workerEnd,
    workersDone,
    verifyEnd: workersDone + VERIFY_SECONDS,
    finish: workersDone + VERIFY_SECONDS + SYNTHESIS_SECONDS,
    allFailed,
  };
}

export interface SimulateInput {
  analysisId: string;
  title: string;
  useCase: RunView["use_case"];
  plan: ResearchPlan;
  pack: Pack;
  run: RunRecord;
  /** Epoch milliseconds. */
  now: number;
  briefId: string;
}

const NUMBER_WORDS = ["No", "One", "Two", "Three", "Four"];
const numberWord = (n: number) => NUMBER_WORDS[n] ?? String(n);

export function simulateRun(input: SimulateInput): RunView {
  const { plan, pack, run } = input;
  const approved = Date.parse(run.approved_at);
  const speed = run.speed;
  const cancelledAt = run.cancelled_at ? Date.parse(run.cancelled_at) : null;
  const clock = cancelledAt ?? input.now;
  const simT = Math.max(0, ((clock - approved) / 1000) * speed);
  const at = (simSeconds: number) => new Date(approved + (simSeconds / speed) * 1000).toISOString();

  const workers = WORKER_ORDER.filter((worker) => plan.tasks.some((task) => task.worker === worker));
  const line = timeline(workers, run.scenario);

  const tasks: RunTaskView[] = workers.map((worker) => {
    const task = plan.tasks.find((candidate) => candidate.worker === worker)!;
    const content = pack.workers[worker];
    const seconds = WORKER_SECONDS[worker];
    const fraction = failFraction(run.scenario, worker);
    const end = line.workerEnd.get(worker)!;
    const elapsed = Math.max(0, simT - DISPATCH_SECONDS);

    let state: TaskState;
    let progress: number;
    if (fraction !== null && simT >= end) {
      state = "failed";
      progress = Math.round(fraction * 100);
    } else if (fraction === null && simT >= end) {
      state = "done";
      progress = 100;
    } else {
      state = simT >= DISPATCH_SECONDS ? "running" : "queued";
      progress = Math.min(99, Math.round((elapsed / seconds) * 100));
    }

    const sources = Math.round((content.evidence.length * progress) / 100);
    let activity: string;
    if (state === "done") activity = content.done;
    else if (state === "failed") activity = `Stopped: ${worker === "market_intelligence" ? FAILURE_TEXT.market.toLowerCase().replace(/\.$/, "") : FAILURE_TEXT.all.toLowerCase().replace(/\.$/, "")}`;
    else if (state === "queued") activity = "Waiting to start";
    else activity = content.activity[Math.min(content.activity.length - 1, Math.floor((progress / 100) * content.activity.length))];

    const log = content.log
      .filter((entry) => progress >= Math.round(entry.at * 100))
      .map((entry) => ({ at: at(DISPATCH_SECONDS + entry.at * seconds), text: entry.text }));

    return {
      task_id: task.task_id,
      worker,
      state,
      progress,
      sources,
      activity,
      log,
      error: state === "failed" ? (worker === "market_intelligence" ? FAILURE_TEXT.market : FAILURE_TEXT.all) : null,
    };
  });

  const doneCount = tasks.filter((task) => task.state === "done").length;
  const failedCount = tasks.filter((task) => task.state === "failed").length;
  const workersFinished = tasks.length > 0 && doneCount + failedCount === tasks.length;

  /* ---- events (control level only) ---- */
  const events: RunEvent[] = [];
  const push = (event: Omit<RunEvent, "event_id" | "callout"> & { callout?: RunEvent["callout"] }) =>
    events.push({ event_id: `${input.analysisId}:${events.length}`, callout: null, ...event });

  push({
    at: at(0),
    source: "supervisor",
    kind: "info",
    message: `Plan approved by ${run.approved_by}. ${numberWord(tasks.length)} ${tasks.length === 1 ? "worker" : "workers"} dispatched in parallel.`,
  });

  const timed: Array<{ t: number; event: Parameters<typeof push>[0] }> = [];
  for (const task of tasks) {
    const content = pack.workers[task.worker];
    const end = line.workerEnd.get(task.worker)!;
    if (task.state === "done") {
      const gaps = content.gaps.length;
      timed.push({
        t: end,
        event: {
          at: at(end),
          source: task.worker,
          kind: "info",
          message: `Complete. ${content.evidence.length} sources, ${gaps === 0 ? "no gaps reported" : gaps === 1 ? "1 gap reported" : `${gaps} gaps reported`}.`,
        },
      });
    }
    if (task.state === "failed") {
      timed.push({
        t: end,
        event: {
          at: at(end),
          source: task.worker,
          kind: "failure",
          message: `Failed: ${task.error ?? "worker stopped"}`,
          callout:
            run.scenario === "market_fails"
              ? {
                  text: "Continue without the Market worker. Verification and the brief will mark the evidence as partial.",
                  highlight: null,
                }
              : null,
        },
      });
    }
  }
  const competitor = tasks.find((task) => task.worker === "competitor_intelligence");
  if (competitor && run.scenario !== "run_fails" && pack.workers.competitor_intelligence.gaps.length > 0) {
    const gapAt = DISPATCH_SECONDS + WORKER_SECONDS.competitor_intelligence * 0.75;
    if (simT >= gapAt) {
      timed.push({
        t: gapAt,
        event: {
          at: at(gapAt),
          source: "competitor_intelligence",
          kind: "replan",
          message: pack.replan.gap,
          callout: {
            text: `Added one targeted task: ${pack.replan.task}. Reason: ${pack.replan.reason}.`,
            highlight: pack.replan.task,
          },
        },
      });
    }
  }
  if (run.scenario === "run_fails" && workersFinished) {
    timed.push({
      t: line.workersDone + 0.1,
      event: { at: at(line.workersDone + 0.1), source: "supervisor", kind: "failure", message: "No worker reported. The run stopped before any brief." },
    });
  }

  const runFailed = workersFinished && line.allFailed;
  const verifyStart = line.workersDone;
  const synthStart = verifyStart + VERIFY_SECONDS;
  if (!runFailed && workersFinished) {
    timed.push({
      t: verifyStart,
      event: { at: at(verifyStart), source: "supervisor", kind: "info", message: `Verification started on ${doneCount} of ${tasks.length} workers.` },
    });
    if (simT >= synthStart) {
      timed.push({ t: synthStart, event: { at: at(synthStart), source: "supervisor", kind: "info", message: "Verification complete. Synthesis started." } });
    }
    if (simT >= line.finish) {
      timed.push({ t: line.finish, event: { at: at(line.finish), source: "supervisor", kind: "info", message: "The brief is ready." } });
    }
  }
  timed.sort((a, b) => a.t - b.t).forEach((entry) => push(entry.event));

  /* ---- stage, state ---- */
  let stage: RunStage = "workers";
  let verification: RunView["verification"] = "waiting";
  let state: RunState = "running";
  let finishedAt: string | null = null;

  if (runFailed) {
    state = "failed";
    finishedAt = at(line.workersDone);
  } else if (workersFinished) {
    if (simT < synthStart) {
      stage = "verify";
      verification = "running";
    } else if (simT < line.finish) {
      stage = "synthesize";
      verification = "done";
    } else {
      stage = "brief";
      verification = "done";
      state = run.scenario === "normal" ? "completed" : "partial";
      finishedAt = at(line.finish);
    }
  }
  if (cancelledAt !== null && state === "running") {
    state = "cancelled";
    finishedAt = run.cancelled_at;
  }

  return {
    analysis_id: input.analysisId,
    title: input.title,
    use_case: input.useCase,
    state,
    stage,
    approved_at: run.approved_at,
    approved_by: run.approved_by,
    started_at: run.approved_at,
    finished_at: finishedAt,
    tasks,
    events,
    verification,
    brief_id: state === "completed" || state === "partial" ? input.briefId : null,
  };
}
