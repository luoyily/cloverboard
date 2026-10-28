export interface RunSummary {
  run_id: string;
  name: string | null;
  started_at: number;
  has_scalars: boolean;
  last_recorded_at: number | null;
  config: Record<string, unknown>;
  source: string;
  source_label?: string | null;
  source_online?: boolean;
  has_notes?: boolean;
  note_excerpt?: string;
  tags?: string[];
  tags_writable?: boolean;
  time_origin?: number | null;
  time_origin_kind?: "step_zero" | "first_sample" | null;
}

export interface ScalarPoint {
  step: number;
  value: number | null;
  smoothed?: number | null;
  wall_time?: number | null;
}

export interface ScalarSummary { latest: ScalarPoint | null; minimum: number | null; maximum: number | null; first_hit: ScalarPoint | null }
export interface ScalarBounds { minimum: number | null; maximum: number | null; positive_minimum?: number | null }
export interface ImageRecord { image_id: string; name: string; step: number; wall_time: number; path: string; width: number; height: number; format: string; caption: string }
export interface SnapshotManifest { status: "complete" | "partial" | "error"; created_at?: number; root?: string; files?: { path: string; bytes: number }[]; skipped?: { path: string; reason: string }[]; total_bytes?: number; archive_bytes?: number; error?: string; limit_reason?: string }
export interface RunDetails { config: Record<string, unknown>; env: Record<string, unknown>; snapshot: SnapshotManifest | null }
export interface SourceStatus { source_id: string; label: string; kind: "local" | "peer"; online: boolean }
export interface RunCatalog { runs: RunSummary[]; sources: SourceStatus[] }

export interface MetricField { metric: string; group: string; name: string }
export interface MediaNameSummary { name: string; count: number; step_count: number; first_step: number; last_step: number }
export interface MediaTimeline { steps: number[]; total_steps: number }
export interface ImagePage { images: ImageRecord[]; step: number | null; total: number; next_offset: number | null }
export interface AudioRecord { audio_id: string; name: string; step: number; wall_time: number; path: string; sample_rate: number; channels: number; frames: number; caption: string }
export interface AudioPage { audio: AudioRecord[]; step: number | null; total: number; next_offset: number | null }
export interface TextRecord { name: string; step: number; wall_time: number; text: string }
export function fetchTextNames(runId: string, signal?: AbortSignal): Promise<MediaNameSummary[]> { return getJson(`/api/text-names?${new URLSearchParams({ run_id: runId })}`, { signal }); }
export function fetchTextTimeline(runId: string, name: string, maxSteps: number, signal?: AbortSignal): Promise<MediaTimeline> { return getJson(`/api/text-timeline?${new URLSearchParams({ run_id: runId, name, max_steps: String(maxSteps) })}`, { signal }); }
export function fetchText(runId: string, name: string, step: number | null, signal?: AbortSignal): Promise<TextRecord | null> {
  const query = new URLSearchParams({ run_id: runId, name });
  if (step !== null) query.set("step", String(step));
  return getJson(`/api/text?${query}`, { signal });
}
export function fetchAudioNames(runId: string, signal?: AbortSignal): Promise<MediaNameSummary[]> { return getJson(`/api/audio-names?${new URLSearchParams({ run_id: runId })}`, { signal }); }
export function fetchAudioTimeline(runId: string, name: string, maxSteps: number, signal?: AbortSignal): Promise<MediaTimeline> { return getJson(`/api/audio-timeline?${new URLSearchParams({ run_id: runId, name, max_steps: String(maxSteps) })}`, { signal }); }
export function fetchAudioPage(runId: string, name: string, step: number | null, limit: number, offset = 0, signal?: AbortSignal): Promise<AudioPage> {
  const query = new URLSearchParams({ run_id: runId, name, limit: String(limit), offset: String(offset) });
  if (step !== null) query.set("step", String(step));
  return getJson(`/api/audio?${query}`, { signal });
}
export function updateRunTags(runId: string, add: string[], remove: string[]): Promise<{ tags: string[] }> {
  return getJson(`/api/run-tags?${new URLSearchParams({ run_id: runId })}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ add, remove }) });
}
export interface RunNote { body: string; updated_at: number | null; revision: number; writable: boolean }
export function fetchImageNames(runId: string, signal?: AbortSignal): Promise<MediaNameSummary[]> { return getJson(`/api/image-names?${new URLSearchParams({ run_id: runId })}`, { signal }); }
export function fetchImageTimeline(runId: string, name: string, maxSteps: number, signal?: AbortSignal): Promise<MediaTimeline> { return getJson(`/api/image-timeline?${new URLSearchParams({ run_id: runId, name, max_steps: String(maxSteps) })}`, { signal }); }
export function fetchImagePage(runId: string, name: string, step: number | null, limit: number, offset = 0, signal?: AbortSignal): Promise<ImagePage> {
  const query = new URLSearchParams({ run_id: runId, name, limit: String(limit), offset: String(offset) });
  if (step !== null) query.set("step", String(step));
  return getJson(`/api/images?${query}`, { signal });
}
export function fetchNote(runId: string, signal?: AbortSignal): Promise<RunNote> { return getJson(`/api/run-notes?${new URLSearchParams({ run_id: runId })}`, { signal }); }
export function saveNote(runId: string, body: string, revision: number): Promise<RunNote> { return getJson(`/api/run-notes?${new URLSearchParams({ run_id: runId })}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body, revision }) }); }

