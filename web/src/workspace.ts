import { defaultColors, type ColorSettings, type TimeDisplay, type DeltaDisplay } from "./features";
import { normalizeTargets, type MetricTargets } from "./targets";
import { stableValue } from "./comparison";
import type { ReadoutPlacement } from "./readout";
import { normalizePalette } from "./palettes";

// Saved viewing settings. Pointer/readout state, chart zoom and browser
// preferences belong to the UI and must not enter Views, JSON or remote saves.
export interface WorkspaceState {
  version: 1;
  selected_ids: string[] | null;
  search: string;
  search_regex: boolean;
  run_sort: "started_desc" | "started_asc" | "name_asc" | "name_desc";
  metric_filter: string;
  chart_density: "normal" | "compact";
  show_chart_legend: boolean;
  table_time: "last_recorded_at" | "started_at" | "hidden";
  table_show_steps: boolean;
  table_delta: DeltaDisplay;
  tag_filter: string[];
  tag_filter_match: "any" | "all" | "none" | "empty";
  smoothing: number;
  tooltip_decimals: number;
  table_decimals: number;
  show_tags_column: boolean;
  tooltip_sort: "value" | "list";
  tooltip_placement: ReadoutPlacement;
  tooltip_time: TimeDisplay;
  tooltip_delta: DeltaDisplay;
  chart_warmup: Record<string, number | null>;
  y_warmup: number | null;
  y_scale: "linear" | "log";
  chart_scales: Record<string, "linear" | "log">;
  collapsed_groups: string[];
  expanded_charts: string[];
  refresh_interval: number;
  panel: "charts" | "table" | "images";
  media_type: "images" | "audio" | "text";
  text_name: string;
  text_step: number | null;
  audio_name: string;
  audio_step: number | null;
  image_name: string;
  image_step: number | null;
  image_timeline_samples: number;
  images_per_step: number;
  differences_only: boolean;
  metric_columns: string[] | null;
  hidden_params: string[];
  sort_column: string;
  sort_desc: boolean;
  active_view_id: string | null;
  baseline_id: string | null;
  targets: Record<string, MetricTargets>;
  colors: ColorSettings;
}

export interface SavedView {
  view_id: string;
  name: string;
  state: WorkspaceState;
}

export function defaultWorkspaceState(): WorkspaceState {
  return {
    version: 1,
    selected_ids: null,
    search: "", search_regex: false, run_sort: "started_desc", metric_filter: "", chart_density: "normal", table_time: "last_recorded_at",
    tag_filter: [], tag_filter_match: "any",
    show_chart_legend: true,
    smoothing: 0.6,
    tooltip_decimals: 3,
    table_decimals: 4, show_tags_column: false,
    table_show_steps: true, table_delta: "both",
    tooltip_sort: "value",
    tooltip_placement: "vertical",
    tooltip_time: "hidden",
    tooltip_delta: "both",
    chart_warmup: {}, y_warmup: null, y_scale: "linear", chart_scales: {}, collapsed_groups: [], expanded_charts: [],
    refresh_interval: 5,
    panel: "charts",
    media_type: "images", audio_name: "", audio_step: null, text_name: "", text_step: null,
    image_name: "", image_step: null, image_timeline_samples: 200, images_per_step: 12,
    differences_only: true,
    metric_columns: null,
    hidden_params: [],
    sort_column: "run",
    sort_desc: false,
    active_view_id: null,
    baseline_id: null, targets: {}, colors: defaultColors(),
  };
}

export function viewStateKey(state: WorkspaceState): string {
  const current = normalizeState(state);
  return stableValue({
    ...current,
    selected_ids: current.selected_ids ? [...current.selected_ids].sort() : null,
    hidden_params: [...current.hidden_params].sort(), collapsed_groups: [...current.collapsed_groups].sort(),
    tag_filter: [...current.tag_filter].sort(),
    expanded_charts: [...current.expanded_charts].sort(),
    active_view_id: null,
  });
}

