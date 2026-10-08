import type { WorkerType } from "../types/contracts";
import type { IconName } from "../components/ui/Icon";
import type { SourceToggle } from "../types/app";

export interface WorkerMeta {
  /** Suffix of the `.wk.*` class in edrak.css. */
  css: "int" | "comp" | "mkt" | "cust";
  /** The colour token for this worker's progress bar and source squares. */
  color: string;
  icon: IconName;
  /** The source toggle that decides whether this worker gets a task. */
  source: SourceToggle;
}

export const WORKER_META: Record<WorkerType, WorkerMeta> = {
  internal_intelligence: { css: "int", color: "var(--w-int)", icon: "building", source: "internal_files" },
  competitor_intelligence: { css: "comp", color: "var(--w-comp)", icon: "target", source: "competitor_web" },
  market_intelligence: { css: "mkt", color: "var(--w-mkt)", icon: "trend", source: "news_open_data" },
  customer_trends: { css: "cust", color: "var(--w-cust)", icon: "users", source: "reviews_social" },
};

/** Short key used in translation keys, for example `worker.name.int`. */
export function workerKey(worker: WorkerType): "int" | "comp" | "mkt" | "cust" {
  return WORKER_META[worker].css;
}
