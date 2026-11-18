import { normalizeState, viewStateKey, type SavedView, type WorkspaceState } from "./workspace";

export const MAX_CONFIG_BYTES = 2 * 1024 * 1024;
export const MAX_VIEWS = 128;
export const MAX_PALETTES = 32;
export const STORAGE_ERROR = "Could not save in this browser. Changes may be lost when this page closes. Export JSON to keep a copy.";

export type ViewScope = "shared" | "local";

export interface ActiveView {
  scope: ViewScope;
  view_id: string;
  // viewStateKey of the View when it was opened or last saved, so temporary
  // edits can be told apart from the saved definition.
  base_key: string;
}

export interface SessionFile {
  version: 1;
  state: WorkspaceState;
  active_view: ActiveView | null;
}

interface LocalViewsFile {
  version: 1;
  views: SavedView[];
}

const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const textEncoder = new TextEncoder();

export const sessionKey = (workspaceId: string) => `cloverboard:session:${workspaceId}`;
export const localViewsKey = (workspaceId: string) => `cloverboard:views:local:${workspaceId}`;
// Removed keys: superseded by the session/local-views split.
export const legacyConfigKey = (workspaceId: string) => `cloverboard:local:${workspaceId}`;
export const newViewId = () => crypto.randomUUID().replaceAll("-", "");

function parseState(value: unknown): WorkspaceState {
  if (!record(value) || value.version !== 1) throw new Error("Invalid viewing settings.");
  return normalizeState(value);
}

function parseViews(value: unknown): SavedView[] {
  if (!Array.isArray(value) || value.length > MAX_VIEWS) throw new Error("Unsupported saved Views.");
  const views = value.map((item): SavedView => {
    if (!record(item) || typeof item.view_id !== "string" || !/^[0-9a-f]{32}$/.test(item.view_id) ||
        typeof item.name !== "string" || !item.name.trim() || item.name.trim().length > 80) throw new Error("Invalid saved View.");
    return { view_id: item.view_id, name: item.name.trim(), state: parseState(item.state) };
  });
  if (new Set(views.map(view => view.view_id)).size !== views.length || new Set(views.map(view => view.name.toLowerCase())).size !== views.length) {
    throw new Error("Duplicate View IDs or names.");
  }
  return views;
}

function parseActiveView(value: unknown): ActiveView | null {
  if (!record(value) || (value.scope !== "shared" && value.scope !== "local")) return null;
  if (typeof value.view_id !== "string" || typeof value.base_key !== "string") return null;
  return { scope: value.scope, view_id: value.view_id, base_key: value.base_key };
}

export function readSession(storage: Pick<Storage, "getItem">, key: string): SessionFile | null {
  const text = storage.getItem(key);
  if (text === null) return null;
  const value: unknown = JSON.parse(text);
  if (!record(value) || value.version !== 1) throw new Error("Invalid session settings.");
  return { version: 1, state: parseState(value.state), active_view: parseActiveView(value.active_view) };
}

// Auto refresh used to live in the saved state. Read that one-time value so
// existing browsers keep their choice when it moves to a preference key.
export function readStoredRefreshInterval(storage: Pick<Storage, "getItem">, key: string): unknown {
  const text = storage.getItem(key);
  if (text === null) return null;
  const value: unknown = JSON.parse(text);
  return record(value) && record(value.state) ? value.state.refresh_interval : null;
}

export function writeSession(storage: Pick<Storage, "setItem">, key: string, session: SessionFile): void {
  storage.setItem(key, JSON.stringify(session));
}

export function readLocalViews(storage: Pick<Storage, "getItem">, key: string): SavedView[] | null {
  const text = storage.getItem(key);
  if (text === null) return null;
  const value: unknown = JSON.parse(text);
  if (!record(value) || value.version !== 1) throw new Error("Invalid local Views.");
  return parseViews(value.views);
}

export function writeLocalViews(storage: Pick<Storage, "setItem">, key: string, views: SavedView[]): void {
  storage.setItem(key, JSON.stringify({ version: 1, views } satisfies LocalViewsFile));
}

// Configurations from before the split stored the session and both libraries in
// one object; only the personal parts survive (targets and palettes are workspace-managed).
export function parseLegacyConfig(value: unknown): { state: WorkspaceState; views: SavedView[]; active_view_id: string | null } {
  if (!record(value) || value.version !== 1 || !Array.isArray(value.views)) throw new Error("Unsupported legacy configuration.");
  const state = parseState(value.state);
  const active = record(value.state) && typeof value.state.active_view_id === "string" ? value.state.active_view_id : null;
  return { state, views: parseViews(value.views), active_view_id: active };
}

export function exportLocalFile(state: WorkspaceState, views: SavedView[]): string {
  return JSON.stringify({ format: "cloverboard-local-config", version: 1, state: normalizeState(state), views }, null, 2);
}

export function parseImportFile(text: string): { state: WorkspaceState; views: SavedView[] } {
  if (textEncoder.encode(text).length > MAX_CONFIG_BYTES) throw new Error("Choose a configuration file smaller than 2 MB.");
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error("This file is not valid JSON."); }
  if (!record(value) || (value.format !== "cloverboard-local-config" && value.format !== "cloverboard-viewing-config")) {
    throw new Error("Choose a CloverBoard settings JSON file.");
  }
  return { state: parseState(value.state), views: parseViews(value.views ?? []) };
}

function copyName(name: string, names: Set<string>): string {
  let next = name;
  for (let suffix = 2; names.has(next.toLowerCase()); suffix++) {
    const tail = ` (${suffix})`;
    next = name.slice(0, 80 - tail.length) + tail;
  }
  names.add(next.toLowerCase());
  return next;
}

export function saveLocalView(views: SavedView[], state: WorkspaceState, name: string, viewId?: string): { views: SavedView[]; view: SavedView; baseKey: string } {
  name = name.trim();
  if (!name || name.length > 80) throw new Error("Use a View name between 1 and 80 characters.");
  if (viewId && !views.some(view => view.view_id === viewId)) throw new Error("View not found.");
  if (!viewId && views.length >= MAX_VIEWS) throw new Error(`Limit reached: ${MAX_VIEWS} local Views.`);
  if (views.some(view => view.view_id !== viewId && view.name.toLowerCase() === name.toLowerCase())) throw new Error("A local View with this name already exists.");
  const view = { view_id: viewId ?? newViewId(), name, state: normalizeState(state) };
  return {
    view,
    baseKey: viewStateKey(view.state),
    views: viewId ? views.map(item => item.view_id === viewId ? view : item) : [...views, view],
  };
}

export function renameLocalView(views: SavedView[], viewId: string, name: string): SavedView[] {
  name = name.trim();
  if (!name || name.length > 80) throw new Error("Use a View name between 1 and 80 characters.");
  if (!views.some(view => view.view_id === viewId)) throw new Error("View not found.");
  if (views.some(view => view.view_id !== viewId && view.name.toLowerCase() === name.toLowerCase())) throw new Error("A local View with this name already exists.");
  return views.map(view => view.view_id === viewId ? { ...view, name } : view);
}

export function mergeLocalViews(current: SavedView[], incoming: SavedView[]): SavedView[] {
  if (current.length + incoming.length > MAX_VIEWS) throw new Error(`Import would exceed ${MAX_VIEWS} local Views.`);
  const names = new Set(current.map(view => view.name.toLowerCase()));
  return [...current, ...incoming.map(view => {
    const state = normalizeState(view.state);
    return { view_id: newViewId(), name: copyName(view.name, names), state };
  })];
}
