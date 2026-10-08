import { describe, expect, it } from "vitest";
import { defaultForm, formToRequest } from "../../lib/form";
import type { CompanyFields } from "../../types/app";
import { PACKS } from "./packs";
import { planFor } from "./planner";
import { simulateRun, timeline, type RunRecord, type Scenario } from "./simulate";

const company: CompanyFields = {
  name: "Nile Ledger",
  aliases: [],
  industry: null,
  description: "",
  offerings: [],
  markets: [],
  strategic_goals: "",
  website: "",
  socials: [],
};

const APPROVED = Date.parse("2026-10-08T09:00:00.000Z");

function view(scenario: Scenario, secondsAfter: number, extra: Partial<RunRecord> = {}) {
  const form = { ...defaultForm("product_launch"), goal: "Q", market: "Egypt" };
  const request = formToRequest(form, company, "req-1");
  const plan = planFor(request, form);
  const run: RunRecord = { approved_at: new Date(APPROVED).toISOString(), approved_by: "Layla", speed: 1, scenario, cancelled_at: null, ...extra };
  return simulateRun({
    analysisId: "req-1",
    title: "Test",
    useCase: "product_launch",
    plan,
    pack: PACKS.product_launch,
    run,
    now: APPROVED + secondsAfter * 1000,
    briefId: "brief-1",
  });
}

describe("simulateRun", () => {
  it("starts with every worker queued and the Supervisor's first event", () => {
    const run = view("normal", 0);
    expect(run.state).toBe("running");
    expect(run.tasks.every((task) => task.state === "queued" && task.progress === 0)).toBe(true);
    expect(run.events).toHaveLength(1);
    expect(run.events[0].source).toBe("supervisor");
    expect(run.brief_id).toBeNull();
  });

  it("only ever moves progress forward", () => {
    let previous = run0();
    for (let seconds = 1; seconds <= 90; seconds += 1) {
      const current = view("normal", seconds).tasks.map((task) => task.progress);
      current.forEach((value, index) => expect(value).toBeGreaterThanOrEqual(previous[index]));
      previous = current;
    }
    function run0() {
      return view("normal", 0).tasks.map((task) => task.progress);
    }
  });

  it("finishes the fastest worker first and keeps the others running", () => {
    const run = view("normal", 30);
    const states = Object.fromEntries(run.tasks.map((task) => [task.worker, task.state]));
    expect(states.internal_intelligence).toBe("done");
    expect(states.market_intelligence).toBe("running");
    expect(run.stage).toBe("workers");
    expect(run.verification).toBe("waiting");
  });

  it("moves through verification and synthesis, then completes with a brief", () => {
    const line = timeline(["internal_intelligence", "competitor_intelligence", "market_intelligence", "customer_trends"], "normal");
    expect(view("normal", line.workersDone + 1).stage).toBe("verify");
    expect(view("normal", line.workersDone + 9).stage).toBe("synthesize");
    const done = view("normal", line.finish + 1);
    expect(done.state).toBe("completed");
    expect(done.brief_id).toBe("brief-1");
    expect(done.finished_at).not.toBeNull();
  });

  it("lets the Market worker fail while the run continues, then finishes partial", () => {
    const midway = view("market_fails", 40);
    expect(midway.state).toBe("running");
    const market = midway.tasks.find((task) => task.worker === "market_intelligence")!;
    expect(market.state).toBe("failed");
    expect(market.error).not.toBeNull();
    expect(midway.tasks.filter((task) => task.state === "running").length).toBeGreaterThan(0);
    expect(midway.events.some((event) => event.callout && event.kind === "failure")).toBe(true);

    expect(view("market_fails", 200).state).toBe("partial");
  });

  it("stops a run in which no worker reports, and offers no brief", () => {
    const run = view("run_fails", 100);
    expect(run.state).toBe("failed");
    expect(run.tasks.every((task) => task.state === "failed")).toBe(true);
    expect(run.brief_id).toBeNull();
  });

  it("freezes at the moment of cancellation", () => {
    const cancelled = view("normal", 300, { cancelled_at: new Date(APPROVED + 20_000).toISOString() });
    expect(cancelled.state).toBe("cancelled");
    expect(cancelled.brief_id).toBeNull();
    const later = view("normal", 900, { cancelled_at: new Date(APPROVED + 20_000).toISOString() });
    expect(later.tasks.map((task) => task.progress)).toEqual(cancelled.tasks.map((task) => task.progress));
  });

  it("reports the Supervisor's re-plan once the Competitor worker reaches its gap", () => {
    const run = view("normal", 45);
    const replan = run.events.find((event) => event.kind === "replan");
    expect(replan?.callout?.highlight).toBe(PACKS.product_launch.replan.task);
    expect(replan?.callout?.text).toContain(PACKS.product_launch.replan.task);
  });

  it("runs on a slower clock when asked to", () => {
    const slow = view("normal", 60, { speed: 0.1 });
    // 60 s at a tenth of the speed is 6 mock seconds: nothing is finished.
    expect(slow.tasks.every((task) => task.state !== "done")).toBe(true);
  });
});