export async function pooledResults<T, R>(items: T[], action: (item: T) => Promise<R>, concurrency = 4, onResult?: (result: PromiseSettledResult<R>, index: number) => void, signal?: AbortSignal): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = new Array(items.length);
  let index = 0;
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (index < items.length && !signal?.aborted) {
      const current = index++;
      try { results[current] = { status: "fulfilled", value: await action(items[current]) }; }
      catch (reason) { results[current] = { status: "rejected", reason }; }
      onResult?.(results[current], current);
    }
  }));
  if (signal?.aborted) for (let current = 0; current < items.length; current++) {
    results[current] ??= { status: "rejected", reason: signal.reason };
  }
  return results;
}
export function fetchRunDetails(runId: string, signal?: AbortSignal): Promise<RunDetails> { return getJson(`/api/run-details?${new URLSearchParams({ run_id: runId })}`, { signal }); }
export function assetUrl(runId: string, assetId: string, thumbnail = false): string { return `/api/asset?${new URLSearchParams({ run_id: runId, asset_id: assetId, thumbnail: String(thumbnail) })}`; }

export interface ScalarSeries {
  run_id: string;
  metric: string;
  points: ScalarPoint[];
}

export interface MetricList {
  run_id: string;
  metrics: string[];
  fields: MetricField[];
}

export function seriesKey(runId: string, metric: string): string {
  return `${runId}\u0000${metric}`;
}

async function responseError(response: Response): Promise<Error> {
  const payload = await response.json().catch(() => null);
  const detail = payload?.detail;
  const validation = Array.isArray(detail) ? detail.flatMap((issue) => {
    if (typeof issue?.msg !== "string") return [];
    const field = Array.isArray(issue.loc) ? issue.loc.slice(1).join(".") : "";
    return [field ? `${field}: ${issue.msg}` : issue.msg];
  }).join("; ") : "";
  return new Error(typeof detail === "string" ? detail : validation || `Unable to complete the request (${response.status}).`);
}

export async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw await responseError(response);
  return (await response.json()) as T;
}

export function fetchRuns(localOnly = false, signal?: AbortSignal): Promise<RunSummary[]> {
  return getJson<RunSummary[]>(localOnly ? "/api/runs?local_only=true" : "/api/runs", { signal });
}

interface CachedResponse<T> { etag: string; data: T }
export type RunsCacheEntry = CachedResponse<RunSummary[]>;
export type RunCatalogCacheEntry = CachedResponse<RunCatalog>;

async function fetchWithEtag<T>(url: string, cached: CachedResponse<T> | null, signal?: AbortSignal): Promise<CachedResponse<T>> {
  const response = await fetch(url, {
    headers: cached?.etag ? { "If-None-Match": cached.etag } : {},
    signal,
  });
  if (response.status === 304 && cached) return cached;
  if (!response.ok) throw await responseError(response);
  return { etag: response.headers.get("etag") ?? "", data: (await response.json()) as T };
}

export function fetchRunsWithEtag(cached: RunsCacheEntry | null, localOnly = false, signal?: AbortSignal): Promise<RunsCacheEntry> {
  return fetchWithEtag(localOnly ? "/api/runs?local_only=true" : "/api/runs", cached, signal);
}

/** Runs and source health are revalidated together from the same completed scan. */
export function fetchRunCatalogWithEtag(cached: RunCatalogCacheEntry | null, signal?: AbortSignal): Promise<RunCatalogCacheEntry> {
  return fetchWithEtag("/api/run-catalog", cached, signal);
}

export function fetchMetrics(runId: string, signal?: AbortSignal): Promise<MetricList> {
  const params = new URLSearchParams({ run_id: runId });
  return getJson<MetricList>(`/api/metrics?${params}`, { signal });
}

export function fetchSeries(
  runId: string,
  metric: string,
  maxPoints = 800,
  options?: { stepMin?: number; stepMax?: number; smoothing?: number; signal?: AbortSignal },
): Promise<ScalarSeries> {
  const params = new URLSearchParams({
    run_id: runId,
    metric,
    max_points: String(maxPoints),
  });
  if (options?.stepMin !== undefined) params.set("step_min", String(Math.ceil(options.stepMin)));
  if (options?.stepMax !== undefined) params.set("step_max", String(Math.floor(options.stepMax)));
  if (options?.smoothing) params.set("smoothing", String(options.smoothing));
  return getJson<ScalarSeries>(`/api/scalars?${params}`, { signal: options?.signal });
}

export function fetchSeriesBatch(runId: string, metrics: string[], smoothing: number): Promise<ScalarSeries[]> {
  const params = new URLSearchParams({ run_id: runId, max_points: "800", smoothing: String(smoothing) });
  metrics.forEach((metric) => params.append("metrics", metric));
  return getJson(`/api/scalars-batch?${params}`);
}

export function fetchSummary(runId: string, metric: string, target?: { value: number; direction: string }, signal?: AbortSignal): Promise<ScalarSummary> {
  const params = new URLSearchParams({ run_id: runId, metric });
  if (target) { params.set("target", String(target.value)); params.set("direction", target.direction); }
  return getJson(`/api/scalar-summary?${params}`, { signal });
}

export function fetchBounds(runId: string, metric: string, stepMin?: number, stepMax?: number, signal?: AbortSignal): Promise<ScalarBounds> {
  const params = new URLSearchParams({ run_id: runId, metric });
  if (stepMin !== undefined) params.set("step_min", String(Math.ceil(stepMin)));
  if (stepMax !== undefined) params.set("step_max", String(Math.floor(stepMax)));
  return getJson(`/api/scalar-bounds?${params}`, { signal });
}
