import type { WorkerType } from "../../types/contracts";
import { WORKER_META } from "../../lib/workers";
import { Icon } from "./Icon";

interface WorkerBadgeProps {
  worker: WorkerType;
  /** The 36 px badge (`.wk.lg`) instead of the 28 px one. */
  large?: boolean;
  iconSize?: number;
}

export function WorkerBadge({ worker, large = false, iconSize }: WorkerBadgeProps) {
  const meta = WORKER_META[worker];
  return (
    <span className={`wk ${meta.css} ${large ? "lg" : ""}`.trim()}>
      <Icon name={meta.icon} size={iconSize ?? (large ? 19 : 15)} />
    </span>
  );
}
