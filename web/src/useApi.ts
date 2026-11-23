import { useCallback, useEffect, useRef, useState } from "react";

export type Loaded<T> = { data: T | null; error: boolean; loading: boolean; reload: () => void };

/** Carrega `fn` ao montar (e a cada `pollMs`, se informado). 401 é tratado globalmente em api.ts. */
export function useApi<T>(fn: () => Promise<T>, deps: unknown[] = [], pollMs?: number): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  const ref = useRef(fn);
  ref.current = fn;

  const load = useCallback(async () => {
    try {
      const out = await ref.current();
      if (alive.current) {
        setData(out);
        setError(false);
      }
    } catch {
      if (alive.current) setError(true);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    void load();
    const timer = pollMs ? setInterval(() => void load(), pollMs) : undefined;
    return () => {
      alive.current = false;
      if (timer) clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, pollMs, ...deps]);

  return { data, error, loading, reload: () => void load() };
}
