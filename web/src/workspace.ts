import { useCallback, useEffect, useRef, useState } from "react";
import { getJson } from "./api";
import { defaultColors, type Target, type ChartZoom, type ColorSettings, type TimeDisplay, type DeltaDisplay } from "./features";
import { stableValue } from "./comparison";
import type { ReadoutPlacement } from "./readout";
import { normalizePalette } from "./palettes";

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
  targets: Record<string, Target>;
  chart_zoom: Record<string, ChartZoom>;
  colors: ColorSettings;
}

export interface SavedView {
  view_id: string;
  name: string;
  state: WorkspaceState;
}

interface StateSave {
  state: WorkspaceState;
  saved_at: number;
}

interface WorkspaceSnapshot {
  workspace_id: string;
  writable: boolean;
  state: WorkspaceState | null;
  saved_at: number;
  views: SavedView[];
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
    baseline_id: null, targets: {}, chart_zoom: {}, colors: defaultColors(),
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
  if (typeof next.search_regex !== "boolean") next.search_regex = false;
  if (!["started_desc", "started_asc", "name_asc", "name_desc"].includes(next.run_sort)) next.run_sort = "started_desc";
  if (typeof next.metric_filter !== "string") next.metric_filter = "";
  if (!["normal", "compact"].includes(next.chart_density)) next.chart_density = "normal";
  if (typeof next.show_chart_legend !== "boolean") next.show_chart_legend = true;
  if (!["last_recorded_at", "started_at", "hidden"].includes(next.table_time)) next.table_time = "last_recorded_at";
  const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
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
  next.chart_warmup = Object.fromEntries(Object.entries(state.chart_warmup ?? {}).filter(([, step]) => step === null || Number.isSafeInteger(step)));
  if (next.y_warmup !== null && !Number.isSafeInteger(next.y_warmup)) next.y_warmup = null;
  if (![0, 5, 10, 30, 60].includes(next.refresh_interval)) next.refresh_interval = 5;
  if (!["charts", "table", "images"].includes(next.panel)) next.panel = "charts";
  if (typeof next.image_name !== "string") next.image_name = "";
  if (!Number.isSafeInteger(next.image_step)) next.image_step = null;
  next.targets = Object.fromEntries(Object.entries(state.targets ?? {}).filter(([, t]) => t && finite(t.value) && ["gte", "lte"].includes(t.direction)));
  next.chart_zoom = Object.fromEntries(Object.entries(state.chart_zoom ?? {}).filter(([, z]) => z && finite(z.x_min) && finite(z.x_max) && z.x_min < z.x_max && ((z.y_min == null && z.y_max == null) || (finite(z.y_min) && finite(z.y_max) && z.y_min < z.y_max))));
  next.baseline_id = typeof state.baseline_id === "string" ? state.baseline_id : null;
  if (!["linear", "log"].includes(next.y_scale)) next.y_scale = "linear";
  next.chart_scales = Object.fromEntries(Object.entries(state.chart_scales ?? {}).filter(([, value]) => ["linear", "log"].includes(value)));
  next.collapsed_groups = [...new Set((state.collapsed_groups ?? []).filter((value) => typeof value === "string"))];
  next.expanded_charts = [...new Set((Array.isArray(state.expanded_charts) ? state.expanded_charts : []).filter((value) => typeof value === "string"))];
  if (![50, 100, 200, 500].includes(next.image_timeline_samples)) next.image_timeline_samples = 200;
  if (![4, 8, 12, 24].includes(next.images_per_step)) next.images_per_step = 12;
  const c = state.colors;
  next.colors = { ...defaultColors(), ...c };
  next.colors.field = typeof c?.field === "string" ? c.field : "";
  if (!["random", "parameter", "continuous"].includes(next.colors.mode)) next.colors.mode = "random";
  next.colors.palette = normalizePalette(c?.palette);
  if (!Array.isArray(c?.domain) || c.domain.length !== 2 || !c.domain.every(finite) || c.domain[0] >= c.domain[1]) next.colors.domain = [0, 1];
  next.colors.overrides = Object.fromEntries(Object.entries(c?.overrides ?? {}).filter(([, v]) => typeof v === "string" && /^#[0-9a-f]{6}$/i.test(v)));
  next.colors.assignments = Object.fromEntries(Object.entries(c?.assignments ?? {}).filter(([, v]) => Number.isInteger(v) && v >= 0 && v <= 100000));
  next.colors.top_k = { ...defaultColors().top_k, ...c?.top_k };
  if (typeof next.colors.top_k.enabled !== "boolean") next.colors.top_k.enabled = false;
  if (typeof next.colors.top_k.metric !== "string") next.colors.top_k.metric = "";
  if (!["min", "max"].includes(next.colors.top_k.direction)) next.colors.top_k.direction = "max";
  if (!["latest", "best"].includes(next.colors.top_k.readout)) next.colors.top_k.readout = "latest";
  if (!Number.isInteger(next.colors.top_k.k) || next.colors.top_k.k < 1 || next.colors.top_k.k > 1000) next.colors.top_k.k = 3;
  return next;
}

function cachedState(key: string): StateSave | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? "null");
    const state = value?.state;
    const strings = (items: unknown): items is string[] =>
      Array.isArray(items) && items.every((item) => typeof item === "string");
    if (
      !Number.isFinite(value?.saved_at) || state?.version !== 1 ||
      !(state.selected_ids === null || strings(state.selected_ids)) ||
      typeof state.search !== "string" || typeof state.smoothing !== "number" ||
      !Number.isFinite(state.smoothing) || state.smoothing < 0 || state.smoothing > 0.95 ||
      !["charts", "table", "images"].includes(state.panel) ||
      typeof state.differences_only !== "boolean" ||
      !(state.metric_columns === null || strings(state.metric_columns)) ||
      !strings(state.hidden_params) || typeof state.sort_column !== "string" ||
      typeof state.sort_desc !== "boolean" ||
      !(state.active_view_id === null || typeof state.active_view_id === "string")
    ) return null;
    return { state: normalizeState(state), saved_at: value.saved_at };
  } catch {
    return null;
  }
}

