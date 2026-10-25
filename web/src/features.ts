import type { RunSummary, ScalarPoint, ScalarSummary } from "./api";
import { flattenConfig, stableValue } from "./comparison";

export interface Target { value: number; direction: "gte" | "lte" }
export type TimeDisplay = "hidden" | "elapsed" | "wall";
export type DeltaDisplay = "baseline" | "target" | "both" | "hidden";

export function targetDelta(point: ScalarPoint | undefined | null, target: Target | undefined): number | null {
  return target && point?.value != null && Number.isFinite(point.value) ? point.value - target.value : null;
}

export function deltaColumns(mode: DeltaDisplay, baseline: boolean, target: boolean) {
  return {
    baseline: baseline && (mode === "baseline" || mode === "both"),
    target: target && (mode === "target" || mode === "both"),
  };
}
export interface ChartZoom { x_min: number; x_max: number; y_min: number | null; y_max: number | null }
export function effectiveWarmup(override: number | null | undefined, global: number | null): number | undefined {
  return (override === undefined ? global : override) ?? undefined;
}

export function clearYZoom(zooms: Record<string, ChartZoom>, metrics: (metric: string) => boolean): Record<string, ChartZoom> {
  return Object.fromEntries(Object.entries(zooms).map(([metric, zoom]) => [metric, metrics(metric) ? { ...zoom, y_min: null, y_max: null } : zoom]));
}
export interface TopK { enabled: boolean; metric: string; direction: "min" | "max"; readout: "latest" | "best"; k: number }
export interface ColorSettings {
  mode: "random" | "parameter" | "continuous";
  field: string;
  domain: [number, number];
  overrides: Record<string, string>;
  assignments: Record<string, number>;
  top_k: TopK;
}
export interface ColorRegistry { version: 1; seed: string; slots: Record<string, number> }

export function defaultColors(): ColorSettings {
  return { mode: "random", field: "", domain: [0, 1], overrides: {}, assignments: {}, top_k: { enabled: false, metric: "", direction: "max", readout: "latest", k: 3 } };
}

export function runColorKey(run: RunSummary): string { return `run:${JSON.stringify([run.source, run.run_id])}`; }
export function parameterValue(run: RunSummary, field: string): unknown { return flattenConfig(run.config).get(field); }
export function categoryKey(field: string, value: unknown): string { return `parameter:${JSON.stringify([field, stableValue(value)])}`; }

export function matchesTagFilter(run: RunSummary, tags: string[], match: "any" | "all" | "none" | "empty"): boolean {
  const recorded = run.tags ?? [];
  if (match === "empty") return !recorded.length;
  if (!tags.length) return true;
  if (match === "all") return tags.every((tag) => recorded.includes(tag));
  if (match === "none") return tags.every((tag) => !recorded.includes(tag));
  return tags.some((tag) => recorded.includes(tag));
}

export function targetMet(point: ScalarPoint | null | undefined, target: Target): boolean | null {
  if (point?.value == null || !Number.isFinite(point.value)) return null;
  return target.direction === "gte" ? point.value >= target.value : point.value <= target.value;
}

export function baselineDelta(point: ScalarPoint | undefined | null, base: ScalarPoint | undefined | null): number | null {
  if (point?.value == null || base?.value == null || !Number.isFinite(point.value) || !Number.isFinite(base.value)) return null;
  return point.value - base.value;
}

export function topRunIds(runs: RunSummary[], summaries: Map<string, ScalarSummary>, settings: TopK): Set<string> | null {
  if (!settings.enabled || !settings.metric) return null;
  const key = (id: string) => `${id}\u0000${settings.metric}`;
  const ranked = runs.map((run) => {
    const summary = summaries.get(key(run.run_id));
    return { id: run.run_id, value: settings.readout === "latest" ? summary?.latest?.value : settings.direction === "min" ? summary?.minimum : summary?.maximum };
  }).filter((item): item is { id: string; value: number } => typeof item.value === "number" && Number.isFinite(item.value));
  ranked.sort((a, b) => (settings.direction === "min" ? a.value - b.value : b.value - a.value) || a.id.localeCompare(b.id));
  return new Set(ranked.slice(0, settings.k).map((item) => item.id));
}
