import type { RunSummary, ScalarPoint } from "./api";

export interface ParameterColumn {
  key: string;
  different: boolean;
}

export function stableValue(value: unknown): string {
  if (value === undefined) return "undefined";
  if (Array.isArray(value)) return `[${value.map(stableValue).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableValue(item)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function flattenConfig(config: Record<string, unknown>): Map<string, unknown> {
  const values = new Map<string, unknown>();
  function visit(object: Record<string, unknown>, prefix: string) {
    for (const [name, value] of Object.entries(object)) {
      const segment = name.replaceAll("\\", "\\\\").replaceAll(".", "\\.");
      const key = prefix ? `${prefix}.${segment}` : segment;
      if (value !== null && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length) {
        visit(value as Record<string, unknown>, key);
      } else {
        values.set(key, value);
      }
    }
  }
  visit(config, "");
  return values;
}

export function parameterColumns(configs: Map<string, unknown>[]): ParameterColumn[] {
  const names = [...new Set(configs.flatMap((config) => [...config.keys()]))].sort();
  return names.map((key) => ({
    key,
    different: new Set(configs.map((config) => stableValue(config.get(key)))).size > 1,
  }));
}

export function defaultMetricColumns(metrics: string[]): string[] {
  return [...metrics].sort((a, b) => {
    const priority = (name: string) => /^(val|eval|test)[/_]/i.test(name) ? 0 : 1;
    return priority(a) - priority(b) || a.localeCompare(b);
  }).slice(0, 2);
}

export function displayParameter(value: unknown): string {
  if (value === undefined) return "—";
  if (typeof value === "string") return value === "" ? '""' : value;
  return stableValue(value);
}

export function latestPoint(points: ScalarPoint[] | undefined): ScalarPoint | undefined {
  // The scalar endpoint preserves the final raw sample when downsampling.
  return points?.at(-1);
}

export function formatMetric(value: number | null | undefined, decimals?: number): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (decimals === undefined) {
    if (value !== 0 && (Math.abs(value) < 0.0001 || Math.abs(value) >= 1e6)) return value.toExponential(3);
    return Number(value.toPrecision(6)).toString();
  }
  const places = Math.max(0, Math.min(8, Math.trunc(decimals)));
  if (value !== 0 && (Math.abs(value) < Math.max(0.0001, 0.5 * 10 ** -places) || Math.abs(value) >= 1e6)) return value.toExponential(places);
  const text = value.toFixed(places);
  return Number(text) === 0 ? (0).toFixed(places) : text;
}

export function runName(run: RunSummary): string {
  return run.display_name?.trim() || run.name || run.run_id;
}

export function runLabel(run: RunSummary): string {
  const name = runName(run);
  return run.source_label ? `${name} · ${run.source_label}` : name;
}

export function legendLabelMap(runs: RunSummary[], trimCommonPrefix: boolean): Map<string, string> {
  const entries = runs.map((run) => ({
    run,
    name: runName(run),
    suffix: run.source_label ? ` · ${run.source_label}` : "",
    full: runLabel(run),
  }));
  const labels = new Map(entries.map(({ run, full }) => [run.run_id, full]));
  if (!trimCommonPrefix || entries.length < 2) return labels;
  let prefix = entries[0].name;
  for (const entry of entries.slice(1)) {
    let length = 0;
    while (length < prefix.length && length < entry.name.length && prefix[length] === entry.name[length]) length++;
    prefix = prefix.slice(0, length);
    if (prefix.length < 2) return labels;
  }
  const names = entries.map(({ name }) => name.slice(prefix.length).replace(/^[-_/.\s]+/, ""));
  const short = entries.map((entry, index) => `${names[index]}${entry.suffix}`);
  if (names.some((name) => !name) || new Set(short).size !== short.length) return labels;
  return new Map(entries.map((entry, index) => [entry.run.run_id, short[index]]));
}
