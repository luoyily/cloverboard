import assert from "node:assert/strict";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test, { after } from "node:test";
import ts from "typescript";

const directory = await mkdtemp(join(tmpdir(), "cloverboard-viewing-"));
after(() => rm(directory, { recursive: true, force: true }));
for (const name of ["palettes", "comparison", "features", "targets", "workspace", "viewingConfig", "workspaceSession", "theme", "runSelection"]) {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
  await writeFile(join(directory, `${name}.mjs`), outputText.replace(/from "\.\/([^"]+)"/g, 'from "./$1.mjs"'));
}
const configModule = await import(pathToFileURL(join(directory, "viewingConfig.mjs")));
const workspace = await import(pathToFileURL(join(directory, "workspace.mjs")));
const theme = await import(pathToFileURL(join(directory, "theme.mjs")));
const { WorkspaceSession } = await import(pathToFileURL(join(directory, "workspaceSession.mjs")));
const { availableSelection, reconcileSelection } = await import(pathToFileURL(join(directory, "runSelection.mjs")));
const normalize = (patch = {}) => workspace.normalizeState(patch);
const view = (state, name = "Comparison", id = configModule.newViewId()) => ({ view_id: id, name, state: normalize(state) });
function memoryStorage(memory = new Map()) {
  return { getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value), removeItem: key => memory.delete(key) };
}
function library() {
  const state = normalize({ smoothing: .2 });
  const saved = configModule.saveLocalView([], state, "Comparison");
  return { state, views: saved.views };
}

test("the session and My Views persist under independent workspace keys", () => {
  const memory = new Map(), storage = memoryStorage(memory);
  const state = normalize({ smoothing: .2 });
  const saved = view(state, "Mine");
  configModule.writeLocalViews(storage, configModule.localViewsKey("workspace-a"), [saved]);
  const active = { scope: "local", view_id: saved.view_id, base_key: workspace.viewStateKey(state) };
  configModule.writeSession(storage, configModule.sessionKey("workspace-a"), { version: 1, state, active_view: active });
  assert.deepEqual(configModule.readLocalViews(storage, configModule.localViewsKey("workspace-a")), [saved]);
  assert.deepEqual(configModule.readSession(storage, configModule.sessionKey("workspace-a")), { version: 1, state, active_view: active });
  assert.equal(configModule.readLocalViews(storage, configModule.localViewsKey("workspace-b")), null);
  assert.equal(configModule.readSession(storage, configModule.sessionKey("workspace-b")), null);
  assert.equal(configModule.localViewsKey("workspace-a"), "cloverboard:views:local:workspace-a");
  assert.equal(configModule.sessionKey("workspace-a"), "cloverboard:session:workspace-a");
});

test("saving a My View replaces by id, rejects duplicate names and enforces the limit", () => {
  const state = normalize({ smoothing: .2 });
  const first = configModule.saveLocalView([], state, "  Mine  ");
  assert.equal(first.view.name, "Mine");
  assert.equal(first.baseKey, workspace.viewStateKey(state));
  const updated = configModule.saveLocalView(first.views, { ...state, smoothing: .3 }, "Mine", first.view.view_id);
  assert.equal(updated.views.length, 1);
  assert.equal(updated.views[0].state.smoothing, .3);
  assert.equal(updated.view.view_id, first.view.view_id);
  assert.throws(() => configModule.saveLocalView(first.views, state, "mine"), /already exists/);
  assert.throws(() => configModule.saveLocalView(first.views, state, "Mine", "0".repeat(32)), /not found/);
  assert.throws(() => configModule.saveLocalView(first.views, state, " "), /between 1 and 80/);
  const full = Array.from({ length: configModule.MAX_VIEWS }, (_, index) => (
    { view_id: index.toString(16).padStart(32, "0"), name: `View ${index}`, state }
  ));
  assert.throws(() => configModule.saveLocalView(full, state, "Extra"), /Limit reached/);
});

test("export and import cover only the session and My Views, including old export files", () => {
  const state = normalize({ smoothing: .25, selected_ids: ["a"] });
  const views = [view(state, "Mine")];
  const file = JSON.parse(configModule.exportLocalFile(state, views));
  assert.equal(file.format, "cloverboard-local-config");
  assert.equal(file.version, 1);
  assert.deepEqual(file.state, state);
  assert.deepEqual(file.views, views);
  assert.deepEqual(configModule.parseImportFile(JSON.stringify(file)), { state, views });
  assert.deepEqual(configModule.parseImportFile(JSON.stringify({ format: "cloverboard-viewing-config", version: 1, state, views })), { state, views });
});

