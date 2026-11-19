import { useCallback, useEffect, useRef, useState } from "react";

// Sequential data loader: the newest request wins, so a slow response can never
// overwrite a newer selection. Unmounting invalidates any in-flight request.
export function useAsyncData(load, deps = []) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  const seq = useRef(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  const run = useCallback(async () => {
    const id = (seq.current += 1);
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const data = await loadRef.current();
      if (id === seq.current) setState({ data, loading: false, error: null });
    } catch (err) {
      if (id === seq.current) {
        setState((current) => ({
          data: current.data,
          loading: false,
          error: err && err.message ? err.message : String(err),
        }));
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    run();
    return () => {
      seq.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { ...state, reload: run };
}

// One-shot in-flight guard for form submits (prevents double submits).
export function useInFlight() {
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const guard = useCallback(async (fn) => {
    if (busyRef.current) return undefined;
    busyRef.current = true;
    setBusy(true);
    try {
      return await fn();
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, []);
  return [busy, guard];
}
