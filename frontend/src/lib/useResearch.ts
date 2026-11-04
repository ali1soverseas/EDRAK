import { edrak, type ResearchRun } from "../services/edrak";
import { usePolled } from "./hooks";
import { isActive } from "./research";

const POLL_MS = 3000;

/**
 * The company's latest research run. It is fetched when the screen opens and every few seconds while the run is
 * queued or running, then left alone. `reload` fetches again and starts polling if a new run began.
 */
export function useLatestRun() {
  const polled = usePolled(() => edrak.researchRuns(1), [], {
    intervalMs: POLL_MS,
    stopWhen: (runs) => !isActive(runs[0]),
  });
  const run: ResearchRun | undefined = polled.data?.[0];
  return { run, loading: polled.data === undefined && polled.loading, error: polled.error, reload: polled.reload };
}