test("imports copy Views with unique names and fresh identities", () => {
  const mine = configModule.saveLocalView([], normalize(), "Comparison").views;
  const incoming = [view({ smoothing: .4 }, "comparison"), view({}, "Other")];
  const merged = configModule.mergeLocalViews(mine, incoming);
  assert.deepEqual(merged.map(item => item.name), ["Comparison", "comparison (2)", "Other"]);
  assert.equal(new Set(merged.map(item => item.view_id)).size, 3);
  assert.equal(merged[1].state.smoothing, .4);
  assert.throws(() => configModule.mergeLocalViews(mine, Array.from({ length: configModule.MAX_VIEWS }, () => view({}))), /exceed/);
});

test("invalid session files and imports are rejected", () => {
  assert.throws(() => configModule.parseImportFile("{"), /valid JSON/);
  assert.throws(() => configModule.parseImportFile('{"version":1}'), /CloverBoard/);
  assert.throws(() => configModule.parseImportFile(JSON.stringify({
    format: "cloverboard-local-config", version: 1, state: normalize(),
    views: [{ view_id: "not-a-view", name: "A", state: normalize() }],
  })), /Invalid saved View/);
  assert.throws(() => configModule.parseImportFile(JSON.stringify({
    format: "cloverboard-local-config", version: 1, state: { ...normalize(), version: 2 }, views: [],
  })), /viewing settings/);
  assert.throws(() => configModule.parseImportFile(" ".repeat(configModule.MAX_CONFIG_BYTES + 1)), /2 MB/);
  assert.throws(() => configModule.readSession({ getItem: () => "{" }, "key"));
  assert.throws(() => configModule.readLocalViews({ getItem: () => "{" }, "key"));
});

