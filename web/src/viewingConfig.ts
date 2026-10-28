import { stableValue } from "./comparison";
import { normalizePalette, paletteError, type CustomPalette, type PaletteInput } from "./palettes";
import { defaultWorkspaceState, normalizeState, type SavedView, type WorkspaceState } from "./workspace";

export const MAX_CONFIG_BYTES = 2 * 1024 * 1024;
export const MAX_VIEWS = 128;
export const MAX_PALETTES = 32;
export const STORAGE_ERROR = "Could not save in this browser. Changes may be lost when this page closes. Export JSON to keep a copy.";
export interface ViewingConfig {
  version: 1;
  state: WorkspaceState;
  views: SavedView[];
  palettes: CustomPalette[];
}

const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
export const localConfigKey = (workspaceId: string) => `cloverboard:local:${workspaceId}`;
export const newViewId = () => crypto.randomUUID().replaceAll("-", "");
export const newPaletteId = () => `custom:${crypto.randomUUID().replaceAll("-", "")}`;

export function initialConfig(state: WorkspaceState | null, views: SavedView[] = [], palettes: CustomPalette[] = []): ViewingConfig {
  return { version: 1, state: normalizeState(state ?? defaultWorkspaceState()), views: views.map(view => ({ ...view, state: normalizeState(view.state) })), palettes };
}

function parseState(value: unknown): WorkspaceState {
  if (!record(value) || value.version !== 1) throw new Error("Invalid viewing settings.");
  try {
    const normalized = normalizeState(value);
    if (stableValue(normalized) !== stableValue(value)) throw new Error();
    return normalized;
  } catch { throw new Error("Invalid viewing settings."); }
}

export function parseConfig(value: unknown): ViewingConfig {
  if (!record(value) || value.version !== 1 || !Array.isArray(value.views) || !Array.isArray(value.palettes)) throw new Error("Unsupported viewing configuration format.");
  if (value.views.length > MAX_VIEWS || value.palettes.length > MAX_PALETTES) throw new Error("Configuration library limit exceeded.");
  const views = value.views.map((item): SavedView => {
    if (!record(item) || typeof item.view_id !== "string" || !/^[0-9a-f]{32}$/.test(item.view_id) || typeof item.name !== "string" || !item.name.trim() || item.name.trim().length > 80) throw new Error("Invalid local View.");
    return { view_id: item.view_id, name: item.name.trim(), state: { ...parseState(item.state), active_view_id: null } };
  });
  const palettes = value.palettes.map((item): CustomPalette => {
    if (!record(item)) throw new Error("Invalid local palette.");
    const palette = normalizePalette({ ...item, kind: "custom" });
    if (palette.kind !== "custom") throw new Error("Invalid local palette.");
    return { id: palette.id, name: palette.name, colors: palette.colors };
  });
  for (const items of [views.map(item => [item.view_id, item.name]), palettes.map(item => [item.id, item.name])]) {
    if (new Set(items.map(item => item[0])).size !== items.length || new Set(items.map(item => item[1].toLowerCase())).size !== items.length) throw new Error("Duplicate IDs or names in viewing configuration.");
  }
  return { version: 1, state: parseState(value.state), views, palettes };
}

export function readLocalConfig(storage: Pick<Storage, "getItem">, key: string): ViewingConfig | null {
  const text = storage.getItem(key);
  return text === null ? null : parseConfig(JSON.parse(text));
}

export function writeLocalConfig(storage: Pick<Storage, "setItem">, key: string, config: ViewingConfig): void {
  storage.setItem(key, JSON.stringify(config));
}

export function exportConfig(config: ViewingConfig): string {
  return JSON.stringify({ format: "cloverboard-viewing-config", ...config }, null, 2);
}

export function parseConfigFile(text: string): ViewingConfig {
  if (new TextEncoder().encode(text).length > MAX_CONFIG_BYTES) throw new Error("Choose a configuration file smaller than 2 MB.");
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error("This file is not valid JSON."); }
  if (!record(value) || value.format !== "cloverboard-viewing-config") throw new Error("Choose a CloverBoard viewing configuration JSON file.");
  return parseConfig(value);
}

