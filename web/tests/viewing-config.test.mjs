import assert from "node:assert/strict";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test, { after } from "node:test";
import ts from "typescript";

const directory = await mkdtemp(join(tmpdir(), "cloverboard-viewing-"));
after(() => rm(directory, { recursive: true, force: true }));
for (const name of ["palettes", "comparison", "features", "targets", "workspace", "viewingConfig", "workspaceSession", "theme"]) {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
  await writeFile(join(directory, `${name}.mjs`), outputText.replace(/from "\.\/([^"]+)"/g, 'from "./$1.mjs"'));
}
const configModule = await import(pathToFileURL(join(directory, "viewingConfig.mjs")));
const workspace = await import(pathToFileURL(join(directory, "workspace.mjs")));
const theme = await import(pathToFileURL(join(directory, "theme.mjs")));
const { WorkspaceSession } = await import(pathToFileURL(join(directory, "workspaceSession.mjs")));
const base = () => configModule.initialConfig(null);
function library() {
  const { config, palette } = configModule.saveLocalPalette(base(), { name: "Ocean", colors: ["#3276b8", "#267f6c"] });
  return configModule.saveLocalView({ ...config, state: { ...config.state, colors: { ...config.state.colors, palette: { kind: "custom", ...palette } } } }, "Comparison");
}

test("local Views and palettes round-trip together through browser storage and JSON", () => {
  const config = library(), memory = new Map();
  const storage = { getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value) };
  const key = configModule.localConfigKey("workspace-a");
  configModule.writeLocalConfig(storage, key, config);
  assert.deepEqual(configModule.readLocalConfig(storage, key), config);
  assert.deepEqual(configModule.parseConfigFile(configModule.exportConfig(config)), config);
  assert.equal(configModule.readLocalConfig(storage, configModule.localConfigKey("workspace-b")), null);
  assert.deepEqual(Object.keys(config.state.colors.runs), []);
});

test("import adds copies, handles case-insensitive name collisions and remaps palette references", () => {
  const existing = library(), imported = configModule.mergeConfig(existing, existing);
  const view = imported.views[1], palette = imported.palettes[1];
  assert.equal(view.name, "Comparison (2)");
  assert.equal(palette.name, "Ocean (2)");
  assert.notEqual(view.view_id, existing.views[0].view_id);
  assert.notEqual(palette.id, existing.palettes[0].id);
  assert.equal(imported.state.active_view_id, view.view_id);
  assert.equal(imported.state.colors.palette.id, palette.id);
  assert.equal(view.state.colors.palette.id, palette.id);
  assert.equal(workspace.viewStateKey(imported.state), workspace.viewStateKey(view.state));
  assert.deepEqual(imported.views[0], existing.views[0]);
  assert.deepEqual(configModule.parseConfigFile(configModule.exportConfig(imported)), imported);
});

test("import preserves historical palette snapshots even when the library was edited or deleted", () => {
  const old = library();
  const edited = configModule.saveLocalPalette(old, { name: "Ocean", colors: ["#9955af", "#c05a24"] }, old.palettes[0].id).config;
  const imported = configModule.mergeConfig(base(), edited);
  assert.deepEqual(imported.views[0].state.colors.palette.colors, old.palettes[0].colors);
  assert.deepEqual(imported.palettes[0].colors, edited.palettes[0].colors);
  assert.equal(imported.views[0].state.colors.palette.id, imported.palettes[0].id);
  const withoutLibrary = configModule.mergeConfig(base(), { ...old, palettes: [] });
  assert.equal(withoutLibrary.palettes.length, 0);
  assert.deepEqual(withoutLibrary.state.colors.palette.colors, old.palettes[0].colors);
  assert.equal(withoutLibrary.state.colors.palette.id, withoutLibrary.views[0].state.colors.palette.id);
});

test("restoring remote settings replaces only the current view and its colors", () => {
  const current = library();
  const state = workspace.normalizeState({ smoothing: .2, metric_filter: "remote" });
  const restored = configModule.restoreRemoteConfig(current, state);
  assert.deepEqual(restored.state, state);
  assert.deepEqual(restored.views, current.views);
  assert.deepEqual(restored.palettes, current.palettes);
});