test("storage failures propagate instead of pretending local data was saved", () => {
  const quotaError = new Error("QuotaExceededError");
  assert.throws(() => configModule.writeSession({ setItem() { throw quotaError; } }, "key", { version: 1, state: normalize(), active_view: null }), error => error === quotaError);
  assert.throws(() => configModule.writeLocalViews({ setItem() { throw quotaError; } }, "key", []), error => error === quotaError);
  assert.throws(() => configModule.readSession({ getItem() { throw new Error("storage disabled"); } }, "key"), /storage disabled/);
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

function sessionFixture(t, writable = true, memory = new Map()) {
  const remote = {
    workspace_id: "workspace-a", writable, saved_at: 1,
    state: workspace.defaultWorkspaceState(), views: [],
    target_library: { targets: {}, saved_at: 1 },
  };
  const palettes = [];
  let serial = 0;
  const writes = [], failures = new Set();
  const storage = memoryStorage(memory);
  async function request(url, init = {}) {
    const method = init.method ?? "GET", body = init.body ? JSON.parse(init.body) : undefined;
    if (method === "GET") return structuredClone(url.endsWith("/palettes") ? palettes : remote);
    writes.push({ url, method, body });
    if (!writable || failures.has(url)) throw new Error("Request failed");
    if (url.endsWith("/targets")) { remote.target_library = body; return structuredClone(remote.target_library); }
    const palette = url.includes("/palettes"), items = palette ? palettes : remote.views;
    const idField = palette ? "id" : "view_id";
    const id = method === "POST" ? (palette ? "custom:" : "") + (++serial).toString(16).padStart(32, "0") : decodeURIComponent(url.split("/").at(-1));
    const index = items.findIndex(item => item[idField] === id);
    if (method === "DELETE") return items.splice(index, 1)[0];
    const item = { ...(method === "PATCH" ? items[index] : {}), ...body, [idField]: id };
    if (!palette) item.state = workspace.normalizeState(item.state);
    if (index < 0) items.push(item); else items[index] = item;
    return structuredClone(item);
  }
  const session = new WorkspaceSession(request, () => storage);
  t.after(() => session.stop());
  return { session, remote, palettes, writes, failures, memory, storage, request };
}

function watchingSettings() {
  return workspace.normalizeState({
    selected_ids: ["baseline", "trial"], baseline_id: "baseline", search: "trial|baseline", search_regex: true,
    tag_filter: ["reviewed"], tag_filter_match: "all", run_sort: "name_desc", panel: "table",
    colors: { ...workspace.defaultWorkspaceState().colors, runs: { trial: { color: "#abcdef", fixed: true } }, top_k: { enabled: true, metric: "score", direction: "max", readout: "best", k: 2 } },
    metric_filter: "loss", chart_density: "compact", show_chart_legend: false, chart_legend_layout: "single", chart_legend_trim_prefix: true, smoothing: .2,
    y_scale: "log", y_warmup: 100, chart_scales: { score: "linear" }, chart_warmup: { score: null },
    collapsed_groups: ["train"], expanded_charts: ["validation/score"],
    tooltip_decimals: 6, tooltip_sort: "list", tooltip_placement: "horizontal", tooltip_time: "wall", tooltip_delta: "hidden",
    metric_columns: ["score"], hidden_params: ["seed"], differences_only: false, sort_column: "metric:score", sort_desc: true,
    table_decimals: 2, table_time: "hidden", table_show_steps: false, table_delta: "target", show_tags_column: true,
    media_type: "audio", audio_name: "sample", audio_step: 0, image_name: "pred", image_step: 100,
    text_name: "prompt", text_step: 200, image_timeline_samples: 50, images_per_step: 4,
  });
}

test("browser auto refresh ignores shared state and survives View changes", async t => {
  const f = sessionFixture(t);
  f.remote.state.refresh_interval = 0;
  f.remote.views.push({ view_id: "a".repeat(32), name: "Off", state: { ...normalize({ smoothing: .2 }), refresh_interval: 0 } });
  await f.session.load("http://localhost/");
  assert.equal(f.session.getSnapshot().refreshInterval, 5);
  assert.equal(f.memory.get(workspace.REFRESH_INTERVAL_KEY), "5");
  f.session.setRefreshInterval(30);
  assert.equal(f.session.getSnapshot().refreshInterval, 30);
  assert.equal(f.memory.get(workspace.REFRESH_INTERVAL_KEY), "30");
  f.session.openView(f.session.getSnapshot().sharedViews[0], "shared");
  assert.equal(f.session.getSnapshot().refreshInterval, 30);
  const reloaded = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => reloaded.stop());
  await reloaded.load("http://localhost/");
  assert.equal(reloaded.getSnapshot().refreshInterval, 30);
});

test("legacy local refresh interval migrates once and yields to the browser preference", async t => {
  const f = sessionFixture(t);
  const legacyState = { ...workspace.defaultWorkspaceState(), refresh_interval: 60 };
  f.memory.set(configModule.sessionKey("workspace-a"), JSON.stringify({ version: 1, state: legacyState, active_view: null }));
  await f.session.load("http://localhost/");
  assert.equal(f.session.getSnapshot().refreshInterval, 60);
  assert.equal(f.memory.get(workspace.REFRESH_INTERVAL_KEY), "60");
  f.memory.set(configModule.sessionKey("workspace-a"), JSON.stringify({ version: 1, state: { ...legacyState, refresh_interval: 0 }, active_view: null }));
  const reloaded = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => reloaded.stop());
  await reloaded.load("http://localhost/");
  assert.equal(reloaded.getSnapshot().refreshInterval, 60);
});

test("auto refresh is not saved in Views or session state", async t => {
  const f = sessionFixture(t);
  f.remote.views.push({ view_id: "a".repeat(32), name: "Shared", state: normalize({ smoothing: .2 }) });
  await f.session.load("http://localhost/");
  f.session.openView(f.session.getSnapshot().sharedViews[0], "shared");
  const baseKey = f.session.getSnapshot().activeView.base_key;
  f.session.setRefreshInterval(30);
  assert.equal(f.session.getSnapshot().activeView.base_key, baseKey);
  assert.equal(f.session.getSnapshot().state.refresh_interval, undefined);
  await f.session.saveView("Shared", "shared", f.session.getSnapshot().activeView.view_id);
  assert.equal(Object.hasOwn(f.remote.views[0].state, "refresh_interval"), false);
  assert.equal(configModule.readSession(f.storage, configModule.sessionKey("workspace-a")).state.refresh_interval, undefined);
});