export function normalizeState(state: Partial<WorkspaceState>): WorkspaceState {
  const defaults = defaultWorkspaceState();
  const next = { ...defaults, ...Object.fromEntries(Object.entries(state).filter(([key]) => Object.hasOwn(defaults, key))) };
  const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  const entries = <T,>(value: Record<string, T> | null | undefined): [string, T][] => value !== null && typeof value === "object" && !Array.isArray(value) ? Object.entries(value) : [];
  next.version = 1;
  next.selected_ids = state.selected_ids == null ? null : strings(state.selected_ids);
  next.metric_columns = state.metric_columns == null ? null : strings(state.metric_columns);
  next.hidden_params = strings(state.hidden_params);
  if (typeof next.search !== "string") next.search = "";
  if (typeof next.differences_only !== "boolean") next.differences_only = true;
  if (typeof next.sort_column !== "string") next.sort_column = "run";
  if (typeof next.sort_desc !== "boolean") next.sort_desc = false;
  next.active_view_id = typeof state.active_view_id === "string" ? state.active_view_id : null;
  if (typeof next.search_regex !== "boolean") next.search_regex = false;
  if (!["started_desc", "started_asc", "name_asc", "name_desc"].includes(next.run_sort)) next.run_sort = "started_desc";
  if (typeof next.metric_filter !== "string") next.metric_filter = "";
  if (!["normal", "compact"].includes(next.chart_density)) next.chart_density = "normal";
  if (typeof next.show_chart_legend !== "boolean") next.show_chart_legend = true;
  if (!["last_recorded_at", "started_at", "hidden"].includes(next.table_time)) next.table_time = "last_recorded_at";
  const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
  if (!finite(next.smoothing) || next.smoothing < 0 || next.smoothing > .95) next.smoothing = .6;
  if (!Number.isInteger(next.tooltip_decimals) || next.tooltip_decimals < 0 || next.tooltip_decimals > 8) next.tooltip_decimals = 3;
  if (!Number.isInteger(next.table_decimals) || next.table_decimals < 0 || next.table_decimals > 8) next.table_decimals = 4;
  next.tag_filter = [...new Set((Array.isArray(state.tag_filter) ? state.tag_filter : []).filter((v) => typeof v === "string" && v.length > 0 && v.length <= 80))].slice(0, 64);
  if (!["any", "all", "none", "empty"].includes(next.tag_filter_match)) next.tag_filter_match = "any";
  if (typeof next.show_tags_column !== "boolean") next.show_tags_column = false;
  if (typeof next.table_show_steps !== "boolean") next.table_show_steps = true;
  if (!["baseline", "target", "both", "hidden"].includes(next.table_delta)) next.table_delta = "both";
  if (!["images", "audio", "text"].includes(next.media_type)) next.media_type = "images";
  if (typeof next.text_name !== "string") next.text_name = "";
  if (!Number.isSafeInteger(next.text_step)) next.text_step = null;
  if (typeof next.audio_name !== "string") next.audio_name = "";
  if (!Number.isSafeInteger(next.audio_step)) next.audio_step = null;
  if (!["value", "list"].includes(next.tooltip_sort)) next.tooltip_sort = "value";
  if (!["vertical", "horizontal"].includes(next.tooltip_placement)) next.tooltip_placement = "vertical";
  if (!["hidden", "elapsed", "wall"].includes(next.tooltip_time)) next.tooltip_time = "hidden";
  if (!["baseline", "target", "both", "hidden"].includes(next.tooltip_delta)) next.tooltip_delta = "both";
  next.chart_warmup = Object.fromEntries(entries(state.chart_warmup).filter(([, step]) => step === null || Number.isSafeInteger(step)));
  if (next.y_warmup !== null && !Number.isSafeInteger(next.y_warmup)) next.y_warmup = null;
  if (![0, 5, 10, 30, 60].includes(next.refresh_interval)) next.refresh_interval = 5;
  if (!["charts", "table", "images"].includes(next.panel)) next.panel = "charts";
  if (typeof next.image_name !== "string") next.image_name = "";
  if (!Number.isSafeInteger(next.image_step)) next.image_step = null;
  next.targets = normalizeTargets(Object.fromEntries(entries(state.targets)));
  next.baseline_id = typeof state.baseline_id === "string" ? state.baseline_id : null;
  if (!["linear", "log"].includes(next.y_scale)) next.y_scale = "linear";
  next.chart_scales = Object.fromEntries(entries(state.chart_scales).filter(([, value]) => ["linear", "log"].includes(value)));
  next.collapsed_groups = [...new Set(strings(state.collapsed_groups))];
  next.expanded_charts = [...new Set((Array.isArray(state.expanded_charts) ? state.expanded_charts : []).filter((value) => typeof value === "string"))];
  if (![50, 100, 200, 500].includes(next.image_timeline_samples)) next.image_timeline_samples = 200;
  if (![4, 8, 12, 24].includes(next.images_per_step)) next.images_per_step = 12;
  const c = state.colors && typeof state.colors === "object" && !Array.isArray(state.colors) ? state.colors : undefined;
  next.colors = { ...defaultColors(), ...Object.fromEntries(Object.entries(c ?? {}).filter(([key]) => Object.hasOwn(defaultColors(), key))) };
  next.colors.field = typeof c?.field === "string" ? c.field : "";
  if (!["random", "parameter", "continuous"].includes(next.colors.mode)) next.colors.mode = "random";
  next.colors.palette = normalizePalette(c?.palette);
  if (!Array.isArray(c?.domain) || c.domain.length !== 2 || !c.domain.every(finite) || c.domain[0] >= c.domain[1]) next.colors.domain = [0, 1];
  next.colors.runs = Object.fromEntries(entries(c?.runs).filter(([, v]) => v && typeof v.color === "string" && /^#[0-9a-f]{6}$/i.test(v.color) && typeof v.fixed === "boolean").map(([key, v]) => [key, { color: v.color.toLowerCase(), fixed: v.fixed }]));
  next.colors.top_k = { ...defaultColors().top_k, ...c?.top_k };
  if (typeof next.colors.top_k.enabled !== "boolean") next.colors.top_k.enabled = false;
  if (typeof next.colors.top_k.metric !== "string") next.colors.top_k.metric = "";
  if (!["min", "max"].includes(next.colors.top_k.direction)) next.colors.top_k.direction = "max";
  if (!["latest", "best"].includes(next.colors.top_k.readout)) next.colors.top_k.readout = "latest";
  if (!Number.isInteger(next.colors.top_k.k) || next.colors.top_k.k < 1 || next.colors.top_k.k > 1000) next.colors.top_k.k = 3;
  return next;
}

export type WorkspaceUpdate = (patch: Partial<WorkspaceState> | ((state: WorkspaceState) => Partial<WorkspaceState>)) => void;

export function encodeSharedState(state: WorkspaceState): string {
  const bytes = new TextEncoder().encode(JSON.stringify({ version: 1, state: normalizeState(state) }));
  if (bytes.length > 12000) throw new Error("This view is too large for a link. Export your personal configuration as JSON instead.");
  return btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join("")).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

export function readSharedLocation(address: string): { state?: WorkspaceState; viewId?: string } {
  const url = new URL(address), hash = new URLSearchParams(url.hash.slice(1)), encoded = hash.get("state");
  if (!encoded) return { viewId: url.searchParams.get("view") ?? undefined };
  try {
    if (encoded.length > 16000 || !/^[A-Za-z0-9_-]+$/.test(encoded)) throw new Error();
    const binary = atob(encoded.replaceAll("-", "+").replaceAll("_", "/"));
    const data = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(binary, (char) => char.charCodeAt(0))));
    const value = data.state;
    const strings = (items: unknown): items is string[] => Array.isArray(items) && items.every((item) => typeof item === "string");
    if (data.version !== 1 || value?.version !== 1 || !(value.selected_ids === null || strings(value.selected_ids)) || typeof value.search !== "string" || !Number.isFinite(value.smoothing) || value.smoothing < 0 || value.smoothing > .95 || !["charts", "table", "images"].includes(value.panel) || typeof value.differences_only !== "boolean" || !(value.metric_columns === null || strings(value.metric_columns)) || !strings(value.hidden_params) || typeof value.sort_column !== "string" || typeof value.sort_desc !== "boolean") throw new Error();
    return { state: normalizeState(value) };
  } catch { throw new Error("This shared link contains invalid or unsupported viewing settings."); }
}