test("local editing is independent of server writability and protects remote library entries", () => {
  const config = library();
  const changed = configModule.saveLocalView({ ...config, state: { ...config.state, smoothing: .1 } }, "Renamed", config.views[0].view_id);
  assert.equal(changed.views.length, 1);
  assert.equal(changed.views[0].state.smoothing, .1);
  assert.throws(() => configModule.saveLocalView(config, "Remote", "remote-id"), /View not found/);
  assert.throws(() => configModule.saveLocalPalette(config, { name: "Remote", colors: ["#123456", "#abcdef"] }, "remote-id"), /Palette not found/);
  assert.throws(() => configModule.saveLocalView(config, "comparison"), /already exists/);
  assert.throws(() => configModule.saveLocalPalette(config, { name: "ocean", colors: ["#123456", "#abcdef"] }), /already exists/);
});

test("invalid imports are rejected without changing existing libraries", () => {
  const config = library(), before = structuredClone(config);
  assert.throws(() => configModule.parseConfigFile("{"), /valid JSON/);
  assert.throws(() => configModule.parseConfigFile('{"version":1}'), /CloverBoard/);
  for (const patch of [{ version: 2 }, { views: {} }, { palettes: [null] }]) {
    assert.throws(() => configModule.parseConfigFile(JSON.stringify({ format: "cloverboard-viewing-config", ...config, ...patch })));
  }
  for (const patch of [{ search: 42 }, { selected_ids: 7 }, { collapsed_groups: {} }, { colors: null }, { smoothing: 10 }, { chart_zoom: { loss: { x_min: 10, x_max: 1 } } }]) {
    assert.throws(() => configModule.parseConfigFile(JSON.stringify({ format: "cloverboard-viewing-config", ...config, state: { ...config.state, ...patch } })), /viewing settings/);
  }
  assert.deepEqual(config, before);
});

test("storage failures propagate instead of pretending local data was saved", () => {
  const config = library(), quotaError = new Error("QuotaExceededError");
  assert.throws(() => configModule.writeLocalConfig({ setItem() { throw quotaError; } }, "key", config), error => error === quotaError);
  assert.throws(() => configModule.readLocalConfig({ getItem() { throw new Error("storage disabled"); } }, "key"), /storage disabled/);
  assert.throws(() => configModule.readLocalConfig({ getItem() { return "{"; } }, "key"));
  assert.match(configModule.STORAGE_ERROR, /Export JSON/);
});

test("unavailable browser storage does not crash theme initialization or saving", () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("storage disabled"); } });
  try {
    assert.equal(theme.loadThemeMode(), "light");
    assert.doesNotThrow(() => theme.saveThemeMode("dark"));
  } finally {
    if (previous) Object.defineProperty(globalThis, "localStorage", previous);
    else delete globalThis.localStorage;
  }
});

test("large configurations use JSON while URL sharing points to the available export action", () => {
  const config = { ...base(), state: workspace.normalizeState({ selected_ids: Array.from({ length: 1000 }, (_, index) => `experiment-${index}`) }) };
  assert.throws(() => workspace.encodeSharedState(config.state), /Export.*JSON/);
  assert.deepEqual(configModule.parseConfigFile(configModule.exportConfig(config)).state.selected_ids, config.state.selected_ids);
  assert.throws(() => configModule.parseConfigFile(" ".repeat(configModule.MAX_CONFIG_BYTES + 1)), /smaller than 2 MB/);
});

test("imports enforce library limits and reject duplicate identities", () => {
  const config = library();
  assert.throws(() => configModule.parseConfig({ ...config, views: [...config.views, ...config.views] }), /Duplicate/);
  assert.throws(() => configModule.parseConfig({ ...config, palettes: [...config.palettes, ...config.palettes] }), /Duplicate/);
  const full = { ...config, views: Array.from({ length: configModule.MAX_VIEWS }, (_, index) => ({ ...config.views[0], view_id: String(index), name: String(index) })) };
  assert.throws(() => configModule.mergeConfig(full, config), /exceed/);
});

test("imported colors and fixed flags persist inside the current state and named Views", () => {
  const incoming = library();
  incoming.state.colors.runs = { example: { color: "#123456", fixed: true } };
  incoming.views[0].state.colors.runs = { example: { color: "#abcdef", fixed: false } };
  const imported = configModule.mergeConfig(base(), incoming);
  assert.deepEqual(configModule.parseConfigFile(configModule.exportConfig(imported)), imported);
  assert.deepEqual(imported.state.colors.runs, incoming.state.colors.runs);
  assert.deepEqual(imported.views[0].state.colors.runs, incoming.views[0].state.colors.runs);
});