function copyName(name: string, names: Set<string>, limit: number): string {
  let next = name;
  for (let suffix = 2; names.has(next.toLowerCase()); suffix++) {
    const tail = ` (${suffix})`;
    next = name.slice(0, limit - tail.length) + tail;
  }
  names.add(next.toLowerCase());
  return next;
}

export function mergeConfig(current: ViewingConfig, incoming: ViewingConfig): ViewingConfig {
  if (current.views.length + incoming.views.length > MAX_VIEWS || current.palettes.length + incoming.palettes.length > MAX_PALETTES) throw new Error(`Import would exceed ${MAX_VIEWS} local Views or ${MAX_PALETTES} palettes.`);
  const paletteIds = new Map<string, string>(), viewIds = new Map<string, string>();
  const paletteNames = new Set(current.palettes.map(item => item.name.toLowerCase()));
  const viewNames = new Set(current.views.map(item => item.name.toLowerCase()));
  const palettes = incoming.palettes.map(item => {
    const id = newPaletteId(); paletteIds.set(item.id, id);
    return { ...item, id, name: copyName(item.name, paletteNames, 60) };
  });
  for (const view of incoming.views) viewIds.set(view.view_id, newViewId());
  function remap(state: WorkspaceState): WorkspaceState {
    const next = normalizeState(state), palette = next.colors.palette;
    if (palette.kind === "custom") {
      if (!paletteIds.has(palette.id)) paletteIds.set(palette.id, newPaletteId());
      const id = paletteIds.get(palette.id)!;
      next.colors = { ...next.colors, palette: { ...palette, id, name: palettes.find(item => item.id === id)?.name ?? palette.name } };
    }
    return { ...next, active_view_id: state.active_view_id ? viewIds.get(state.active_view_id) ?? null : null };
  }
  const views = incoming.views.map(item => ({ view_id: viewIds.get(item.view_id)!, name: copyName(item.name, viewNames, 80), state: { ...remap(item.state), active_view_id: null } }));
  return { ...current, state: remap(incoming.state), views: [...current.views, ...views], palettes: [...current.palettes, ...palettes] };
}

export function restoreRemoteConfig(current: ViewingConfig, state: WorkspaceState | null): ViewingConfig {
  const restored = normalizeState(state ?? defaultWorkspaceState());
  if (!current.views.some(view => view.view_id === restored.active_view_id)) restored.active_view_id = null;
  return { ...current, state: restored };
}

export function saveLocalView(config: ViewingConfig, name: string, viewId?: string): ViewingConfig {
  name = name.trim();
  if (!name || name.length > 80) throw new Error("Use a View name between 1 and 80 characters.");
  if (viewId && !config.views.some(item => item.view_id === viewId)) throw new Error("View not found.");
  if (!viewId && config.views.length >= MAX_VIEWS) throw new Error(`Limit reached: ${MAX_VIEWS} local Views.`);
  if (config.views.some(item => item.view_id !== viewId && item.name.toLowerCase() === name.toLowerCase())) throw new Error("A local View with this name already exists.");
  const view = { view_id: viewId ?? newViewId(), name, state: { ...normalizeState(config.state), active_view_id: null } };
  return { ...config, state: { ...config.state, active_view_id: view.view_id }, views: viewId ? config.views.map(item => item.view_id === viewId ? view : item) : [...config.views, view] };
}

export function saveLocalPalette(config: ViewingConfig, input: PaletteInput, id?: string): { config: ViewingConfig; palette: CustomPalette } {
  const error = paletteError(input.name, input.colors);
  if (error) throw new Error(error);
  if (id && !config.palettes.some(item => item.id === id)) throw new Error("Palette not found.");
  if (!id && config.palettes.length >= MAX_PALETTES) throw new Error(`Limit reached: ${MAX_PALETTES} local palettes.`);
  if (config.palettes.some(item => item.id !== id && item.name.toLowerCase() === input.name.trim().toLowerCase())) throw new Error("A local palette with this name already exists.");
  const palette = { id: id ?? newPaletteId(), name: input.name.trim(), colors: input.colors.map(color => color.toLowerCase()) };
  const palettes = id ? config.palettes.map(item => item.id === id ? palette : item) : [...config.palettes, palette];
  return { palette, config: { ...config, palettes } };
}
