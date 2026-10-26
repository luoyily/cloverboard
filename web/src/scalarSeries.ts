import { seriesKey, type ScalarPoint, type ScalarSeries } from "./api";
import { RequestCache } from "./dataCache";

export interface SeriesRequest { runId: string; metric: string }
export type MetricSeries = ReadonlyMap<string, ScalarPoint[]>;
export type SeriesSnapshot = ReadonlyMap<string, MetricSeries>;
interface SeriesResult extends SeriesRequest { points: ScalarPoint[] }

// Retain one displayed version per selected run/metric, including folded groups.
// The request LRU is only for reuse beyond this working set, not its owner.
export class ScalarSeriesStore {
  private versions = new Map<string, string>();
  private snapshot: SeriesSnapshot = new Map();

  read(): SeriesSnapshot { return this.snapshot; }
  has(request: SeriesRequest, version: string): boolean {
    return this.versions.get(seriesKey(request.runId, request.metric)) === version;
  }

  retain(runIds: Set<string>): void {
    let next: Map<string, MetricSeries> | undefined;
    for (const [metric, runs] of this.snapshot) {
      const kept = new Map([...runs].filter(([runId]) => runIds.has(runId)));
      if (kept.size === runs.size) continue;
      for (const runId of runs.keys()) if (!runIds.has(runId)) this.versions.delete(seriesKey(runId, metric));
      next ??= new Map(this.snapshot);
      if (kept.size) next.set(metric, kept); else next.delete(metric);
    }
    if (next) this.snapshot = next;
  }

  apply(results: SeriesResult[], version: string): void {
    const changed = new Map<string, Map<string, ScalarPoint[]>>();
    for (const result of results) {
      this.versions.set(seriesKey(result.runId, result.metric), version);
      const runs = changed.get(result.metric) ?? this.snapshot.get(result.metric);
      if (runs?.get(result.runId) === result.points) continue;
      if (!changed.has(result.metric)) changed.set(result.metric, new Map(runs));
      changed.get(result.metric)!.set(result.runId, result.points);
    }
    if (changed.size) this.snapshot = new Map([...this.snapshot, ...changed]);
  }
}

interface LoadOptions {
  store: ScalarSeriesStore;
  cache: RequestCache<ScalarPoint[]>;
  selectedIds: Set<string>;
  requests: SeriesRequest[];
  smoothing: number;
  refreshTick: number;
  load: (runId: string, metrics: string[], smoothing: number) => Promise<ScalarSeries[]>;
  schedule: (publish: () => void) => () => void;
  onPublish: (snapshot: SeriesSnapshot, pending: Set<string>) => void;
  onError: () => void;
}

export function loadScalarSeries({ store, cache, selectedIds, requests, smoothing, refreshTick, load, schedule, onPublish, onError }: LoadOptions): () => void {
  const version = JSON.stringify([smoothing, refreshTick]);
  store.retain(selectedIds);
  const unique = new Map(requests.map((request) => [seriesKey(request.runId, request.metric), request]));
  const missing = [...unique.values()].filter((request) => !store.has(request, version));
  const pending = new Set(missing.map(({ runId, metric }) => seriesKey(runId, metric)));
  onPublish(store.read(), new Set(pending));
  let cancelled = false, unschedule: (() => void) | undefined;
  let results: SeriesResult[] = [], completed: string[] = [], failed = false;
  function commit() {
    store.apply(results, version);
    for (const key of completed) pending.delete(key);
    results = []; completed = [];
  }
  function flush() {
    unschedule = undefined;
    commit();
    if (failed) { failed = false; onError(); }
    onPublish(store.read(), new Set(pending));
  }
  function finish(request: SeriesRequest, points?: ScalarPoint[]) {
    if (cancelled) return;
    if (points) results.push({ ...request, points }); else failed = true;
    completed.push(seriesKey(request.runId, request.metric));
    unschedule ??= schedule(flush);
  }
  const byRun = new Map<string, SeriesRequest[]>();
  for (const request of missing) {
    const group = byRun.get(request.runId) ?? [];
    group.push(request); byRun.set(request.runId, group);
  }
  for (const [runId, entries] of byRun) for (let start = 0; start < entries.length; start += 16) {
    const batch = entries.slice(start, start + 16);
    const keyed = new Map(batch.map((request) => [JSON.stringify([runId, request.metric, smoothing, refreshTick]), request.metric]));
    const promises = cache.loadMany([...keyed.keys()], async (keys) => {
      const records = await load(runId, keys.map((key) => keyed.get(key)!), smoothing);
      const points = new Map(records.map((record) => [record.metric, record.points]));
      return keys.map((key) => {
        const value = points.get(keyed.get(key)!);
        if (!value) throw Error("Incomplete series batch.");
        return value;
      });
    });
    batch.forEach((request, index) => void promises[index].then((points) => finish(request, points), () => finish(request)));
  }
  return () => {
    cancelled = true;
    unschedule?.();
    // Keep completed results when a group changes before the next frame.
    // Later network completions remain in the bounded request cache only.
    commit();
  };
}
