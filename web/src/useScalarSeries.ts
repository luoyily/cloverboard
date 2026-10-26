import { useEffect, useState } from "react";
import { fetchSeriesBatch, seriesKey, type ScalarPoint } from "./api";
import { RequestCache } from "./dataCache";
import { loadScalarSeries, ScalarSeriesStore, type SeriesRequest } from "./scalarSeries";

function schedulePublish(publish: () => void): () => void {
  let waiting = true;
  function cancel() { waiting = false; cancelAnimationFrame(frame); window.clearTimeout(timer); }
  function flush() { if (waiting) { cancel(); publish(); } }
  const frame = requestAnimationFrame(flush);
  // Background documents must still finish loading when animation frames pause.
  const timer = window.setTimeout(flush, 100);
  return cancel;
}

export function useScalarSeries(selectedIds: string[], requests: SeriesRequest[], smoothing: number, refreshTick: number, enabled: boolean) {
  const [store] = useState(() => new ScalarSeriesStore());
  const [cache] = useState(() => new RequestCache<ScalarPoint[]>(128));
  const [series, setSeries] = useState(() => store.read());
  const [progress, setProgress] = useState({ key: "", pending: new Set<string>() });
  const [error, setError] = useState(false);
  const selectionKey = JSON.stringify(selectedIds);
  const requestKey = JSON.stringify([requests, smoothing, refreshTick]);
  const version = JSON.stringify([smoothing, refreshTick]);
  useEffect(() => {
    if (!enabled) return;
    setError(false);
    return loadScalarSeries({ store, cache, selectedIds: new Set(selectedIds), requests, smoothing, refreshTick,
      load: fetchSeriesBatch, schedule: schedulePublish,
      onPublish: (snapshot, pending) => { setSeries(snapshot); setProgress({ key: requestKey, pending }); },
      onError: () => setError(true),
    });
  }, [enabled, selectionKey, requestKey]);
  const loadingMetrics = new Set(requests.filter((request) => progress.key === requestKey
    ? progress.pending.has(seriesKey(request.runId, request.metric))
    : !store.has(request, version)).map(({ metric }) => metric));
  return { series, loadingMetrics, error };
}
