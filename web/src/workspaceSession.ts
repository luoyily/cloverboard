import type { CustomPalette, PaletteInput } from "./palettes";
import {
  defaultWorkspaceState, isRefreshInterval, normalizeRefreshInterval, normalizeState, readSharedLocation,
  viewStateKey, REFRESH_INTERVAL_KEY, type RefreshInterval, type SavedView, type WorkspaceState, type WorkspaceUpdate,
} from "./workspace";
import { normalizeTargets, type TargetLibrary } from "./targets";
import {
  STORAGE_ERROR, legacyConfigKey, localViewsKey, mergeLocalViews, parseImportFile, parseLegacyConfig,
  readLocalViews, readSession, readStoredRefreshInterval, renameLocalView, saveLocalView, sessionKey,
  writeLocalViews, writeSession,
  type ActiveView, type SessionFile, type ViewScope,
} from "./viewingConfig";

interface TargetLibrarySave {
  targets: TargetLibrary;
  saved_at: number;
}

interface WorkspaceSnapshot {
  workspace_id: string;
  writable: boolean;
  state: WorkspaceState | null;
  views: SavedView[];
  target_library: TargetLibrarySave;
}

interface RemoteAssets { workspace: WorkspaceSnapshot; palettes: CustomPalette[] }
type Request = <T>(url: string, init?: RequestInit) => Promise<T>;
type StorageArea = Pick<Storage, "getItem" | "setItem"> & Partial<Pick<Storage, "removeItem">>;

export type { ActiveView, ViewScope };

const READ_ERROR = "Could not restore this browser's settings. Shared assets are available; local Views start empty.";

// Browser settings are the only home of viewing state. The server owns the
// shared assets: targets, palettes and named Views.
export class WorkspaceSession {
  private snapshot = {
    sharedViews: [] as SavedView[],
    localViews: [] as SavedView[],
    palettes: [] as CustomPalette[],
    targets: {} as TargetLibrary,
    state: defaultWorkspaceState(),
    refreshInterval: 5 as RefreshInterval,
    activeView: null as ActiveView | null,
    workspaceId: "",
    writable: false,
    ready: false,
    loadError: false,
    contextVersion: 0,
    storageError: "",
    targetsError: "",
    linkNotice: "",
  };
  private listeners = new Set<() => void>();
  private sessionStorageKey = "";
  private localViewsStorageKey = "";
  private targetsSavedAt = 0;
  private generation = 0;
  constructor(private request: Request, private storage: () => StorageArea) {}

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish(patch: Partial<typeof this.snapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener();
  }
  private replaceContext(patch: Partial<typeof this.snapshot> = {}) {
    this.publish({ ...patch, contextVersion: this.snapshot.contextVersion + 1 });
  }

  private async fetchRemote(): Promise<RemoteAssets> {
    const [workspace, palettes] = await Promise.all([
      this.request<WorkspaceSnapshot>("/api/workspace"), this.request<CustomPalette[]>("/api/workspace/palettes"),
    ]);
    return {
      workspace: {
        ...workspace,
        state: workspace.state ? normalizeState(workspace.state) : null,
        views: workspace.views.map(view => ({ ...view, state: normalizeState(view.state) })),
        target_library: { targets: normalizeTargets(workspace.target_library?.targets ?? {}), saved_at: workspace.target_library?.saved_at ?? 0 },
      },
      palettes,
    };
  }

