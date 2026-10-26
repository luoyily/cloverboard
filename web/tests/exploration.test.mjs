import assert from "node:assert/strict";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";
import test, { after } from "node:test";
import ts from "typescript";

const directory = await mkdtemp(join(tmpdir(), "cloverboard-tests-"));
after(() => rm(directory, { recursive: true, force: true }));
for (const name of ["comparison", "features", "chartMath", "colors", "api", "workspace", "readout", "imageTimeline", "dataCache", "runSelection"]) {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), "utf8");
  let { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
  outputText = outputText.replace(/from "\.\/([^"]+)"/g, 'from "./$1.mjs"').replace('from "react"', `from "${pathToFileURL(resolve("node_modules/react/index.js")).href}"`);
  outputText = outputText.replace('from "d3-scale"', `from "${pathToFileURL(resolve("node_modules/d3-scale/src/index.js")).href}"`);
  await writeFile(join(directory, `${name}.mjs`), outputText);
}
const feature = await import(pathToFileURL(join(directory, "features.mjs")));
const colors = await import(pathToFileURL(join(directory, "colors.mjs")));
const readout = await import(pathToFileURL(join(directory, "readout.mjs")));
const { imagesAtStep, sampleSteps } = await import(pathToFileURL(join(directory, "imageTimeline.mjs")));
const { nearestPoint, buildPath, compareReadouts, valueDomain, fittedDomain, logTicks, visiblePoints, tailStepRange, stepAxis } = await import(pathToFileURL(join(directory, "chartMath.mjs")));
const { pooledResults, getJson } = await import(pathToFileURL(join(directory, "api.mjs")));
const { BoundedCache, RequestCache } = await import(pathToFileURL(join(directory, "dataCache.mjs")));
const { defaultWorkspaceState, normalizeState, viewStateKey, encodeSharedState, readSharedLocation } = await import(pathToFileURL(join(directory, "workspace.mjs")));
const run = (id, config = {}, source = "local") => ({ run_id: id, name: id, source, started_at: 1, has_scalars: true, last_recorded_at: null, config });

test("tail shortcuts use the whole Step span and keep small/nonzero ranges valid", () => {
  assert.deepEqual(tailStepRange([0, 100], 50), [50, 100]);
  assert.deepEqual(tailStepRange([100, 350], 20), [300, 350]);
  assert.deepEqual(tailStepRange([0, 1], 20), [0, 1]);
  assert.deepEqual(tailStepRange([10, 10], 50), [10, 10]);
});

test("Step axis leaves right-side room and shows a nearby round endpoint", () => {
  for (const width of [160, 300, 440, 1000]) {
    const axis = stepAxis(0, 999, width);
    assert.ok(width - axis.dataWidth >= 12 - 1e-9);
    assert.ok(width - axis.dataWidth <= 20 + 1e-9);
    assert.equal(axis.ticks.at(-1).step, 1000);
    assert.equal(axis.ticks.at(-1).label, "1000");
    assert.equal(axis.stepAt(width), 999);
    assert.equal(axis.stepAt(axis.dataWidth), 999);
    assert.equal(axis.stepAt(-20), 0);
    assert.ok(Math.abs(axis.stepAt(axis.scale(500)) - 500) < 1e-9);
  }
});

test("zoomed Step axis preserves exact bounds and an irregular endpoint label", () => {
  const axis = stepAxis(523, 937, 300);
  assert.equal(axis.ticks.at(-1).step, 937);
  assert.equal(axis.ticks.at(-1).label, "937");
  assert.equal(axis.stepAt(-10), 523);
  assert.equal(axis.stepAt(300), 937);
  assert.equal(Math.floor(axis.stepAt(axis.dataWidth)), 937);
  const large = stepAxis(42000, 49337, 300);
  assert.equal(large.ticks.at(-1).label, "49337");
});

test("Step labels stay apart in narrow charts and a single Step remains readable", () => {
  for (const width of [60, 120, 200, 440]) {
    for (const [minimum, maximum] of [[0, 999], [42000, 49937], [10, 11], [10, 10]]) {
      const axis = stepAxis(minimum, maximum, width);
      assert.ok(axis.ticks.length >= 1);
      for (const [index, tick] of axis.ticks.entries()) {
        assert.ok(tick.left >= -1e-9 && tick.right <= width + 1e-9);
        if (index) assert.ok(tick.left - axis.ticks[index - 1].right >= 8 - 1e-9);
      }
      assert.equal(axis.stepAt(width), maximum);
      assert.equal(axis.stepAt(0), minimum);
    }
  }
});

test("text viewing state survives normalization and sharing independently of other media", () => {
  const state = normalizeState({ media_type: "text", text_name: "debug/output", text_step: 10, image_step: 4, audio_step: 7 });
  assert.equal(state.media_type, "text");
  assert.equal(state.text_name, "debug/output");
  assert.equal(state.text_step, 10);
  assert.equal(state.image_step, 4);
  assert.equal(state.audio_step, 7);
  assert.equal(normalizeState({ text_step: 1.5 }).text_step, null);
  assert.equal(normalizeState({ text_name: 2 }).text_name, "");
  assert.ok(viewStateKey(state).includes("debug/output"));
});