function cacheState(key: string, payload: StateSave) {
  try {
    localStorage.setItem(key, JSON.stringify(payload));
  } catch {
    // Server persistence still works when browser storage is unavailable.
  }
}

export type WorkspaceUpdate = (patch: Partial<WorkspaceState> | ((state: WorkspaceState) => Partial<WorkspaceState>)) => void;

export function useWorkspace() {
  const [state, setState] = useState<WorkspaceState>(defaultWorkspaceState);
  const [views, setViews] = useState<SavedView[]>([]);
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [writable, setWritable] = useState(false);
  const [localOnly, setLocalOnly] = useState(false);
  const [loadTick, setLoadTick] = useState(0);
  const [workspaceId, setWorkspaceId] = useState("");
  const [linkNotice, setLinkNotice] = useState("");
  const identity = useRef("");
  const lastKey = useRef("");
  const pending = useRef<StateSave | null>(null);
  const savedAt = useRef(0);

  useEffect(() => {
    let cancelled = false;
    setLoadError(false);
    async function restore() {
      try {
        const snapshot = await getJson<WorkspaceSnapshot>("/api/workspace");
        if (cancelled) return;
        identity.current = `cloverboard:workspace:${snapshot.workspace_id}${snapshot.writable ? "" : ":readonly"}`;
        const cached = cachedState(identity.current);
        const useCache = cached !== null && (!snapshot.writable || cached.saved_at > snapshot.saved_at);
        const restored = normalizeState(useCache ? cached.state : snapshot.state ?? defaultWorkspaceState());
        if (!snapshot.views.some((view) => view.view_id === restored.active_view_id)) restored.active_view_id = null;
        let fromLink = false;
        try {
          const shared = readSharedLocation(window.location.href);
          if (shared.state) { Object.assign(restored, shared.state); fromLink = true; }
          else if (shared.viewId) {
            const view = snapshot.views.find((item) => item.view_id === shared.viewId);
            if (!view) throw new Error("The linked View is unavailable.");
            Object.assign(restored, normalizeState(view.state), { active_view_id: view.view_id }); fromLink = true;
          }
          if (fromLink && restored.active_view_id && !snapshot.views.some((item) => item.view_id === restored.active_view_id)) restored.active_view_id = null;
          if (fromLink) {
            const clean = new URL(window.location.href); clean.hash = ""; clean.searchParams.delete("view");
            window.history.replaceState(null, "", clean);
          }
        } catch (reason) { setLinkNotice(reason instanceof Error ? reason.message : "Unable to restore shared link."); }
        pending.current = fromLink ? { state: restored, saved_at: Math.max(Date.now(), snapshot.saved_at + 1) } : useCache ? cached : null;
        if (fromLink && pending.current) cacheState(identity.current, pending.current);
        savedAt.current = Math.max(cached?.saved_at ?? 0, snapshot.saved_at, pending.current?.saved_at ?? 0);
        lastKey.current = JSON.stringify(restored);
        setState(restored);
        setViews(snapshot.views.map((view) => ({ ...view, state: normalizeState(view.state) })));
        setWorkspaceId(snapshot.workspace_id);
        setWritable(snapshot.writable);
        setLocalOnly(!snapshot.writable);
        setReady(true);
      } catch {
        if (!cancelled) setLoadError(true);
      }
    }
    void restore();
    return () => { cancelled = true; };
  }, [loadTick]);

  useEffect(() => {
    if (!ready) return;
    const key = JSON.stringify(state);
    if (key !== lastKey.current) {
      savedAt.current = Math.max(Date.now(), savedAt.current + 1);
      pending.current = { state, saved_at: savedAt.current };
      lastKey.current = key;
      cacheState(identity.current, pending.current);
    }
    if (!pending.current || !writable) return;
    const payload = pending.current;
    const timer = window.setTimeout(() => {
      void getJson<WorkspaceSnapshot>("/api/workspace/state", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then(() => {
        if (pending.current === payload) pending.current = null;
        setLocalOnly(false);
      }).catch(() => setLocalOnly(true));
    }, 400);
    return () => window.clearTimeout(timer);
  }, [state, ready, writable]);

  useEffect(() => {
    function flush() {
      if (!pending.current || !writable) return;
      void fetch("/api/workspace/state", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pending.current),
        keepalive: true,
      }).catch(() => {});
    }
    window.addEventListener("pagehide", flush);
    return () => window.removeEventListener("pagehide", flush);
  }, [writable]);

  const update: WorkspaceUpdate = useCallback((patch) => {
    setState((previous) => ({ ...previous, ...(typeof patch === "function" ? patch(previous) : patch) }));
  }, []);

  async function saveView(name: string, viewId?: string) {
    if (!writable) throw new Error("Saved views are read-only. Your viewing changes stay in this browser.");
    const view = await getJson<SavedView>(
      viewId ? `/api/workspace/views/${encodeURIComponent(viewId)}` : "/api/workspace/views",
      {
        method: viewId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, state }),
      },
    );
    setViews((previous) => viewId
      ? previous.map((item) => item.view_id === viewId ? view : item)
      : [...previous, view]);
    update({ active_view_id: view.view_id });
  }

  async function renameView(viewId: string, name: string) {
    if (!writable) throw new Error("Saved views are read-only.");
    const view = await getJson<SavedView>(`/api/workspace/views/${encodeURIComponent(viewId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
    });
    setViews((previous) => previous.map((item) => item.view_id === viewId ? view : item));
  }

  async function deleteView(viewId: string) {
    if (!writable) throw new Error("Saved views are read-only.");
    await getJson<SavedView>(`/api/workspace/views/${encodeURIComponent(viewId)}`, { method: "DELETE" });
    setViews((previous) => previous.filter((view) => view.view_id !== viewId));
    setState((previous) => previous.active_view_id === viewId ? { ...previous, active_view_id: null } : previous);
  }

  return {
    state, update, views, ready, writable, loadError, localOnly, saveView, renameView, deleteView, workspaceId, linkNotice,
    openView: (view: SavedView) => setState({ ...normalizeState(view.state), active_view_id: view.view_id }),
    retry: () => setLoadTick((value) => value + 1),
  };
}

export function encodeSharedState(state: WorkspaceState): string {
  const bytes = new TextEncoder().encode(JSON.stringify({ version: 1, state: normalizeState(state) }));
  if (bytes.length > 12000) throw new Error("This state is too large for a URL. Save and link a named View instead.");
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
