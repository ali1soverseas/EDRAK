/**
 * The stage tracker at the top of Live run: Plan, Dispatch, Workers, Verify, Synthesize,
 * Brief. Finished steps are solid, the current one holds the animated hub while the run
 * is running, and the rest are dashed.
 */
import { Fragment } from "react";
import { useI18n, type TKey } from "../i18n";
import type { RunStage, RunView } from "../types/app";
import { Hub } from "./ui/Hub";
import { Icon } from "./ui/Icon";

type StepState = "done" | "current" | "pending" | "failed" | "stopped";

const STAGES: readonly RunStage[] = ["plan", "dispatch", "workers", "verify", "synthesize", "brief"];

const LABEL: Record<RunStage, TKey> = {
  plan: "run.step.plan",
  dispatch: "run.step.dispatch",
  workers: "run.step.workers",
  verify: "run.step.verify",
  synthesize: "run.step.synthesize",
  brief: "run.step.brief",
};

function stepStates(run: RunView): StepState[] {
  const index = STAGES.indexOf(run.stage);
  switch (run.state) {
    case "completed":
    case "partial":
      return STAGES.map(() => "done");
    case "failed":
      return STAGES.map((_, i) => (i < 2 ? "done" : i === 2 ? "failed" : "pending"));
    case "cancelled":
      return STAGES.map((_, i) => (i < index ? "done" : i === index ? "stopped" : "pending"));
    case "running":
      return STAGES.map((_, i) => (i < index ? "done" : i === index ? "current" : "pending"));
  }
}

function StepIcon({ state, running }: { state: StepState; running: boolean }) {
  switch (state) {
    case "done":
      return (
        <span className="step-box done">
          <Icon name="check" size={16} stroke={2.4} />
        </span>
      );
    case "current":
      return (
        <span className="step-box current">
          {running ? <Hub size={22} color="#0742ED" line={2.4} node={7.2} center={3.6} /> : <Icon name="refresh" size={16} />}
        </span>
      );
    case "failed":
      return (
        <span className="step-box failed">
          <Icon name="x" size={16} stroke={2.4} />
        </span>
      );
    case "stopped":
      return (
        <span className="step-box stopped">
          <Icon name="pause" size={16} />
        </span>
      );
    case "pending":
      return <span className="step-box pending" />;
  }
}

export function StageTracker({ run }: { run: RunView }) {
  const { t, tn, formatClock } = useI18n();
  const states = stepStates(run);
  const running = run.state === "running";
  const done = run.tasks.filter((task) => task.state === "done").length;
  const failed = run.tasks.filter((task) => task.state === "failed").length;

  const sub = (stage: RunStage, state: StepState): string => {
    switch (stage) {
      case "plan":
        return t("run.step.approved", { time: formatClock(run.approved_at) });
      case "dispatch":
        return tn("plural.workers", run.tasks.length);
      case "workers":
        return failed > 0 ? t("run.step.workersMixed", { done, failed }) : t("run.step.workersDone", { done, total: run.tasks.length });
      case "brief":
        return state === "done" ? t("run.step.ready") : t("run.step.waiting");
      default:
        if (state === "done") return t("run.step.done");
        if (state === "current") return t("run.step.running");
        if (state === "stopped") return t("run.step.stopped");
        return t("run.step.waiting");
    }
  };

  return (
    <ol className="card stage-tracker" aria-label={t("run.stages")}>
      {STAGES.map((stage, index) => {
        const state = states[index];
        return (
          <Fragment key={stage}>
            <li className="step" aria-current={state === "current" ? "step" : undefined}>
              <StepIcon state={state} running={running} />
              <div className={`s13 b6 ${state === "pending" ? "t3" : ""}`.trim()}>{t(LABEL[stage])}</div>
              <div className="s12 t3" style={{ marginTop: -4 }}>
                {sub(stage, state)}
              </div>
            </li>
            {index < STAGES.length - 1 && <div className={`step-line ${states[index] === "done" ? "solid" : ""}`.trim()} aria-hidden="true" />}
          </Fragment>
        );
      })}
    </ol>
  );
}