test("HTTP failures show business messages and FastAPI validation details", async (t) => {
  const fetch = t.mock.method(globalThis, "fetch");
  fetch.mock.mockImplementation(async () => new Response(JSON.stringify({ detail: "Run not found" }), { status: 404 }));
  await assert.rejects(getJson("/api/scalars"), /Run not found/);
  fetch.mock.mockImplementation(async () => new Response(JSON.stringify({ detail: [{ loc: ["query", "max_points"], msg: "Input should be greater than or equal to 2" }] }), { status: 422 }));
  await assert.rejects(getJson("/api/scalars"), /max_points: Input should be greater than or equal to 2/);
  fetch.mock.mockImplementation(async () => new Response("unavailable", { status: 503 }));
  await assert.rejects(getJson("/api/scalars"), /503/);
});

test("progressive subscribers share queued requests and respect the fetch limit", async () => {
  const cache = new RequestCache(8, 2), releases = [], started = [];
  const load = (id) => cache.load(id, () => { started.push(id); return new Promise((resolve) => releases.push(() => resolve(id))); });
  const a = load("local"), duplicate = load("local"), b = load("peer"), c = load("later");
  assert.equal(a, duplicate);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(started, ["local", "peer"]);
  releases[0]();
  assert.equal(await a, "local");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(started, ["local", "peer", "later"]);
  releases[1](); releases[2]();
  assert.deepEqual(await Promise.all([duplicate, b, c]), ["local", "peer", "later"]);
  assert.equal(await cache.load("local", () => { throw Error("must use cache"); }), "local");
});

test("failed requests can retry and refresh identities never reuse stale samples", async () => {
  const cache = new RequestCache(2, 1);
  await assert.rejects(cache.load("run:tick0", async () => { throw Error("offline"); }));
  assert.equal(await cache.load("run:tick0", async () => 1), 1);
  assert.equal(await cache.load("run:tick1", async () => 2), 2);
});

test("overlapping metric batches share individual results and retry failures within the concurrency limit", async () => {
  const cache = new RequestCache(16, 1), started = [], releases = [];
  const action = async (keys) => { started.push(keys); await new Promise((resolve) => releases.push(resolve)); return keys.map((key) => key.toUpperCase()); };
  const first = cache.loadMany(["loss", "psnr", "loss"], action);
  const second = cache.loadMany(["psnr", "ssim"], action);
  assert.equal(first[0], first[2]);
  assert.equal(first[1], second[0]);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(started, [["loss", "psnr"]]);
  releases[0]();
  assert.deepEqual(await Promise.all(first), ["LOSS", "PSNR", "LOSS"]);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(started, [["loss", "psnr"], ["ssim"]]);
  releases[1]();
  assert.deepEqual(await Promise.all(second), ["PSNR", "SSIM"]);
  assert.deepEqual(await Promise.all(cache.loadMany(["loss", "ssim"], () => { throw Error("must reuse entries"); })), ["LOSS", "SSIM"]);
  const failed = cache.loadMany(["offline", "missing"], async () => { throw Error("offline"); });
  assert.deepEqual((await Promise.allSettled(failed)).map((result) => result.status), ["rejected", "rejected"]);
  assert.deepEqual(await Promise.all(cache.loadMany(["offline", "missing"], async () => [1, 2])), [1, 2]);
});

test("local zoom retains edge samples and gaps without extending the recorded range", () => {
  const points = [{ step: 0, value: 1 }, { step: 10, value: null }, { step: 20, value: 3 }, { step: 30, value: 2 }];
  assert.deepEqual(visiblePoints(points, 12, 18), points.slice(1, 3));
  assert.deepEqual(visiblePoints(points, 10, 20), points);
  assert.deepEqual(visiblePoints(points, 31, 40), []);
  assert.deepEqual(visiblePoints(points, -20, -1), []);
  const path = buildPath(visiblePoints(points, 5, 25), (v) => v, (v) => v);
  assert.equal((path.match(/M/g) ?? []).length, 2);
});

test("readouts prefer vertical space by default and retain horizontal preference", () => {
  const anchor = { left: 220, right: 620, top: 100, bottom: 380 };
  const popup = { width: 450, height: 180 };
  const below = readout.placeReadout(anchor, popup, { width: 1200, height: 800 });
  assert.ok(below.top > anchor.bottom);
  const right = readout.placeReadout(anchor, popup, { width: 1200, height: 800 }, "horizontal");
  assert.ok(right.left > anchor.right);
  const left = readout.placeReadout({ ...anchor, left: 700, right: 1100 }, popup, { width: 1200, height: 800 }, "horizontal");
  assert.ok(left.left + popup.width < 700);
});

