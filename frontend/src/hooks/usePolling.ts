import { useCallback, useEffect, useRef, useState } from "react";

export interface PollingState<T> {
  data: T | null; error: Error | null; loading: boolean; updatedAt: Date | null; refresh: () => void;
}

/** Polls `fetcher`, keeping the last good data visible if a refresh fails. Re-runs when `deps` change. */
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs: number, deps: unknown[] = [], enabled = true): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    setLoading(true);
    const run = async () => {
      try {
        const result = await fetcherRef.current();
        if (!cancelled) { setData(result); setError(null); setUpdatedAt(new Date()); }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (!cancelled) { setLoading(false); if (enabled) timer = window.setTimeout(run, intervalMs); }
      }
    };
    void run();
    return () => { cancelled = true; if (timer) window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, enabled, tick, ...deps]);

  return { data, error, loading, updatedAt, refresh };
}