test("loading keeps My Views and session state while refreshing shared assets", async t => {
  const f = sessionFixture(t), { session } = f;
  f.remote.views.push({ view_id: "a".repeat(32), name: "Shared comparison", state: normalize({ smoothing: .2 }) });
  await session.load("http://localhost/");
  session.update({ smoothing: .15, selected_ids: ["run-1"] });
  await session.saveView("Personal", "local");
  f.palettes.push({ id: "custom:" + "b".repeat(32), name: "Ocean", colors: ["#123456", "#abcdef"] });
  f.remote.target_library = {
    targets: { loss: { items: [{ id: "goal", name: "Goal", chart_label: "G", value: .5, direction: "lte" }], active_id: "goal" } },
    saved_at: 2,
  };
  f.remote.views[0] = { ...f.remote.views[0], name: "Shared comparison v2" };
  const reloaded = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => reloaded.stop());
  await reloaded.load("http://localhost/");
  const snapshot = reloaded.getSnapshot();
  assert.equal(snapshot.state.smoothing, .15);
  assert.deepEqual(snapshot.state.selected_ids, ["run-1"]);
  assert.deepEqual(snapshot.localViews.map(item => item.name), ["Personal"]);
  assert.deepEqual(snapshot.sharedViews.map(item => item.name), ["Shared comparison v2"]);
  assert.equal(snapshot.palettes.length, 1);
  assert.equal(snapshot.targets.loss.items[0].id, "goal");
});

test("Refresh follows shared View edits only while the session is unmodified", async t => {
  const f = sessionFixture(t);
  const viewId = "a".repeat(32);
  f.remote.views.push({ view_id: viewId, name: "Shared", state: normalize({ smoothing: .2 }) });
  await f.session.load("http://localhost/");
  f.session.openView(f.remote.views[0], "shared");
  assert.equal(f.session.getSnapshot().activeView.scope, "shared");
  f.remote.views[0].state = normalize({ smoothing: .4 });
  const reloaded = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => reloaded.stop());
  await reloaded.load("http://localhost/");
  assert.equal(reloaded.getSnapshot().state.smoothing, .4);
  assert.equal(reloaded.getSnapshot().activeView.base_key, workspace.viewStateKey(f.remote.views[0].state));
  reloaded.update({ smoothing: .1 });
  f.remote.views[0].state = normalize({ smoothing: .5 });
  const third = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => third.stop());
  await third.load("http://localhost/");
  assert.equal(third.getSnapshot().state.smoothing, .1);
  assert.equal(third.getSnapshot().activeView.view_id, viewId);
});

test("Shared View CRUD reaches the server while My Views stay in the browser", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  session.update({ smoothing: .3 });
  await session.saveView("Team view", "shared");
  assert.equal(f.remote.views.length, 1);
  assert.equal(f.writes[0].url, "/api/workspace/views");
  assert.equal(f.writes[0].body.state.smoothing, .3);
  assert.equal(session.getSnapshot().activeView.scope, "shared");
  await session.saveView("My view", "local");
  assert.equal(f.remote.views.length, 1);
  assert.deepEqual(session.getSnapshot().localViews.map(item => item.name), ["My view"]);
  assert.equal(session.getSnapshot().activeView.scope, "local");
  const sharedId = f.remote.views[0].view_id, localId = session.getSnapshot().localViews[0].view_id;
  await session.renameView("shared", sharedId, "Renamed team view");
  await session.renameView("local", localId, "Renamed my view");
  assert.deepEqual(f.writes.map(item => item.url), ["/api/workspace/views", `/api/workspace/views/${sharedId}`]);
  assert.deepEqual(f.writes[1].body, { name: "Renamed team view" });
  assert.equal(f.remote.views[0].name, "Renamed team view");
  assert.equal(session.getSnapshot().localViews[0].name, "Renamed my view");
  await session.deleteView("shared", sharedId);
  assert.equal(f.remote.views.length, 0);
  assert.equal(f.writes.at(-1).method, "DELETE");
  assert.equal(session.getSnapshot().localViews.length, 1);
});