test("readouts fall back above, across orientations, and within viewport bounds", () => {
  const popup = { width: 450, height: 180 }, viewport = { width: 1200, height: 800 };
  const low = { left: 220, right: 620, top: 500, bottom: 780 };
  const above = readout.placeReadout(low, popup, viewport);
  assert.ok(above.top + popup.height < low.top);
  const tall = { left: 220, right: 620, top: 20, bottom: 780 };
  assert.ok(readout.placeReadout(tall, popup, viewport).left > tall.right);
  const wide = { left: 12, right: 1188, top: 100, bottom: 380 };
  assert.ok(readout.placeReadout(wide, popup, viewport, "horizontal").top > wide.bottom);
  const cramped = { left: 12, right: 1188, top: 150, bottom: 700 };
  const limited = readout.placeReadout(cramped, popup, viewport);
  assert.ok(limited.maxHeight >= 80 && limited.maxHeight < popup.height);
  assert.ok(limited.top >= 12 && limited.top + limited.maxHeight <= viewport.height - 12);
  const full = readout.placeReadout({ left: 12, right: 1188, top: 20, bottom: 780 }, popup, viewport);
  assert.equal(full.docked, true);
  for (const preference of ["vertical", "horizontal"]) {
    const mobile = readout.placeReadout(wide, popup, { width: 320, height: 720 }, preference);
    assert.ok(mobile.docked && mobile.left >= 0 && mobile.left + mobile.maxWidth <= 320);
  }
});

test("chart legend and hover placement settings support defaults and shared state", () => {
  const defaults = normalizeState({});
  assert.equal(defaults.show_chart_legend, true);
  assert.equal(defaults.tooltip_placement, "vertical");
  const invalid = normalizeState({ show_chart_legend: null, tooltip_placement: "other" });
  assert.equal(invalid.show_chart_legend, true);
  assert.equal(invalid.tooltip_placement, "vertical");
  const state = normalizeState({ show_chart_legend: false, tooltip_placement: "horizontal" });
  const restored = readSharedLocation(`http://localhost/#state=${encodeSharedState(state)}`).state;
  assert.equal(restored.show_chart_legend, false);
  assert.equal(restored.tooltip_placement, "horizontal");
  assert.notEqual(viewStateKey(state), viewStateKey(defaults));
  const old = { ...defaultWorkspaceState() };
  delete old.show_chart_legend;
  delete old.tooltip_placement;
  const legacy = readSharedLocation(`http://localhost/#state=${Buffer.from(JSON.stringify({ version: 1, state: old })).toString("base64url")}`).state;
  assert.equal(legacy.show_chart_legend, true);
  assert.equal(legacy.tooltip_placement, "vertical");
});

test("fast media results publish before a slow peer finishes", async () => {
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const seen = [];
  const all = pooledResults(["local", "peer"], async (name) => name === "peer" ? await slow : name, 2, (result, index) => seen.push([index, result.value]));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(seen, [[0, "local"]]);
  release("peer");
  assert.equal((await all)[1].value, "peer");
});

test("bounded data cache reuses visited pages without crossing refresh identities", () => {
  const cache = new BoundedCache(2);
  cache.set("workspace:refresh0:step0", { step: 0 });
  cache.set("workspace:refresh0:step1", { step: 1 });
  assert.equal(cache.get("workspace:refresh1:step0"), undefined);
  assert.equal(cache.get("workspace:refresh0:step0").step, 0);
  cache.set("workspace:refresh0:step2", { step: 2 });
  assert.equal(cache.get("workspace:refresh0:step1"), undefined);
  assert.equal(cache.get("workspace:refresh0:step0").step, 0);
});

test("tag filters preserve exact membership for any, all, none and empty modes", () => {
  const tagged = { ...run("tagged"), tags: ["baseline", "reviewed"] };
  assert.equal(feature.matchesTagFilter(tagged, ["baseline"], "any"), true);
  assert.equal(feature.matchesTagFilter(tagged, ["base"], "any"), false);
  assert.equal(feature.matchesTagFilter(tagged, ["BASELINE"], "any"), false);
  assert.equal(feature.matchesTagFilter(tagged, ["baseline", "partial"], "all"), false);
  assert.equal(feature.matchesTagFilter(tagged, ["partial"], "none"), true);
  assert.equal(feature.matchesTagFilter(run("untagged"), [], "empty"), true);
  assert.equal(feature.matchesTagFilter(tagged, ["baseline", "reviewed"], "all"), true);
  assert.equal(feature.matchesTagFilter(tagged, ["baseline"], "none"), false);
  assert.equal(feature.matchesTagFilter(tagged, [], "empty"), false);
});

test("media and table settings survive views and shared state", () => {
  const state = normalizeState({ ...defaultWorkspaceState(), panel: "images", media_type: "audio", audio_step: 0, table_decimals: 7, show_tags_column: true, tag_filter: ["reviewed"], tag_filter_match: "all", table_show_steps: false, table_delta: "hidden", baseline_id: "r1", targets: { loss: { value: .2, direction: "lte" } } });
  const shared = readSharedLocation(`http://localhost/#state=${encodeSharedState(state)}`);
  assert.equal(shared.state.media_type, "audio");
  assert.equal(shared.state.audio_step, 0);
  assert.equal(shared.state.table_decimals, 7);
  assert.deepEqual(shared.state.tag_filter, ["reviewed"]);
  for (const key of ["table_show_steps", "table_delta", "baseline_id", "targets"]) assert.deepEqual(shared.state[key], state[key]);
  assert.notEqual(viewStateKey(state), viewStateKey({ ...state, table_show_steps: true }));
});

