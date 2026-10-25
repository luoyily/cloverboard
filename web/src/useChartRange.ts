import { useEffect, useRef, useState } from "react";
import { fetchBounds, type RunSummary, type ScalarBounds } from "./api";
import { RequestCache } from "./dataCache";

const boundsCache = new RequestCache<ScalarBounds>(512, 4);

export function useWarmupRange(runs: RunSummary[], metric: string, step: number | undefined, minimum: number | undefined, maximum: number | undefined, refreshTick: number) {
  const key = JSON.stringify([runs.map((run) => run.run_id), metric, step, minimum, maximum, refreshTick]);
  const [result, setResult] = useState<{ key: string; bounds: ScalarBounds[]; status: "ready" | "empty" | "error" } | null>(null);
  useEffect(() => {
    if (step === undefined) return;
    let cancelled = false;
    const cutoff = Math.max(step, minimum ?? -Infinity);
    void (maximum !== undefined && cutoff > maximum ? Promise.resolve([]) : Promise.all(runs.map((run) => {
      const requestKey = JSON.stringify([run.run_id, metric, cutoff, maximum, refreshTick]);
      return boundsCache.load(requestKey, () => fetchBounds(run.run_id, metric, cutoff, maximum));
    })))
      .then((bounds) => {
        const empty = !bounds.some((bound) => bound.minimum !== null && bound.maximum !== null);
        if (!cancelled) setResult({ key, bounds, status: empty ? "empty" : "ready" });
      })
      .catch(() => { if (!cancelled) setResult({ key, bounds: [], status: "error" }); });
    return () => { cancelled = true; };
  }, [key]);
  return step === undefined ? null : result?.key === key ? result : { key, bounds: [], status: "loading" as const };
}

// Expand before painting new data; wait briefly before contracting a live range.
export function useSettledDomain(low: number, high: number, key: string): [number, number] {
  const [settled, setSettled] = useState({ key, low, high });
  const current = useRef(settled);
  const expanded = settled.key === key ? { key, low: Math.min(low, settled.low), high: Math.max(high, settled.high) } : { key, low, high };
  current.current = expanded;
  useEffect(() => {
    setSettled(current.current);
    const timer = window.setTimeout(() => setSettled({ key, low, high }), 1800);
    return () => window.clearTimeout(timer);
  }, [key, low, high]);
  return [expanded.low, expanded.high];
}