test("a read-only workspace saves the session and My Views but never writes server assets", async t => {
  const f = sessionFixture(t, false), { session } = f;
  await session.load("http://localhost/");
  session.update({ smoothing: .25 });
  await session.saveView("Personal", "local");
  await assert.rejects(session.saveView("Team", "shared"), /read-only/);
  await assert.rejects(session.savePalette({ name: "Ocean", colors: ["#123456", "#abcdef"] }), /read-only/);
  const targets = { loss: { items: [{ id: "goal", name: "Goal", chart_label: "G", value: .5, direction: "lte" }], active_id: "goal" } };
  await session.saveTargets(targets);
  assert.match(session.getSnapshot().targetsError, /Could not save targets/);
  assert.deepEqual(f.writes, []);
  const reloaded = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => reloaded.stop());
  await reloaded.load("http://localhost/");
  assert.equal(reloaded.getSnapshot().state.smoothing, .25);
  assert.deepEqual(reloaded.getSnapshot().localViews.map(item => item.name), ["Personal"]);
  assert.deepEqual(reloaded.getSnapshot().targets, {});
});

test("Promote to Shared moves a My View onto the server", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  session.update({ smoothing: .35 });
  await session.saveView("Mine", "local");
  const localId = session.getSnapshot().localViews[0].view_id;
  await session.promoteView(localId);
  const snapshot = session.getSnapshot();
  assert.deepEqual(snapshot.localViews, []);
  assert.equal(snapshot.sharedViews.length, 1);
  assert.equal(snapshot.sharedViews[0].name, "Mine");
  assert.equal(snapshot.activeView.scope, "shared");
  assert.equal(snapshot.activeView.view_id, snapshot.sharedViews[0].view_id);
  assert.equal(f.remote.views[0].state.smoothing, .35);
  assert.deepEqual(f.writes.map(item => item.url), ["/api/workspace/views"]);
  const reloaded = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => reloaded.stop());
  await reloaded.load("http://localhost/");
  assert.deepEqual(reloaded.getSnapshot().localViews, []);
  assert.equal(reloaded.getSnapshot().sharedViews.length, 1);
  assert.equal(reloaded.getSnapshot().activeView.scope, "shared");
});

test("import adds My Views and applies the session without touching shared assets", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  const state = normalize({ smoothing: .2, search: "imported" });
  const views = [view(state, "Imported")];
  await session.importConfig(configModule.exportLocalFile(state, views));
  assert.equal(session.getSnapshot().state.search, "imported");
  assert.equal(session.getSnapshot().localViews[0].name, "Imported");
  assert.equal(session.getSnapshot().activeView, null);
  assert.deepEqual(f.writes, []);
  const firstId = session.getSnapshot().localViews[0].view_id;
  await session.importConfig(configModule.exportLocalFile(state, views));
  assert.equal(session.getSnapshot().localViews.length, 2);
  assert.equal(session.getSnapshot().localViews[1].name, "Imported (2)");
  assert.notEqual(session.getSnapshot().localViews[1].view_id, firstId);
});

test("legacy browser configs migrate once into the session and My Views", async t => {
  const f = sessionFixture(t);
  const shared = { view_id: "a".repeat(32), name: "Shared", state: normalize({ smoothing: .2 }) };
  const local = view({ smoothing: .3 }, "Local", "b".repeat(32));
  f.remote.views.push(shared);
  f.memory.set(configModule.legacyConfigKey("workspace-a"), JSON.stringify({
    version: 1, palettes: [],
    views: [local, shared],
    state: { ...normalize({ smoothing: .4 }), active_view_id: local.view_id },
  }));
  for (const suffix of [":mode", ":remote"]) f.memory.set(configModule.legacyConfigKey("workspace-a") + suffix, "legacy");
  await f.session.load("http://localhost/");
  const snapshot = f.session.getSnapshot();
  assert.equal(snapshot.state.smoothing, .4);
  assert.deepEqual(snapshot.localViews.map(item => item.name), ["Local"]);
  assert.deepEqual(snapshot.activeView, { scope: "local", view_id: local.view_id, base_key: workspace.viewStateKey(local.state) });
  for (const suffix of ["", ":mode", ":remote"]) assert.equal(f.memory.has(configModule.legacyConfigKey("workspace-a") + suffix), false);
  assert.ok(f.memory.has(configModule.sessionKey("workspace-a")));
  assert.ok(f.memory.has(configModule.localViewsKey("workspace-a")));
  assert.deepEqual(f.writes, []);
});

