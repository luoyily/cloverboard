import type { CustomPalette, PaletteInput } from "./palettes";
import { normalizeState, readSharedLocation, type SavedView, type WorkspaceState, type WorkspaceUpdate } from "./workspace";
import {
  initialConfig, mergeConfig, parseConfigFile, localConfigKey, readLocalConfig, restoreRemoteConfig,
  saveLocalPalette, saveLocalView, STORAGE_ERROR, writeLocalConfig, type ViewingConfig,
} from "./viewingConfig";

export type SettingsMode = "remote" | "local";
interface WorkspaceSnapshot {
  workspace_id: string;
  writable: boolean;
  saved_at: number;
  state: WorkspaceState | null;
  views: SavedView[];
}
interface RemoteSettings { workspace: WorkspaceSnapshot; palettes: CustomPalette[] }
interface StateSave { state: WorkspaceState; saved_at: number }
type Request = <T>(url: string, init?: RequestInit) => Promise<T>;
const initial = initialConfig(null);

// The remote autosave queue is independent of local browser settings. Switching
// modes or importing a file must never enqueue the local state for upload.
export class WorkspaceSession {
  private snapshot = {
    local: initial, remote: null as RemoteSettings | null, mode: "remote" as SettingsMode,
    state: initial.state, contextVersion: 0, ready: false, loadError: false,
    storageError: "", stateError: "", linkNotice: "",
  };
  private listeners = new Set<() => void>();
  private key = "";
  private generation = 0;
  private pending: StateSave | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private stateTask: Promise<void> | null = null;
  constructor(private request: Request, private storage: () => Pick<Storage, "getItem" | "setItem">) {}

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish(patch: Partial<typeof this.snapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener();
  }
  private replaceContext(patch: Partial<typeof this.snapshot> = {}) {
    this.publish({ ...patch, contextVersion: this.snapshot.contextVersion + 1 });
  }
  private async fetchRemote(): Promise<RemoteSettings> {
    const [workspace, palettes] = await Promise.all([
      this.request<WorkspaceSnapshot>("/api/workspace"), this.request<CustomPalette[]>("/api/workspace/palettes"),
    ]);
    return { workspace: { ...workspace, state: workspace.state ? normalizeState(workspace.state) : null, views: workspace.views.map(view => ({ ...view, state: normalizeState(view.state) })) }, palettes };
  }
  private write(key: string, value: unknown) {
    try { this.storage().setItem(key, JSON.stringify(value)); this.publish({ storageError: "" }); }
    catch {
      this.publish({ storageError: STORAGE_ERROR });
    }
  }
  private storeLocal(local: ViewingConfig, required = false) {
    try { writeLocalConfig(this.storage(), this.key, local); this.publish({ storageError: "" }); }
    catch { this.publish({ storageError: STORAGE_ERROR }); if (required) throw new Error(STORAGE_ERROR); }
    this.publish({ local, ...(this.snapshot.mode === "local" ? { state: local.state } : {}) });
  }
  private activateLocal(local: ViewingConfig, required = false) {
    this.storeLocal(local, required);
    if (this.snapshot.mode !== "local") this.write(`${this.key}:mode`, "local");
    this.replaceContext({ mode: "local", state: local.state });
  }
  getConfig = (): ViewingConfig => this.snapshot.mode === "local" ? this.snapshot.local : {
    version: 1, state: this.snapshot.state, views: this.snapshot.remote?.workspace.views ?? [], palettes: this.snapshot.remote?.palettes ?? [],
  };
  private requireRemote() {
    if (!this.snapshot.remote?.workspace.writable || this.snapshot.mode !== "remote") throw new Error("Switch to Remote workspace to edit remote settings.");
    return this.snapshot.remote;
  }
  private mutate<T>(url: string, method: string, body?: unknown) {
    this.requireRemote();
    return this.request<T>(url, { method, headers: { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  }

  async load(address: string) {
    const generation = ++this.generation;
    this.publish({ loadError: false });
    try {
      const remote = await this.fetchRemote();
      if (generation !== this.generation) return false;
      this.key = localConfigKey(remote.workspace.workspace_id);
      let local = initialConfig(remote.workspace.state, remote.workspace.views, remote.palettes), mode: SettingsMode = remote.workspace.writable ? "remote" : "local";
      let cacheError = "", cachedSave: StateSave | null = null;
      try {
        local = readLocalConfig(this.storage(), this.key) ?? local;
        if (remote.workspace.writable) {
          if (this.storage().getItem(`${this.key}:mode`) === '"local"') mode = "local";
          const cached = JSON.parse(this.storage().getItem(`${this.key}:remote`) ?? "null");
          if (cached?.state?.version === 1 && Number.isSafeInteger(cached.saved_at) && cached.saved_at > remote.workspace.saved_at) cachedSave = { state: normalizeState(cached.state), saved_at: cached.saved_at };
        }
      } catch { cacheError = "Could not restore browser settings. Remote settings are available; import a JSON backup to recover local settings."; }
      if (cachedSave) remote.workspace = { ...remote.workspace, ...cachedSave };
      const available = mode === "local" ? local.views : remote.workspace.views;
      let state = normalizeState(mode === "local" ? local.state : remote.workspace.state ?? {});
      if (!available.some(view => view.view_id === state.active_view_id)) state.active_view_id = null;
      let fromLink = false, linkNotice = "";
      try {
        const shared = readSharedLocation(address);
        if (shared.state) { state = shared.state; fromLink = true; }
        else if (shared.viewId) {
          const view = remote.workspace.views.find(item => item.view_id === shared.viewId);
          if (!view) throw new Error("The linked remote View is unavailable.");
          state = { ...normalizeState(view.state), active_view_id: view.view_id }; fromLink = true;
        }
        if (!available.some(view => view.view_id === state.active_view_id)) state.active_view_id = null;
      } catch (reason) { linkNotice = reason instanceof Error ? reason.message : "Unable to restore shared link."; }
      this.pending = cachedSave;
      this.replaceContext({ remote, local, mode, state, ready: true, storageError: cacheError, linkNotice });
      if (mode === "local") this.storeLocal({ ...local, state });
      else if (fromLink) this.saveRemoteState(state);
      if (cacheError) this.publish({ storageError: cacheError });
      if (this.pending) this.scheduleSave();
      return fromLink;
    } catch { if (generation === this.generation) this.publish({ loadError: true }); return false; }
  }

  private scheduleSave() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { void this.flushRemote(); }, 400);
  }
  private saveRemoteState(state: WorkspaceState) {
    const remote = this.requireRemote();
    if (!remote.workspace.views.some(view => view.view_id === state.active_view_id)) state = { ...state, active_view_id: null };
    const payload = { state, saved_at: Math.max(Date.now(), remote.workspace.saved_at + 1, (this.pending?.saved_at ?? 0) + 1) };
    this.pending = payload;
    this.write(`${this.key}:remote`, payload);
    this.publish({ remote: { ...remote, workspace: { ...remote.workspace, ...payload } }, state });
    this.scheduleSave();
  }
  flushRemote = async (keepalive = false): Promise<void> => {
    clearTimeout(this.timer);
    if (this.stateTask) { await this.stateTask; if (this.pending && !this.snapshot.stateError) await this.flushRemote(keepalive); return; }
    const payload = this.pending;
    if (!payload || !this.snapshot.remote?.workspace.writable) return;
    this.stateTask = (async () => {
      try {
        await this.request("/api/workspace/state", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), keepalive });
        if (this.pending === payload) this.pending = null;
        this.publish({ stateError: "" });
        if (this.pending) this.scheduleSave();
      } catch { this.publish({ stateError: "Could not save viewing settings to the remote workspace. Retry to upload them. If browser storage is available, pending changes will also be recovered on reload." }); }
    })();
    await this.stateTask;
    this.stateTask = null;
    if (keepalive && this.pending !== payload && this.pending && !this.snapshot.stateError) await this.flushRemote(true);
  };

  update: WorkspaceUpdate = patch => {
    if (!this.snapshot.ready) return;
    const state = normalizeState({ ...this.snapshot.state, ...(typeof patch === "function" ? patch(this.snapshot.state) : patch) });
    if (this.snapshot.mode === "local") this.storeLocal({ ...this.snapshot.local, state });
    else this.saveRemoteState(state);
  };
  setMode = async (mode: SettingsMode) => {
    if (mode === this.snapshot.mode) return;
    if (mode === "local") { this.activateLocal(this.snapshot.local, true); return; }
    if (!this.snapshot.remote?.workspace.writable) throw new Error("This server is read-only.");
    await this.retryRemote();
    if (this.pending) throw new Error(this.snapshot.stateError || "Remote settings are still being saved.");
    const remote = await this.fetchRemote();
    if (!remote.workspace.writable) throw new Error("This server is read-only.");
    this.write(`${this.key}:mode`, "remote");
    this.replaceContext({ remote, mode, state: normalizeState(remote.workspace.state ?? {}) });
  };
  openView = (view: SavedView) => {
    if (!this.getConfig().views.some(item => item.view_id === view.view_id)) return;
    this.update({ ...normalizeState(view.state), active_view_id: view.view_id });
    this.replaceContext();
  };
  saveView = async (name: string, viewId?: string) => {
    if (this.snapshot.mode === "local") { this.storeLocal(saveLocalView(this.snapshot.local, name, viewId), true); return; }
    const view = await this.mutate<SavedView>(`/api/workspace/views${viewId ? `/${encodeURIComponent(viewId)}` : ""}`, viewId ? "PUT" : "POST", { name, state: this.snapshot.state });
    const remote = this.snapshot.remote!;
    this.publish({ remote: { ...remote, workspace: { ...remote.workspace, views: viewId ? remote.workspace.views.map(item => item.view_id === viewId ? view : item) : [...remote.workspace.views, view] } } });
    if (this.snapshot.mode === "remote") this.update({ active_view_id: view.view_id });
  };
  renameView = async (viewId: string, name: string) => {
    const local = this.snapshot.local, view = local.views.find(item => item.view_id === viewId);
    if (this.snapshot.mode === "local") {
      if (!view) throw new Error("View not found in this browser.");
      const renamed = saveLocalView({ ...local, state: view.state }, name, viewId);
      this.storeLocal({ ...renamed, state: local.state }, true); return;
    }
    const renamed = await this.mutate<SavedView>(`/api/workspace/views/${encodeURIComponent(viewId)}`, "PATCH", { name });
    const remote = this.snapshot.remote!;
    this.publish({ remote: { ...remote, workspace: { ...remote.workspace, views: remote.workspace.views.map(item => item.view_id === viewId ? renamed : item) } } });
  };
  deleteView = async (viewId: string) => {
    const local = this.snapshot.local;
    if (this.snapshot.mode === "local") {
      this.storeLocal({ ...local, views: local.views.filter(item => item.view_id !== viewId), state: { ...local.state, active_view_id: local.state.active_view_id === viewId ? null : local.state.active_view_id } }, true); return;
    }
    await this.mutate(`/api/workspace/views/${encodeURIComponent(viewId)}`, "DELETE");
    const remote = this.snapshot.remote!;
    this.publish({ remote: { ...remote, workspace: { ...remote.workspace, views: remote.workspace.views.filter(item => item.view_id !== viewId) } } });
    if (this.snapshot.mode === "remote" && this.snapshot.state.active_view_id === viewId) this.update({ active_view_id: null });
  };
  savePalette = async (input: PaletteInput, id?: string): Promise<CustomPalette> => {
    if (this.snapshot.mode === "local") {
      const result = saveLocalPalette(this.snapshot.local, input, id);
      this.storeLocal(result.config, true); return result.palette;
    }
    const palette = await this.mutate<CustomPalette>(`/api/workspace/palettes${id ? `/${encodeURIComponent(id)}` : ""}`, id ? "PUT" : "POST", input);
    const remote = this.snapshot.remote!;
    this.publish({ remote: { ...remote, palettes: id ? remote.palettes.map(item => item.id === id ? palette : item) : [...remote.palettes, palette] } });
    return palette;
  };
  deletePalette = async (id: string) => {
    const local = this.snapshot.local;
    if (this.snapshot.mode === "local") { this.storeLocal({ ...local, palettes: local.palettes.filter(item => item.id !== id) }, true); return; }
    await this.mutate(`/api/workspace/palettes/${encodeURIComponent(id)}`, "DELETE");
    const remote = this.snapshot.remote!;
    this.publish({ remote: { ...remote, palettes: remote.palettes.filter(item => item.id !== id) } });
  };
  importConfig = async (text: string) => {
    const incoming = parseConfigFile(text);
    if (this.snapshot.mode === "local") { this.storeLocal(mergeConfig(this.snapshot.local, incoming), true); this.replaceContext(); return; }
    await this.retryRemote();
    if (this.pending) throw new Error(this.snapshot.stateError);
    const remote = await this.fetchRemote();
    const merged = mergeConfig(initialConfig(this.snapshot.state, remote.workspace.views, remote.palettes), incoming);
    const saved_at = Math.max(Date.now(), remote.workspace.saved_at + 1);
    const workspace = await this.mutate<WorkspaceSnapshot>("/api/workspace/config", "PUT", { ...merged, saved_at });
    this.write(`${this.key}:remote`, { state: workspace.state, saved_at: workspace.saved_at });
    this.replaceContext({ remote: { workspace, palettes: merged.palettes }, ...(this.snapshot.mode === "remote" ? { state: normalizeState(workspace.state ?? {}) } : {}) });
  };
  restoreRemote = async () => {
    if (this.snapshot.mode !== "local") throw new Error("Switch to Local mode to restore remote settings.");
    const remote = await this.fetchRemote();
    this.storeLocal(restoreRemoteConfig(this.snapshot.local, remote.workspace.state), true);
    this.replaceContext({ remote, linkNotice: "" });
  };
  retryStorage = () => {
    this.storeLocal(this.snapshot.local);
    this.write(`${this.key}:mode`, this.snapshot.mode);
    if (this.pending) this.write(`${this.key}:remote`, this.pending);
  };
  retryRemote = async () => { await this.flushRemote(); };
  stop = () => { this.generation++; clearTimeout(this.timer); };
}
