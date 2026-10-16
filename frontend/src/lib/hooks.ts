import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";

export interface AsyncState<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => void;
  setData: (value: T) => void;
}

/** Runs `load` on mount and whenever `deps` change. Stale answers are dropped. */
export function useAsync<T>(load: () => Promise<T>, deps: DependencyList): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(undefined);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let current = true;
    setLoading(true);
    load().then(
      (value) => {
        if (!current) return;
        setData(value);
        setError(undefined);
        setLoading(false);
      },
      (reason: unknown) => {
        if (!current) return;
        setError(reason);
        setLoading(false);
      },
    );
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { data, error, loading, reload, setData };
}

interface PollOptions<T> {
  /** Milliseconds between requests. The live run polls every one to two seconds. */
  intervalMs: number;
  /** Stop polling once this returns true for the latest data. */
  stopWhen?: (data: T) => boolean;
}

/**
 * Fetches now, then again every `intervalMs` until `stopWhen` says the data is final.
 * Requests never overlap, and nothing is fetched while the tab is hidden.
 */
export function usePolled<T>(
  load: () => Promise<T>,
  deps: DependencyList,
  { intervalMs, stopWhen }: PollOptions<T>,
): Omit<AsyncState<T>, "setData"> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(undefined);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const stopRef = useRef(stopWhen);
  stopRef.current = stopWhen;

  useEffect(() => {
    let current = true;
    let timer: number | undefined;
    let inFlight = false;

    const run = async () => {
      if (inFlight || !current) return;
      if (document.hidden) {
        timer = window.setTimeout(run, intervalMs);
        return;
      }
      inFlight = true;
      try {
        const value = await load();
        if (!current) return;
        setData(value);
        setError(undefined);
        if (stopRef.current?.(value)) return;
      } catch (reason) {
        if (!current) return;
        setError(reason);
      } finally {
        inFlight = false;
        if (current) setLoading(false);
      }
      if (current) timer = window.setTimeout(run, intervalMs);
    };

    setLoading(true);
    void run();
    return () => {
      current = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs, tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  return { data, error, loading, reload };
}

/** Current time in ms, updated every `intervalMs` while `active`. Drives the elapsed clock. */
export function useNow(active: boolean, intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [active, intervalMs]);
  return now;
}