test("shared links reopen Views and state links snapshot without changing saved assets", async t => {
  const f = sessionFixture(t), { session } = f;
  const linkedView = { view_id: "a".repeat(32), name: "Linked", state: normalize({ smoothing: .2, panel: "table" }) };
  f.remote.views.push(linkedView);
  assert.equal(await session.load(`http://localhost/?view=${linkedView.view_id}`), true);
  assert.equal(session.getSnapshot().state.panel, "table");
  assert.deepEqual(session.getSnapshot().activeView, { scope: "shared", view_id: linkedView.view_id, base_key: workspace.viewStateKey(linkedView.state) });
  const linkedState = normalize({ smoothing: .1, search: "link" });
  assert.equal(await session.load(`http://localhost/#state=${workspace.encodeSharedState(linkedState)}`), true);
  assert.equal(session.getSnapshot().state.search, "link");
  assert.equal(session.getSnapshot().activeView, null);
  await session.load(`http://localhost/?view=${"c".repeat(32)}`);
  assert.match(session.getSnapshot().linkNotice, /unavailable/);
});

test("a deleted active Shared View stops being named after refresh", async t => {
  const f = sessionFixture(t);
  f.remote.views.push({ view_id: "a".repeat(32), name: "Gone", state: normalize({ smoothing: .2 }) });
  await f.session.load("http://localhost/");
  f.session.openView(f.remote.views[0], "shared");
  f.remote.views = [];
  const reloaded = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => reloaded.stop());
  await reloaded.load("http://localhost/");
  assert.equal(reloaded.getSnapshot().activeView, null);
  assert.equal(reloaded.getSnapshot().state.smoothing, .2);
});

test("shared watching settings persist in the session and both View scopes", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  const expected = watchingSettings();
  session.update(expected);
  const version = session.getSnapshot().contextVersion;
  await session.saveView("Team watching", "shared");
  assert.equal(session.getSnapshot().contextVersion, version);
  assert.deepEqual(f.remote.views[0].state, expected);
  await session.saveView("My watching", "local");
  assert.deepEqual(session.getSnapshot().localViews[0].state, expected);
  const file = configModule.parseImportFile(configModule.exportLocalFile(session.getSnapshot().state, session.getSnapshot().localViews));
  assert.deepEqual(file.state, expected);
  assert.deepEqual(file.views[0].state, expected);
  session.openView(f.remote.views[0], "shared");
  assert.equal(session.getSnapshot().contextVersion, version + 1);
  const reloaded = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => reloaded.stop());
  await reloaded.load("http://localhost/");
  assert.deepEqual(reloaded.getSnapshot().state, expected);
});

test("temporary interactions never enter the session, Views or imports", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  const viewing = watchingSettings();
  const interacting = { ...viewing, chart_zoom: { loss: { x_min: 10, x_max: 20, y_min: 0, y_max: 1 } }, hoverStep: 15, pinned: true, theme: "dark", sidebarWidth: 350 };
  assert.equal(workspace.viewStateKey(interacting), workspace.viewStateKey(viewing));
  assert.deepEqual(workspace.readSharedLocation(`http://localhost/#state=${workspace.encodeSharedState(interacting)}`).state, viewing);
  session.update(interacting);
  await session.saveView("Clean snapshot", "shared");
  assert.deepEqual(f.remote.views[0].state, viewing);
  assert.deepEqual(configModule.readSession(f.storage, configModule.sessionKey("workspace-a")).state, viewing);
  const file = JSON.parse(configModule.exportLocalFile(session.getSnapshot().state, session.getSnapshot().localViews));
  for (const key of ["chart_zoom", "hoverStep", "pinned", "theme", "sidebarWidth", "mode", "contextVersion"]) assert.equal(Object.hasOwn(file.state, key), false);
});

test("the target library saves independently and never enters Views or session state", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  const before = structuredClone(session.getSnapshot().state);
  const targets = { loss: { items: [{ id: "goal", name: "Goal", chart_label: "G", value: .5, direction: "lte" }], active_id: "goal" } };
  await session.saveTargets(targets);
  assert.deepEqual(f.remote.target_library.targets, targets);
  assert.deepEqual(session.getSnapshot().state, before);
  await session.saveView("With targets", "shared");
  assert.equal(Object.hasOwn(f.remote.views[0].state, "targets"), false);
  session.openView(f.remote.views[0], "shared");
  assert.deepEqual(session.getSnapshot().targets, targets);
  f.failures.add("/api/workspace/targets");
  await session.saveTargets({});
  assert.match(session.getSnapshot().targetsError, /Could not save targets/);
  assert.deepEqual(session.getSnapshot().targets, {});
});