function sessionFixture(t, writable = true, memory = new Map()) {
  const remote = { workspace_id: "workspace-a", writable, saved_at: 1, state: workspace.defaultWorkspaceState(), views: [] };
  const palettes = []; let serial = 0;
  const writes = [], failures = new Set();
  const storage = { getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value) };
  async function request(url, init = {}) {
    const method = init.method ?? "GET", body = init.body ? JSON.parse(init.body) : undefined;
    if (method === "GET") return structuredClone(url.endsWith("/palettes") ? palettes : remote);
    writes.push({ url, method, body });
    if (!writable || failures.has(url)) throw new Error("Request failed");
    if (url.endsWith("/state")) { Object.assign(remote, body); return structuredClone(remote); }
    if (url.endsWith("/config")) {
      remote.views = body.views; remote.state = body.state; remote.saved_at = body.saved_at;
      palettes.splice(0, palettes.length, ...body.palettes);
      return structuredClone(remote);
    }
    const palette = url.includes("/palettes"), items = palette ? palettes : remote.views;
    const idField = palette ? "id" : "view_id";
    const id = method === "POST" ? (palette ? "custom:" : "") + (++serial).toString(16).padStart(32, "0") : decodeURIComponent(url.split("/").at(-1));
    const index = items.findIndex(item => item[idField] === id);
    if (method === "DELETE") return items.splice(index, 1)[0];
    const item = { ...(method === "PATCH" ? items[index] : {}), ...body, [idField]: id };
    if (!palette) item.state = { ...workspace.normalizeState(item.state), active_view_id: null };
    if (index < 0) items.push(item); else items[index] = item;
    return structuredClone(item);
  }
  const session = new WorkspaceSession(request, () => storage);
  t.after(() => session.stop());
  return { session, remote, palettes, writes, failures, memory, storage, request };
}

test("normal mode defaults to remote autosave and remote View/palette CRUD", async t => {
  const f = sessionFixture(t), { session } = f;
  configModule.writeLocalConfig(f.storage, configModule.localConfigKey("workspace-a"), { ...library(), state: workspace.normalizeState({ smoothing: .1 }) });
  await session.load("http://localhost/");
  assert.equal(session.getSnapshot().mode, "remote");
  assert.equal(session.getSnapshot().state.smoothing, .6);
  session.update({ smoothing: .3 });
  await session.flushRemote();
  assert.equal(f.remote.state.smoothing, .3);
  await session.saveView("Remote comparison");
  const viewId = f.remote.views[0].view_id;
  session.update({ smoothing: .4 });
  await session.saveView("Remote comparison", viewId);
  await session.renameView(viewId, "Renamed remote");
  assert.equal(f.remote.views[0].state.smoothing, .4);
  assert.equal(f.remote.views[0].name, "Renamed remote");
  const palette = await session.savePalette({ name: "Remote ocean", colors: ["#123456", "#abcdef"] });
  await session.savePalette({ name: "Changed remote", colors: ["#234567", "#abcdef"] }, palette.id);
  assert.equal(f.palettes[0].name, "Changed remote");
  await session.deleteView(viewId);
  await session.deletePalette(palette.id);
  await session.flushRemote();
  assert.equal(f.remote.views.length, 0);
  assert.equal(f.palettes.length, 0);
  assert.equal(f.remote.state.active_view_id, null);
  assert.equal(session.getSnapshot().local.views[0].name, "Comparison");
});

test("normal mode can keep browser changes separate and switching back reloads remote settings", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  session.update({ smoothing: .3 });
  await session.setMode("local");
  session.update({ smoothing: .1 });
  await session.saveView("Local comparison");
  await session.savePalette({ name: "Local ocean", colors: ["#123456", "#abcdef"] });
  await session.flushRemote();
  assert.equal(f.remote.state.smoothing, .3);
  assert.equal(f.writes.length, 1);
  assert.equal(f.remote.views.length, 0);
  assert.equal(f.palettes.length, 0);
  const reloaded = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => reloaded.stop());
  await reloaded.load("http://localhost/");
  assert.equal(reloaded.getSnapshot().mode, "local");
  assert.equal(reloaded.getSnapshot().state.smoothing, .1);
  f.remote.state.smoothing = .7; f.remote.saved_at += 1;
  await session.setMode("remote");
  assert.equal(session.getSnapshot().state.smoothing, .7);
  assert.equal(f.writes.length, 1);
  await session.setMode("local");
  assert.equal(session.getSnapshot().state.smoothing, .1);
});