  private saveSession(state: WorkspaceState, activeView: ActiveView | null): string {
    try {
      writeSession(this.storage(), this.sessionStorageKey, { version: 1, state, active_view: activeView });
      return "";
    } catch { return STORAGE_ERROR; }
  }
  private readRefreshPreference(): RefreshInterval | null {
    try {
      const stored = this.storage().getItem(REFRESH_INTERVAL_KEY);
      if (stored === null || stored.trim() === "") return null;
      const value = Number(stored);
      return isRefreshInterval(value) ? value : null;
    } catch { return null; }
  }
  private writeRefreshPreference(interval: RefreshInterval) {
    try { this.storage().setItem(REFRESH_INTERVAL_KEY, String(interval)); }
    catch { /* Optional browser preference. */ }
  }
  // Move the old state-scoped value once, but never inherit the shared workspace
  // default: a new browser starts with 5s unless this browser chose otherwise.
  private restoreRefreshInterval(sessionStorageKey: string, legacyStorageKey: string): RefreshInterval {
    const preference = this.readRefreshPreference();
    if (preference !== null) return preference;
    let legacy: unknown = null;
    try { legacy = readStoredRefreshInterval(this.storage(), sessionStorageKey); } catch { /* Ignore invalid storage. */ }
    if (legacy === null || legacy === undefined) {
      try { legacy = readStoredRefreshInterval(this.storage(), legacyStorageKey); } catch { /* Ignore invalid storage. */ }
    }
    const interval = normalizeRefreshInterval(legacy);
    this.writeRefreshPreference(interval);
    return interval;
  }
  private saveLocalViews(views: SavedView[]): string {
    try {
      writeLocalViews(this.storage(), this.localViewsStorageKey, views);
      return "";
    } catch { return STORAGE_ERROR; }
  }
  private reportStorage(error: string, required: boolean) {
    if (!error) return;
    if (this.snapshot.storageError !== error) this.publish({ storageError: error });
    if (required) throw new Error(error);
  }
  private writeSession(state: WorkspaceState, activeView: ActiveView | null, required = false) {
    const error = this.saveSession(state, activeView);
    this.reportStorage(error, required);
    if (!error && this.snapshot.storageError) this.publish({ storageError: "" });
  }
  private writeLocalViews(views: SavedView[], required = false) {
    const error = this.saveLocalViews(views);
    this.reportStorage(error, required);
    if (!error && this.snapshot.storageError) this.publish({ storageError: "" });
  }
  private requireWritable() {
    if (!this.snapshot.writable) throw new Error("This server is read-only.");
  }
  private mutate<T>(url: string, method: string, body?: unknown) {
    this.requireWritable();
    return this.request<T>(url, { method, headers: { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  }

  // One-time move from the old single-object browser config. Local copies of
  // shared Views are dropped; the server copy stays the single source.
  private migrateLegacy(workspaceId: string, sharedViews: SavedView[]) {
    const text = this.storage().getItem(legacyConfigKey(workspaceId));
    if (text === null) return null;
    const legacy = parseLegacyConfig(JSON.parse(text));
    const views = legacy.views.filter(view => !sharedViews.some(shared => shared.view_id === view.view_id));
    let activeView: ActiveView | null = null;
    if (legacy.active_view_id) {
      const shared = sharedViews.find(view => view.view_id === legacy.active_view_id);
      const local = views.find(view => view.view_id === legacy.active_view_id);
      const view = shared ?? local;
      if (view) activeView = {
        scope: shared ? "shared" : "local",
        view_id: view.view_id,
        base_key: viewStateKey(view.state),
      };
    }
    return { state: legacy.state, views, active_view: activeView };
  }
  private clearLegacy(workspaceId: string) {
    const key = legacyConfigKey(workspaceId);
    for (const name of [key, `${key}:mode`, `${key}:remote`]) {
      try { this.storage().removeItem?.(name); } catch { /* The new keys already hold the migrated data. */ }
    }
  }

  async load(address: string) {
    const generation = ++this.generation;
    this.publish({ loadError: false });
    try {
      const remote = await this.fetchRemote();
      if (generation !== this.generation) return false;
      this.sessionStorageKey = sessionKey(remote.workspace.workspace_id);
      this.localViewsStorageKey = localViewsKey(remote.workspace.workspace_id);
      this.targetsSavedAt = remote.workspace.target_library.saved_at;
      const refreshInterval = this.restoreRefreshInterval(this.sessionStorageKey, legacyConfigKey(remote.workspace.workspace_id));
      const sharedViews = remote.workspace.views;

      let localViews: SavedView[] = [];
      let state = normalizeState(remote.workspace.state ?? {});
      let activeView: ActiveView | null = null;
      let storageError = "";
      // Read the two keys independently so one corrupt entry does not discard
      // the other, then fall back to the old single-object config once.
      let storedViews: SavedView[] | null = null, storedSession: SessionFile | null = null;
      try { storedViews = readLocalViews(this.storage(), this.localViewsStorageKey); }
      catch { storageError = READ_ERROR; }
      try { storedSession = readSession(this.storage(), this.sessionStorageKey); }
      catch { storageError = READ_ERROR; }
      if (storedViews !== null) localViews = storedViews;
      if (storedSession) { state = storedSession.state; activeView = storedSession.active_view; }
      if (storedViews === null && storedSession === null) {
        try {
          const legacy = this.migrateLegacy(remote.workspace.workspace_id, sharedViews);
          if (legacy) { localViews = legacy.views; state = legacy.state; activeView = legacy.active_view; }
        } catch { storageError = READ_ERROR; }
      }

      let fromLink = false, linkNotice = "";
      try {
        const shared = readSharedLocation(address);
        if (shared.state) { state = shared.state; activeView = null; fromLink = true; }
        else if (shared.viewId) {
          const view = sharedViews.find(item => item.view_id === shared.viewId);
          if (!view) throw new Error("The linked shared View is unavailable.");
          state = normalizeState(view.state);
          activeView = { scope: "shared", view_id: view.view_id, base_key: viewStateKey(state) };
          fromLink = true;
        }
      } catch (reason) { linkNotice = reason instanceof Error ? reason.message : "Unable to restore shared link."; }

      if (activeView) {
        const view = (activeView.scope === "shared" ? sharedViews : localViews).find(item => item.view_id === activeView!.view_id);
        if (!view) activeView = null;
        // Follow remote edits only while the session is still identical to the
        // saved View; unsaved tweaks always win.
        else if (!fromLink && activeView.scope === "shared" && viewStateKey(state) === activeView.base_key) {
          const latest = viewStateKey(view.state);
          if (latest !== activeView.base_key) {
            state = normalizeState(view.state);
            activeView = { ...activeView, base_key: latest };
          }
        }
      }

      // Persist the restored session before announcing it so a read failure is
      // not cleared by the successful writes that follow.
      const writeError = this.saveSession(state, activeView) || this.saveLocalViews(localViews);
      if (!writeError) this.clearLegacy(remote.workspace.workspace_id);
      this.replaceContext({
        sharedViews, localViews, palettes: remote.palettes, targets: remote.workspace.target_library.targets,
        workspaceId: remote.workspace.workspace_id, writable: remote.workspace.writable,
        state, refreshInterval, activeView, ready: true, storageError: storageError || writeError, linkNotice,
      });
      return fromLink;
    } catch {
      if (generation === this.generation) this.publish({ loadError: true });
      return false;
    }
  }

  update: WorkspaceUpdate = patch => {
    if (!this.snapshot.ready) return;
    const state = normalizeState({ ...this.snapshot.state, ...(typeof patch === "function" ? patch(this.snapshot.state) : patch) });
    this.writeSession(state, this.snapshot.activeView);
    this.publish({ state });
  };

  setRefreshInterval = (value: number) => {
    const refreshInterval = normalizeRefreshInterval(value);
    this.writeRefreshPreference(refreshInterval);
    this.publish({ refreshInterval });
  };

  openView = (view: SavedView, scope: ViewScope) => {
    const target = (scope === "shared" ? this.snapshot.sharedViews : this.snapshot.localViews).find(item => item.view_id === view.view_id);
    if (!target) return;
    const state = normalizeState(target.state);
    const activeView = { scope, view_id: target.view_id, base_key: viewStateKey(state) };
    this.writeSession(state, activeView);
    this.replaceContext({ state, activeView });
  };

  saveView = async (name: string, scope: ViewScope, viewId?: string) => {
    name = name.trim();
    if (!name || name.length > 80) throw new Error("Use a View name between 1 and 80 characters.");
    if (scope === "shared") {
      const view = await this.mutate<SavedView>(
        `/api/workspace/views${viewId ? `/${encodeURIComponent(viewId)}` : ""}`, viewId ? "PUT" : "POST",
        { name, state: this.snapshot.state },
      );
      const saved = { ...view, state: normalizeState(view.state) };
      const activeView = { scope, view_id: saved.view_id, base_key: viewStateKey(saved.state) };
      this.publish({
        sharedViews: viewId ? this.snapshot.sharedViews.map(item => item.view_id === saved.view_id ? saved : item) : [...this.snapshot.sharedViews, saved],
        activeView,
      });
      this.writeSession(this.snapshot.state, activeView);
      return;
    }
    const result = saveLocalView(this.snapshot.localViews, this.snapshot.state, name, viewId);
    this.writeLocalViews(result.views, true);
    const activeView = { scope, view_id: result.view.view_id, base_key: result.baseKey };
    this.writeSession(this.snapshot.state, activeView);
    this.publish({ localViews: result.views, activeView });
  };

  renameView = async (scope: ViewScope, viewId: string, name: string) => {
    name = name.trim();
    if (scope === "shared") {
      const view = await this.mutate<SavedView>(`/api/workspace/views/${encodeURIComponent(viewId)}`, "PATCH", { name });
      const saved = { ...view, state: normalizeState(view.state) };
      this.publish({ sharedViews: this.snapshot.sharedViews.map(item => item.view_id === viewId ? saved : item) });
      return;
    }
    const views = renameLocalView(this.snapshot.localViews, viewId, name);
    this.writeLocalViews(views, true);
    this.publish({ localViews: views });
  };

  deleteView = async (scope: ViewScope, viewId: string) => {
    if (scope === "shared") {
      await this.mutate(`/api/workspace/views/${encodeURIComponent(viewId)}`, "DELETE");
      this.publish({ sharedViews: this.snapshot.sharedViews.filter(item => item.view_id !== viewId) });
    } else {
      const views = this.snapshot.localViews.filter(item => item.view_id !== viewId);
      this.writeLocalViews(views, true);
      this.publish({ localViews: views });
    }
    if (this.snapshot.activeView?.scope === scope && this.snapshot.activeView.view_id === viewId) {
      this.writeSession(this.snapshot.state, null);
      this.publish({ activeView: null });
    }
  };

  promoteView = async (viewId: string) => {
    const view = this.snapshot.localViews.find(item => item.view_id === viewId);
    if (!view) throw new Error("Local View not found.");
    const created = await this.mutate<SavedView>("/api/workspace/views", "POST", { name: view.name, state: view.state });
    const saved = { ...created, state: normalizeState(created.state) };
    const localViews = this.snapshot.localViews.filter(item => item.view_id !== viewId);
    const activeView = this.snapshot.activeView?.scope === "local" && this.snapshot.activeView.view_id === viewId
      ? { scope: "shared" as const, view_id: saved.view_id, base_key: viewStateKey(saved.state) }
      : this.snapshot.activeView;
    this.writeLocalViews(localViews, true);
    this.writeSession(this.snapshot.state, activeView);
    this.publish({ sharedViews: [...this.snapshot.sharedViews, saved], localViews, activeView });
  };

  // The target library is workspace-wide and saved independently of viewing state.
  saveTargets = async (targets: TargetLibrary) => {
    const library = normalizeTargets(targets);
    if (!this.snapshot.ready) return;
    try {
      this.requireWritable();
      const saved_at = Math.max(Date.now(), this.targetsSavedAt + 1);
      this.publish({ targets: library, targetsError: "" });
      const saved = await this.request<TargetLibrarySave>("/api/workspace/targets", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ targets: library, saved_at }),
      });
      this.targetsSavedAt = saved.saved_at;
      this.publish({ targets: saved.targets });
    } catch {
      this.publish({ targetsError: "Could not save targets to the workspace. They are kept in this page; edit a target to retry." });
    }
  };

  savePalette = async (input: PaletteInput, id?: string): Promise<CustomPalette> => {
    const palette = await this.mutate<CustomPalette>(`/api/workspace/palettes${id ? `/${encodeURIComponent(id)}` : ""}`, id ? "PUT" : "POST", input);
    this.publish({ palettes: id ? this.snapshot.palettes.map(item => item.id === id ? palette : item) : [...this.snapshot.palettes, palette] });
    return palette;
  };

  deletePalette = async (id: string) => {
    await this.mutate(`/api/workspace/palettes/${encodeURIComponent(id)}`, "DELETE");
    this.publish({ palettes: this.snapshot.palettes.filter(item => item.id !== id) });
  };

  importConfig = async (text: string) => {
    const incoming = parseImportFile(text);
    const localViews = mergeLocalViews(this.snapshot.localViews, incoming.views);
    const state = normalizeState(incoming.state);
    this.writeLocalViews(localViews, true);
    this.writeSession(state, null, true);
    this.replaceContext({ localViews, state, activeView: null });
  };

  retryStorage = () => {
    this.writeSession(this.snapshot.state, this.snapshot.activeView);
    this.writeLocalViews(this.snapshot.localViews);
  };

  stop = () => { this.generation++; };
}