test("table timestamps use padded local date and time without timezone text", () => {
  const local = new Date(2026, 0, 2, 3, 4, 5);
  assert.equal(readout.formatTableTime(local.getTime() / 1000), "2026-01-02 03:04:05");
  for (const value of [undefined, null, NaN, Infinity, 1e20]) assert.equal(readout.formatTableTime(value), "—");
  assert.match(readout.formatTableTime(0), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
});

test("image timeline never substitutes future frames and preserves same-step batches", () => {
  const images = [{ image_id: "a", name: "pred", step: 10 }, { image_id: "b", name: "pred", step: 20 }, { image_id: "c", name: "pred", step: 20 }, { image_id: "d", name: "other", step: 30 }];
  assert.deepEqual(imagesAtStep(images, "pred", 5), []);
  assert.deepEqual(imagesAtStep(images, "pred", 15).map((image) => image.image_id), ["a"]);
  assert.deepEqual(imagesAtStep(images, "pred", null).map((image) => image.image_id), ["b", "c"]);
  const state = normalizeState({ ...defaultWorkspaceState(), panel: "images", image_name: "pred", image_step: 0 });
  assert.equal(state.panel, "images");
  assert.equal(state.image_step, 0);
  assert.notEqual(viewStateKey(state), viewStateKey({ ...state, image_step: null }));
});

test("elapsed uses global origin, preserves pauses and missing timestamps", () => {
  const timed = { ...run("timed"), time_origin: 100, time_origin_kind: "step_zero" };
  assert.equal(readout.elapsedTime({ step: 400, value: .2, wall_time: 4700 }, timed), 4600);
  assert.equal(readout.elapsedTime({ step: 400, value: .2 }, timed), null);
  assert.equal(readout.elapsedTime({ step: 400, value: .2, wall_time: 4700 }, run("legacy")), null);
  assert.equal(readout.formatDuration(4600), "1h 16m 40s");
  assert.equal(readout.formatDuration(-10), "−10s");
  assert.equal(readout.formatDuration(null), "—");
  assert.equal(readout.formatWallTime(Infinity), "—");
  assert.equal(readout.timeOriginLabel({ ...timed, time_origin_kind: "first_sample" }), "Since first sample");
});

test("target delta is signed Raw for either direction including a zero target", () => {
  const point = { step: 10, value: -.2, smoothed: .8 };
  assert.equal(feature.targetDelta(point, { value: 0, direction: "gte" }), -.2);
  assert.equal(feature.targetDelta(point, { value: 0, direction: "lte" }), -.2);
  assert.equal(feature.targetMet(point, { value: 0, direction: "lte" }), true);
  assert.equal(feature.targetDelta({ step: 10, value: null }, { value: 0, direction: "lte" }), null);
  assert.deepEqual(feature.deltaColumns("baseline", true, true), { baseline: true, target: false });
  assert.deepEqual(feature.deltaColumns("baseline", false, true), { baseline: false, target: false });
  assert.deepEqual(feature.deltaColumns("both", true, true), { baseline: true, target: true });
  assert.deepEqual(feature.deltaColumns("hidden", true, true), { baseline: false, target: false });
  assert.deepEqual(feature.deltaColumns("target", true, false), { baseline: false, target: false });
  assert.deepEqual(feature.deltaColumns("target", true, true), { baseline: false, target: true });
});

test("color picker round-trips primaries, gray, black and arbitrary HEX", () => {
  for (const color of ["#000000", "#ffffff", "#808080", "#ff0000", "#00ff00", "#0000ff", "#3276b8", "#c98ba5"]) assert.equal(colors.hsvToHex(colors.hexToHsv(color)), color);
  assert.equal(colors.hsvToHex({ h: 360, s: 1, v: 1 }), "#ff0000");
});

test("time, difference and fixed warmup preferences survive view normalization", () => {
  const state = normalizeState({ ...defaultWorkspaceState(), tooltip_time: "wall", tooltip_delta: "both", chart_warmup: { zero: 0, negative: -10, loss: 379, bad: 1.5, huge: 1e20 } });
  assert.deepEqual(state.chart_warmup, { zero: 0, negative: -10, loss: 379 });
  assert.equal(state.tooltip_time, "wall");
  assert.equal(state.tooltip_delta, "both");
  assert.notEqual(viewStateKey(state), viewStateKey({ ...state, chart_warmup: {} }));
  assert.equal(normalizeState({}).tooltip_time, "hidden");
  assert.equal(normalizeState({}).tooltip_delta, "both");
  assert.equal(normalizeState({ tooltip_delta: "auto" }).tooltip_delta, "both");
  for (const mode of ["baseline", "target", "both", "hidden"]) {
    const explicit = { ...state, tooltip_delta: mode };
    assert.equal(readSharedLocation(`http://localhost/#state=${encodeSharedState(explicit)}`).state.tooltip_delta, mode);
  }
});

test("warmup inheritance distinguishes explicit Auto, zero cutoff and global settings in saved views", () => {
  const state = normalizeState({ y_warmup: 1000, chart_warmup: { auto: null, zero: 0, own: 500, bad: 1.5 } });
  assert.deepEqual(state.chart_warmup, { auto: null, zero: 0, own: 500 });
  assert.equal(feature.effectiveWarmup(state.chart_warmup.inherited, state.y_warmup), 1000);
  assert.equal(feature.effectiveWarmup(state.chart_warmup.auto, state.y_warmup), undefined);
  assert.equal(feature.effectiveWarmup(state.chart_warmup.zero, state.y_warmup), 0);
  assert.equal(feature.effectiveWarmup(state.chart_warmup.own, state.y_warmup), 500);
  const restored = readSharedLocation(`http://localhost/#state=${encodeSharedState(state)}`).state;
  assert.equal(restored.y_warmup, 1000);
  assert.equal(restored.chart_warmup.auto, null);
  assert.equal(normalizeState({ y_warmup: 1e20 }).y_warmup, null);
  const zooms = { inherited: { x_min: 50, x_max: 200, y_min: 1, y_max: 5 }, auto: { x_min: 0, x_max: 10, y_min: 2, y_max: 3 } };
  assert.deepEqual(feature.clearYZoom(zooms, (metric) => !Object.hasOwn(state.chart_warmup, metric)), { inherited: { x_min: 50, x_max: 200, y_min: null, y_max: null }, auto: zooms.auto });
  assert.equal(zooms.inherited.y_min, 1);
});

test("warmup fitting is immediate, preserves EMA and falls back to Auto for empty or nonpositive log samples", () => {
  const points = [{ step: 0, value: 1000, smoothed: 1000 }, { step: 10, value: 2, smoothed: 4 }, { step: 20, value: 3, smoothed: 3.5 }];
  assert.deepEqual(fittedDomain(points, true, false, 10).domain, valueDomain([2, 4, 3, 3.5], false));
  assert.deepEqual(fittedDomain(points, false, false, 10).domain, valueDomain([2, 3], false));
  const precise = fittedDomain(points, false, false, 10, [{ minimum: 1, maximum: 6 }]);
  assert.deepEqual(precise.domain, valueDomain([2, 3, 1, 6], false));
  const empty = fittedDomain(points, true, false, 21);
  assert.equal(empty.empty, true);
  assert.deepEqual(empty.domain, fittedDomain(points, true, false).domain);
  const nonpositive = [{ step: 0, value: 5 }, { step: 10, value: -1 }];
  assert.equal(fittedDomain(nonpositive, false, true, 10).empty, true);
  assert.deepEqual(fittedDomain(nonpositive, false, true, 10).domain, valueDomain([5], true));
  assert.equal(points[0].value, 1000);
});

test("views keep explicit selection and viewing defaults", () => {
  const state = { ...defaultWorkspaceState(), selected_ids: ["lr-1"] };
  assert.notEqual(viewStateKey(state), viewStateKey({ ...state, selected_ids: ["lr-1", "lr-2"] }));
  const defaults = normalizeState({ version: 1, selected_ids: [] });
  assert.deepEqual(defaults.selected_ids, []);
  assert.equal(defaults.colors.mode, "random");
  assert.equal(defaults.refresh_interval, 5);
  assert.equal(defaults.tooltip_sort, "value");
  assert.equal(normalizeState({ refresh_interval: 0 }).refresh_interval, 0);
  assert.equal(normalizeState({ refresh_interval: -1 }).refresh_interval, 5);
});

test("hover order follows the rendered values, keeps baseline first and supports fixed order", () => {
  const rows = [
    { run: run("raw-high"), point: { step: 10, value: 9, smoothed: 2 } },
    { run: run("missing"), point: { step: 10, value: null, smoothed: null } },
    { run: run("ema-high"), point: { step: 10, value: 3, smoothed: 8 } },
    { run: run("baseline"), point: { step: 10, value: 1, smoothed: 1 } },
  ];
  const ordered = (smoothed, baseline, order) => [...rows].sort((a, b) => compareReadouts(a, b, smoothed, baseline, order)).map((row) => row.run.run_id);
  assert.deepEqual(ordered(false, null, "value"), ["raw-high", "ema-high", "baseline", "missing"]);
  assert.deepEqual(ordered(true, "baseline", "value"), ["baseline", "ema-high", "raw-high", "missing"]);
  assert.deepEqual(ordered(true, "baseline", "list"), ["baseline", "raw-high", "missing", "ema-high"]);
  assert.deepEqual(ordered(false, null, "list"), rows.map((row) => row.run.run_id));
});

test("12 and 20 stable identity colors are unique and visible in both themes", () => {
  const runs = Array.from({ length: 20 }, (_, i) => run(`r${i}`));
  const registry = colors.extendRegistry({ version: 1, seed: "test-seed", slots: {} }, runs.map(feature.runColorKey));
  const extended = colors.extendRegistry(registry, [feature.runColorKey(run("older")), feature.runColorKey(run("r1", {}, "peer"))]);
  for (const r of runs) assert.equal(extended.slots[feature.runColorKey(r)], registry.slots[feature.runColorKey(r)]);
  for (const theme of ["light", "dark"]) {
    const resolved = colors.resolveColors(runs, registry, feature.defaultColors(), theme);
    assert.equal(new Set(resolved.values()).size, 20);
    assert.ok([...resolved.values()].every((color) => colors.contrast(color, theme === "light" ? "#ffffff" : "#222831") >= 3));
    const reversed = colors.resolveColors([...runs].reverse(), extended, feature.defaultColors(), theme);
    for (const r of runs) assert.equal(resolved.get(r.run_id), reversed.get(r.run_id));
    const first12 = [...resolved.values()].slice(0, 12);
    const minimum = Math.min(...first12.flatMap((a, i) => first12.slice(i + 1).map((b) => colors.colorDistance(a, b))));
    assert.ok(minimum > .035, `Minimum OKLab distance ${minimum}`);
  }
});

test("manual colors lock; reassign affects only selected unlocked runs and can be undone", () => {
  const runs = [run("a"), run("b"), run("c")], registry = colors.extendRegistry({ version: 1, seed: "lock", slots: {} }, runs.map(feature.runColorKey));
  const settings = { ...feature.defaultColors(), overrides: { [feature.runColorKey(runs[0])]: "#9966aa" } };
  const before = colors.resolveColors(runs, registry, settings, "light");
  const next = colors.reassignColors(runs, new Set(["a", "b"]), registry, settings);
  const after = colors.resolveColors(runs, registry, next, "light");
  assert.equal(after.get("a"), before.get("a")); assert.equal(after.get("c"), before.get("c")); assert.notEqual(after.get("b"), before.get("b"));
  assert.deepEqual(colors.resolveColors(runs, registry, settings, "light"), before);
});

test("continuous colors keep a fixed domain, match values and separate missing/non-numeric", () => {
  const runs = [run("a", { lr: 1 }), run("b", { lr: 1 }), run("c", { lr: "1" }), run("missing")];
  const registry = colors.extendRegistry({ version: 1, seed: "semantic", slots: {} }, runs.map(feature.runColorKey));
  const settings = { ...feature.defaultColors(), mode: "continuous", field: "lr", domain: [0, 2] };
  const before = colors.resolveColors(runs, registry, settings, "light");
  const after = colors.resolveColors([...runs, run("new", { lr: 100 })], registry, settings, "light");
  assert.equal(before.get("a"), before.get("b")); assert.equal(before.get("a"), after.get("a"));
  assert.equal(before.get("c"), before.get("missing")); assert.notEqual(before.get("a"), before.get("missing"));
});

test("parameter families stay stable when runs and new categories arrive", () => {
  const runs = [run("a", { group: false }), run("b", { group: 0 }), run("c", { group: null }), run("missing")];
  const keys = (items) => items.flatMap((r) => [feature.runColorKey(r), feature.categoryKey("group", feature.parameterValue(r, "group"))]);
  const registry = colors.extendRegistry({ version: 1, seed: "family", slots: {} }, keys(runs));
  const settings = { ...feature.defaultColors(), mode: "parameter", field: "group" };
  const before = colors.resolveColors(runs, registry, settings, "light");
  const nextRuns = [run("older", { group: "new" }), ...runs].reverse();
  const after = colors.resolveColors(nextRuns, colors.extendRegistry(registry, keys(nextRuns)), settings, "light");
  for (const r of runs) assert.equal(before.get(r.run_id), after.get(r.run_id));
  assert.notEqual(before.get("a"), before.get("b")); assert.notEqual(before.get("b"), before.get("c"));
  assert.equal(before.get("missing"), "#747e8b");
});

test("Top-K uses exact latest/best and never ranks invalid values", () => {
  const runs = [run("a"), run("b"), run("c")];
  const summaries = new Map([["a\0loss", { latest: { step: 9, value: .7 }, minimum: .1 }], ["b\0loss", { latest: { step: 3, value: .3 }, minimum: .2 }], ["c\0loss", { latest: { step: 10, value: null }, minimum: null }]]);
  const settings = { enabled: true, metric: "loss", direction: "min", k: 1, readout: "latest" };
  assert.deepEqual([...feature.topRunIds(runs, summaries, settings)], ["b"]);
  assert.deepEqual([...feature.topRunIds(runs, summaries, { ...settings, readout: "best" })], ["a"]);
});

test("hover preserves actual samples and does not bridge invalid values", () => {
  const points = [{ step: 0, value: 0 }, { step: 5, value: null }, { step: 10, value: 2 }];
  assert.equal(nearestPoint(points, -1), undefined); assert.equal(nearestPoint(points, 11), points.at(-1));
  assert.equal(nearestPoint(points, 4).value, null);
  assert.equal(feature.targetMet({ step: 9, value: null }, { value: .8, direction: "gte" }), null);
  assert.equal(feature.targetMet({ step: 9, value: .9 }, { value: .8, direction: "gte" }), true);
  const path = buildPath(points, (v) => v, (v) => v);
  assert.equal((path.match(/M/g) ?? []).length, 2); assert.equal(path.includes("L"), false);
});

test("hover delta compares nearest raw samples on different grids using a short run's final recorded point", () => {
  const baseline = [{ step: 0, value: 1 }, { step: 10, value: .75, smoothed: 4 }, { step: 20, value: .5 }, { step: 30, value: .25 }];
  const partial = [{ step: 0, value: .875 }, { step: 7, value: .625 }, { step: 15, value: .5, smoothed: 9 }];
  const point = nearestPoint(partial, 12), base = nearestPoint(baseline, 12);
  assert.equal(point.step, 15);
  assert.equal(base.step, 10);
  assert.equal(feature.baselineDelta(point, base), -.25);
  assert.equal(feature.baselineDelta(base, point), .25);
  assert.equal(feature.baselineDelta(nearestPoint(partial, 15), base), -.25);
  assert.equal(feature.baselineDelta(nearestPoint(partial, 15.1), base), -.25);
  assert.equal(feature.baselineDelta(point, nearestPoint(baseline, -1)), null);
  for (const value of [null, NaN, Infinity]) {
    assert.equal(feature.baselineDelta({ step: 15, value }, base), null);
    assert.equal(feature.baselineDelta(point, { step: 10, value }), null);
  }
});


test("log domain excludes nonpositive values and paths break instead of clamping", () => {
  const [low, high] = valueDomain([-10, 0, .001, 100, NaN], true);
  assert.ok(low > 0 && low < .001 && high > 100);
  assert.deepEqual(valueDomain([-4, 0], true), [1, 10]);
  const points = [{ step: 0, value: 1 }, { step: 1, value: 0 }, { step: 2, value: 10 }, { step: 3, value: -1 }, { step: 4, value: 100 }];
  const path = buildPath(points, (v) => v, Math.log10, false, true);
  assert.equal((path.match(/M/g) ?? []).length, 3);
  assert.ok(!/NaN|Infinity/.test(path));
  assert.equal(buildPath(points.map((point) => ({ ...point, smoothed: 10 })), (v) => v, Math.log10, true, true).split("M").length, 2);
});

test("log ticks cover narrow and wide domains without expanding the visible range", () => {
  const [low, high] = valueDomain([.2, .94], true);
  assert.ok(high < 1.2);
  assert.deepEqual(logTicks(low, high), [.2, .5, 1]);
  assert.deepEqual(logTicks(.88, .99), [.88, .99]);
  const ticks = logTicks(1e-8, 1e8);
  assert.equal(ticks.length, 6);
  assert.equal(ticks[0], 1e-8);
  assert.equal(ticks.at(-1), 1e8);
});

test("sampled image timeline keeps endpoints and stays within the selected budget", () => {
  const steps = Array.from({ length: 10000 }, (_, index) => index * 7);
  const sampled = sampleSteps(steps, 50);
  assert.equal(sampled.length, 50);
  assert.equal(sampled[0], 0);
  assert.equal(sampled.at(-1), steps.at(-1));
  assert.equal(new Set(sampled).size, 50);
});

test("share state round-trips Unicode, empty selections and all new settings", () => {
  const state = { ...defaultWorkspaceState(), selected_ids: [], search: "测试 🌿", y_scale: "log", chart_scales: { "validation/score": "linear" }, collapsed_groups: ["train"], image_timeline_samples: 50, images_per_step: 4 };
  const encoded = encodeSharedState(state);
  assert.deepEqual(readSharedLocation(`http://localhost:6006/?token=test#state=${encoded}`).state, state);
  assert.deepEqual(readSharedLocation("http://localhost:6006/?view=trial"), { viewId: "trial" });
  assert.throws(() => readSharedLocation("http://localhost/#state=bad"), /invalid/);
  assert.throws(() => encodeSharedState({ ...state, search: "x".repeat(20000) }), /too large/);
});

test("expanded charts survive links and view comparison without depending on list order", () => {
  const state = normalizeState({ ...defaultWorkspaceState(), expanded_charts: ["train/loss", "validation/score", "train/loss", 12] });
  assert.deepEqual(state.expanded_charts, ["train/loss", "validation/score"]);
  assert.equal(viewStateKey(state), viewStateKey({ ...state, expanded_charts: [...state.expanded_charts].reverse() }));
  assert.notEqual(viewStateKey(state), viewStateKey({ ...state, expanded_charts: [] }));
  assert.deepEqual(readSharedLocation(`http://localhost/#state=${encodeSharedState(state)}`).state.expanded_charts, state.expanded_charts);
  assert.deepEqual(normalizeState({ expanded_charts: null }).expanded_charts, []);
});

test("image API requests have bounded client concurrency", async () => {
  const { pooledResults } = await import(pathToFileURL(join(directory, "api.mjs")));
  let active = 0, maximum = 0;
  const results = await pooledResults(Array.from({ length: 30 }, (_, i) => i), async (value) => {
    maximum = Math.max(maximum, ++active);
    await new Promise((resolve) => setTimeout(resolve, 1));
    active--;
    if (value === 7) throw new Error("offline");
    return value * 2;
  });
  assert.ok(maximum <= 4);
  assert.equal(results[7].status, "rejected");
  assert.deepEqual(results[29], { status: "fulfilled", value: 58 });
});

const selection = await import(pathToFileURL(join(directory, "runSelection.mjs")));

test("initial recent selection skips empty runs and ordering remains stable", () => {
  const items = [
    { ...run("empty"), started_at: 9, has_scalars: false },
    { ...run("trial10"), started_at: 2, has_scalars: true, last_recorded_at: 4 },
    { ...run("trial2"), started_at: 3, has_scalars: true, last_recorded_at: 5 },
    { ...run("trial1"), started_at: 1, has_scalars: true, last_recorded_at: null },
    { ...run("older"), started_at: 0, has_scalars: true, last_recorded_at: 6 },
  ];
  assert.deepEqual(selection.recentRunIds(items), ["trial2", "trial10", "trial1"]);
  assert.deepEqual(selection.sortRuns(items.slice(1, 4), "name_asc").map(r => r.run_id), ["trial1", "trial2", "trial10"]);
  assert.deepEqual(selection.sortRuns(items.slice(1, 4), "name_desc").map(r => r.run_id), ["trial10", "trial2", "trial1"]);
  assert.equal(selection.sortRuns(items, "started_desc")[0].run_id, "empty");
  assert.equal(selection.sortRuns(items, "started_asc")[0].run_id, "older");
});

test("bulk actions preserve hidden selection and clear an unchecked baseline", () => {
  const state = { ...defaultWorkspaceState(), selected_ids: ["visible", "hidden"], baseline_id: "visible" };
  const patch = selection.selectionPatch(state, new Set(state.selected_ids), ["visible"], false);
  assert.deepEqual(patch.selected_ids, ["hidden"]);
  assert.equal(patch.baseline_id, null);
  const clear = selection.replaceSelection(state, []);
  assert.deepEqual(clear.selected_ids, []);
  assert.equal(clear.baseline_id, null);
});

test("list sorting, compact charts, metric filtering and table time survive view links", () => {
  const state = { ...defaultWorkspaceState(), run_sort: "name_desc", search: "SwinIR|HAT", search_regex: true, chart_density: "compact", metric_filter: "psnr", table_time: "started_at", panel: "table" };
  const restored = readSharedLocation(`http://localhost/#state=${encodeSharedState(state)}`).state;
  for (const key of ["run_sort", "search", "search_regex", "chart_density", "metric_filter", "table_time", "panel"]) assert.equal(restored[key], state[key]);
  assert.notEqual(viewStateKey(state), viewStateKey({ ...state, metric_filter: "" }));
});

test("run search supports literal punctuation and case-insensitive regex across names, IDs and sources", () => {
  const item = { ...run("run-id"), name: "SwinIR_SRx2 (test)[1].log", source_label: "Peer+1" };
  assert.equal(selection.createRunSearch("(test)[1].", false).matches(item), true);
  assert.equal(selection.createRunSearch("peer+1", false).matches(item), true);
  assert.equal(selection.createRunSearch("^run-id$", true).matches(item), true);
  assert.equal(selection.createRunSearch("SRx[24]", true).matches(item), true);
  const alternatives = selection.createRunSearch("swinir|hat", true);
  assert.equal(alternatives.matches(item), true);
  assert.equal(alternatives.matches(item), true);
  assert.equal(alternatives.matches({ ...run("other"), name: "HAT_SRx4" }), true);
  assert.equal(alternatives.matches(run("unrelated")), false);
  assert.equal(selection.createRunSearch("swinir|hat", false).matches(item), false);
  assert.equal(selection.createRunSearch("", true).matches(item), true);
});

test("incomplete regular expressions return an error instead of throwing", () => {
  for (const pattern of ["[", "(", "\\"]) assert.ok(selection.createRunSearch(pattern, true).error);
  assert.equal(selection.createRunSearch("[", false).error, null);
});

test("cancelled bulk loading stops scheduling new requests while shared work may finish", async () => {
  const controller = new AbortController(), started = [];
  let release;
  const first = new Promise(resolve => { release = resolve; });
  const pending = pooledResults([1, 2, 3, 4], async item => { started.push(item); return await first; }, 1, undefined, controller.signal);
  await new Promise(resolve => setImmediate(resolve));
  controller.abort(); release("complete");
  const results = await pending;
  assert.deepEqual(started, [1]);
  assert.equal(results[0].status, "fulfilled");
  assert.ok(results.slice(1).every(result => result.status === "rejected"));
});
