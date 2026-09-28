import { useCallback, useEffect, useRef, useState } from 'react';

export interface AsyncState<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  /** Reload; `soft` keeps the current data visible while fetching. */
  reload: (soft?: boolean) => Promise<void>;
  setData: (updater: T | ((prev: T | null) => T | null)) => void;
}

/**
 * Fetch-on-mount hook with reload. `deps` re-runs the fetch; the loader always uses the latest closure.
 */
export function useAsync<T>(loader: () => Promise<T>, deps: readonly unknown[] = [], options: { enabled?: boolean } = {}): AsyncState<T> {
  const enabled = options.enabled ?? true;
  const [data, setDataState] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const fnRef = useRef(loader);
  fnRef.current = loader;
  const seq = useRef(0);

  const reload = useCallback(
    async (soft = true) => {
      if (!enabled) return;
      const id = ++seq.current;
      if (!soft) setLoading(true);
      try {
        const result = await fnRef.current();
        if (id === seq.current) {
          setDataState(result);
          setError(null);
        }
      } catch (err) {
        if (id === seq.current) setError(err);
      } finally {
        if (id === seq.current) setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [enabled],
  );

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    setLoading(true);
    void reload(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);

  const setData = useCallback((updater: T | ((prev: T | null) => T | null)) => {
    setDataState((prev) => (typeof updater === 'function' ? (updater as (p: T | null) => T | null)(prev) : updater));
  }, []);

  return { data, error, loading, reload, setData };
}

/** Tracks a pending mutation so buttons can disable themselves. */
export function useBusy(): [boolean, <R>(p: Promise<R>) => Promise<R>] {
  const [busy, setBusy] = useState(false);
  const run = useCallback(async <R,>(p: Promise<R>): Promise<R> => {
    setBusy(true);
    try {
      return await p;
    } finally {
      setBusy(false);
    }
  }, []);
  return [busy, run];
}