test("readonly sessions save locally, protect remote libraries and restore remote without writes", async t => {
  const f = sessionFixture(t, false), { session } = f;
  f.remote.views.push({ view_id: "a".repeat(32), name: "Remote view", state: workspace.defaultWorkspaceState() });
  await session.load("http://localhost/");
  session.update({ smoothing: .2 });
  session.update({ colors: { ...session.getSnapshot().state.colors, runs: { local: { color: "#abcdef", fixed: true } } } });
  await session.saveView("Personal comparison");
  await session.savePalette({ name: "Personal ocean", colors: ["#123456", "#abcdef"] });
  await assert.rejects(session.setMode("remote"), /read-only/);
  await session.renameView("a".repeat(32), "Local rename");
  await session.deleteView("a".repeat(32));
  await session.retryRemote();
  const restarted = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => restarted.stop());
  await restarted.load("http://localhost/");
  assert.equal(restarted.getSnapshot().state.smoothing, .2);
  assert.deepEqual(restarted.getSnapshot().state.colors.runs.local, { color: "#abcdef", fixed: true });
  await session.restoreRemote();
  assert.equal(session.getSnapshot().state.smoothing, .6);
  assert.equal(session.getSnapshot().local.views.length, 1);
  assert.equal(session.getSnapshot().local.palettes.length, 1);
  assert.deepEqual(f.writes, []);
});

test("imports follow the explicit mode and never switch it implicitly", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  await session.importConfig(configModule.exportConfig(library()));
  assert.equal(session.getSnapshot().mode, "remote");
  assert.equal(f.remote.views.length, 1);
  assert.equal(f.palettes.length, 1);
  assert.equal(f.writes.at(-1).url, "/api/workspace/config");
  await session.setMode("local");
  const writes = f.writes.length;
  await session.importConfig(configModule.exportConfig(library()));
  session.openView(session.getConfig().views[0]);
  assert.equal(session.getSnapshot().mode, "local");
  assert.equal(f.writes.length, writes);
  assert.equal(session.getSnapshot().local.views.length, 1);
});

test("remote autosave failures keep a recoverable cache and retry independently of local state", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  f.failures.add("/api/workspace/state");
  session.update({ smoothing: .3 });
  await session.flushRemote();
  assert.match(session.getSnapshot().stateError, /remote workspace/);
  assert.equal(f.remote.state.smoothing, .6);
  session.stop();
  f.failures.clear();
  const restarted = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => restarted.stop());
  await restarted.load("http://localhost/");
  assert.equal(restarted.getSnapshot().state.smoothing, .3);
  await restarted.setMode("local");
  restarted.update({ smoothing: .1 });
  await restarted.retryRemote();
  assert.equal(f.remote.state.smoothing, .3);
  assert.equal(restarted.getSnapshot().state.smoothing, .1);
  assert.equal(restarted.getSnapshot().stateError, "");
});

test("failed remote library saves and unavailable browser storage do not pretend to succeed", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  f.failures.add("/api/workspace/views");
  await assert.rejects(session.saveView("Remote failure"), /Request failed/);
  assert.equal(session.getSnapshot().remote.workspace.views.length, 0);
  f.storage.setItem = () => { throw new Error("Quota exceeded"); };
  session.update({ smoothing: .2 });
  await session.flushRemote();
  assert.equal(f.remote.state.smoothing, .2);
  await assert.rejects(session.setMode("local"), /Could not save in this browser/);
  assert.equal(session.getSnapshot().local.views.length, 0);
  assert.equal(session.getSnapshot().mode, "remote");
});

test("pagehide drains a newer remote save while keeping local settings isolated", async t => {
  const f = sessionFixture(t);
  let release, started;
  const gate = new Promise(resolve => { release = resolve; });
  const first = new Promise(resolve => { started = resolve; });
  let pause = true;
  const session = new WorkspaceSession(async (url, init) => {
    if (url.endsWith("/state") && pause) { pause = false; started(); await gate; }
    return f.request(url, init);
  }, () => f.storage);
  t.after(() => session.stop());
  await session.load("http://localhost/");
  session.update({ smoothing: .2 });
  const saving = session.flushRemote();
  await first;
  session.update({ smoothing: .3 });
  const leaving = session.flushRemote(true);
  release();
  await Promise.all([saving, leaving]);
  assert.equal(f.remote.state.smoothing, .3);
  assert.equal(f.writes.length, 2);
  await session.setMode("local");
  session.update({ smoothing: .1 });
  await session.flushRemote(true);
  assert.equal(f.remote.state.smoothing, .3);
});