test("saving a palette only changes the shared palette library", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  const before = structuredClone(session.getSnapshot().state);
  const palette = await session.savePalette({ name: "Ocean", colors: ["#123456", "#abcdef"] });
  assert.deepEqual(session.getSnapshot().state, before);
  assert.deepEqual(session.getSnapshot().palettes, [palette]);
  await session.savePalette({ name: "Ocean v2", colors: ["#234567", "#abcdef"] }, palette.id);
  assert.equal(session.getSnapshot().palettes[0].name, "Ocean v2");
  await session.deletePalette(palette.id);
  assert.deepEqual(session.getSnapshot().palettes, []);
});

test("reconciling runs writes only the session and leaves every View untouched", async t => {
  const f = sessionFixture(t), { session } = f;
  const original = normalize({
    selected_ids: ["gpu:gone", "offline:kept", "present"], baseline_id: "gpu:gone",
    colors: { ...workspace.defaultWorkspaceState().colors, runs: { "gpu:gone": { color: "#abcdef", fixed: true } } },
  });
  const savedView = { view_id: "a".repeat(32), name: "Saved comparison", state: original };
  f.remote.state = original;
  f.remote.views.push(savedView);
  f.palettes.push({ id: "custom:" + "b".repeat(32), name: "Ocean", colors: ["#abcdef", "#3276b8"] });
  await session.load("http://localhost/");
  session.openView(f.remote.views[0], "shared");
  const catalog = {
    runs: [{ run_id: "present", source: "local", started_at: 1, has_scalars: true }],
    sources: [
      { source_id: "local", label: "Local", kind: "local", online: true },
      { source_id: "gpu", label: "GPU", kind: "peer", online: false },
      { source_id: "offline", label: "Offline", kind: "peer", online: false },
    ],
  };
  assert.equal(availableSelection(session.getSnapshot().state, catalog.runs).size, 1);
  assert.equal(reconcileSelection(session.getSnapshot().state, catalog), null);
  assert.deepEqual(f.writes, []);
  catalog.sources[1].online = true;
  const patch = reconcileSelection(session.getSnapshot().state, catalog);
  assert.deepEqual(patch, { selected_ids: ["offline:kept", "present"], baseline_id: null });
  session.update(patch);
  assert.deepEqual(f.writes, []);
  const stored = configModule.readSession(f.storage, configModule.sessionKey("workspace-a"));
  assert.deepEqual(stored.state.selected_ids, ["offline:kept", "present"]);
  assert.equal(stored.state.baseline_id, null);
  assert.deepEqual(stored.state.colors, original.colors);
  assert.deepEqual(f.remote.state, original);
  assert.deepEqual(f.remote.views, [savedView]);
  assert.equal(f.palettes.length, 1);
  const reloaded = new WorkspaceSession(f.request, () => f.storage);
  t.after(() => reloaded.stop());
  await reloaded.load("http://localhost/");
  assert.deepEqual(reloaded.getSnapshot().state.selected_ids, ["offline:kept", "present"]);
  assert.equal(reloaded.getSnapshot().state.baseline_id, null);
  assert.deepEqual(reloaded.getSnapshot().state.colors, original.colors);
});

test("corrupt browser data falls back to shared assets but reports the read failure", async t => {
  const f = sessionFixture(t);
  f.memory.set(configModule.sessionKey("workspace-a"), "{");
  await f.session.load("http://localhost/");
  const snapshot = f.session.getSnapshot();
  assert.equal(snapshot.ready, true);
  assert.match(snapshot.storageError, /Could not restore/);
  assert.deepEqual(snapshot.localViews, []);
  assert.equal(snapshot.state.smoothing, .6);
});

test("storage write failures reject My View saves and can be retried", async t => {
  const f = sessionFixture(t), { session } = f;
  await session.load("http://localhost/");
  f.storage.setItem = () => { throw new Error("Quota exceeded"); };
  await assert.rejects(session.saveView("Cannot save", "local"), /Could not save in this browser/);
  assert.match(session.getSnapshot().storageError, /Could not save/);
  assert.deepEqual(session.getSnapshot().localViews, []);
  f.storage.setItem = (key, value) => f.memory.set(key, value);
  session.retryStorage();
  assert.equal(session.getSnapshot().storageError, "");
});