function watchingSettings() {
  return workspace.normalizeState({
    selected_ids: ["baseline", "trial"], baseline_id: "baseline", search: "trial|baseline", search_regex: true,
    tag_filter: ["reviewed"], tag_filter_match: "all", run_sort: "name_desc", panel: "table",
    colors: { ...workspace.defaultWorkspaceState().colors, runs: { trial: { color: "#abcdef", fixed: true } }, top_k: { enabled: true, metric: "score", direction: "max", readout: "best", k: 2 } },
    targets: { score: { items: [{ id: "goal", name: "Goal", chart_label: "G", value: .9, direction: "gte" }], active_id: "goal" } },
    metric_filter: "loss", chart_density: "compact", show_chart_legend: false, smoothing: .2,
    y_scale: "log", y_warmup: 100, chart_scales: { score: "linear" }, chart_warmup: { score: null },
    collapsed_groups: ["train"], expanded_charts: ["validation/score"],
    tooltip_decimals: 6, tooltip_sort: "list", tooltip_placement: "horizontal", tooltip_time: "wall", tooltip_delta: "hidden",
    metric_columns: ["score"], hidden_params: ["seed"], differences_only: false, sort_column: "metric:score", sort_desc: true,
    table_decimals: 2, table_time: "hidden", table_show_steps: false, table_delta: "target", show_tags_column: true,
    media_type: "audio", audio_name: "sample", audio_step: 0, image_name: "pred", image_step: 100,
    text_name: "prompt", text_step: 200, image_timeline_samples: 50, images_per_step: 4, refresh_interval: 30,
  });
}

for (const mode of ["remote", "local", "readonly"]) test(`${mode}: all watching settings use the same autosave, View and JSON snapshot`, async t => {
  const f = sessionFixture(t, mode !== "readonly"), { session } = f;
  await session.load("http://localhost/");
  if (mode === "local") await session.setMode("local");
  const expected = watchingSettings();
  session.update(expected);
  await session.flushRemote();
  const version = session.getSnapshot().contextVersion;
  await session.saveView("Watching settings");
  assert.equal(session.getSnapshot().contextVersion, version); // Saving a name does not reset temporary UI.
  const config = session.getConfig();
  assert.deepEqual(config.views[0].state, expected);
  const file = configModule.parseConfigFile(configModule.exportConfig(config));
  assert.deepEqual({ ...file.state, active_view_id: null }, expected);
  assert.deepEqual(file.views[0].state, expected);
  if (mode === "remote") assert.deepEqual(f.remote.views[0].state, expected);
  else assert.deepEqual(f.writes, []);
  session.openView(config.views[0]);
  assert.equal(session.getSnapshot().contextVersion, version + 1);
});

test("temporary interactions and browser preferences never affect saved settings or View dirty state", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  const viewing = watchingSettings();
  const interacting = { ...viewing, chart_zoom: { loss: { x_min: 10, x_max: 20, y_min: 0, y_max: 1 } }, hoverStep: 15, pinned: true, theme: "dark", sidebarWidth: 350 };
  assert.equal(workspace.viewStateKey(interacting), workspace.viewStateKey(viewing));
  assert.deepEqual(workspace.readSharedLocation(`http://localhost/#state=${workspace.encodeSharedState(interacting)}`).state, viewing);
  session.update(interacting);
  await session.flushRemote();
  assert.deepEqual(f.remote.state, viewing);
  await session.saveView("Clean snapshot");
  assert.deepEqual(f.remote.views[0].state, viewing);
  const file = JSON.parse(configModule.exportConfig(session.getConfig()));
  for (const key of ["chart_zoom", "hoverStep", "pinned", "theme", "sidebarWidth", "mode", "contextVersion"]) assert.equal(Object.hasOwn(file.state, key), false);
});

test("saving a palette only changes its library in either mode", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  for (const mode of ["remote", "local"]) {
    await session.setMode(mode);
    const before = structuredClone(session.getSnapshot().state);
    await session.savePalette({ name: "Ocean", colors: ["#123456", "#abcdef"] });
    assert.deepEqual(session.getSnapshot().state, before);
    assert.equal(session.getConfig().palettes[0].name, "Ocean");
  }
});
