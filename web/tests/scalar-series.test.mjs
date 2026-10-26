import assert from "node:assert/strict";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test, { after } from "node:test";
import ts from "typescript";

const directory = await mkdtemp(join(tmpdir(), "cloverboard-series-"));
after(() => rm(directory, { recursive: true, force: true }));
for (const name of ["api", "dataCache", "scalarSeries"]) {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
  await writeFile(join(directory, `${name}.mjs`), outputText.replace(/from "\.\/([^"]+)"/g, 'from "./$1.mjs"'));
}
const { ScalarSeriesStore, loadScalarSeries } = await import(pathToFileURL(join(directory, "scalarSeries.mjs")));
const { RequestCache } = await import(pathToFileURL(join(directory, "dataCache.mjs")));
const { seriesKey } = await import(pathToFileURL(join(directory, "api.mjs")));
const tick = () => new Promise((resolve) => setImmediate(resolve));
const version = (smoothing = .6, refreshTick = 0) => JSON.stringify([smoothing, refreshTick]);
const points = (value) => [{ step: 0, value }];
const request = (runId, metric) => ({ runId, metric });

function harness(load = async (runId, metrics) => metrics.map((metric) => ({ metric, points: points(runId.length + metric.length) }))) {
  const store = new ScalarSeriesStore(), cache = new RequestCache(128), frames = new Set(), publications = [], calls = [];
  let errors = 0;
  return { store, cache, publications, calls, frames, get errors() { return errors; },
    start(requests, overrides = {}) {
      return loadScalarSeries({ store, cache, selectedIds: new Set(requests.map((r) => r.runId)), requests, smoothing: .6, refreshTick: 0,
        load: (...args) => { calls.push(args); return load(...args); },
        schedule: (publish) => { frames.add(publish); return () => frames.delete(publish); },
        onPublish: (snapshot, pending) => publications.push({ snapshot, pending }), onError: () => errors++, ...overrides,
      });
    },
    flush() { for (const publish of [...frames]) { frames.delete(publish); publish(); } },
  };
}

test("series snapshots isolate metrics and preserve references for unchanged data", () => {
  const store = new ScalarSeriesStore(), a = points(1), b = points(2);
  store.apply([{ ...request("a", "loss"), points: a }, { ...request("b", "psnr"), points: b }], version());
  const before = store.read();
  store.apply([{ ...request("a", "loss"), points: a }], version(.8));
  store.retain(new Set(["a", "b"]));
  assert.equal(store.read(), before);
  assert.ok(store.has(request("a", "loss"), version(.8)));
  assert.equal(store.has(request("a", "loss"), version()), false);
  store.apply([{ ...request("a", "loss"), points: points(3) }], version(.8));
  assert.notEqual(store.read().get("loss"), before.get("loss"));
  assert.equal(store.read().get("psnr"), before.get("psnr"));
  assert.equal(before.get("loss").get("a"), a);
  const retained = store.read().get("loss");
  store.retain(new Set(["a"]));
  assert.equal(store.read().get("loss"), retained);
  assert.equal(store.read().has("psnr"), false);
  assert.equal(store.has(request("b", "psnr"), version()), false);
});

test("220 active curves survive LRU eviction and fold/reopen without requests or republishing samples", async () => {
  const h = harness();
  const requests = Array.from({ length: 20 }, (_, r) => Array.from({ length: 11 }, (_, m) => request(`run-${r}`, `metric-${m}`))).flat();
  let stop = h.start(requests);
  await tick();
  assert.equal(h.frames.size, 1);
  assert.equal(h.publications.length, 1);
  h.flush();
  assert.equal(h.publications.length, 2);
  assert.equal(h.publications.at(-1).pending.size, 0);
  assert.equal(h.store.read().size, 11);
  assert.equal(h.calls.length, 20);
  const snapshot = h.store.read();
  stop();
  stop = h.start(requests.filter((r) => r.metric === "metric-0"));
  await tick(); h.flush(); stop();
  stop = h.start(requests);
  await tick(); h.flush(); stop();
  assert.equal(h.calls.length, 20);
  assert.equal(h.store.read(), snapshot);
  assert.equal(h.publications.at(-1).pending.size, 0);
});

test("smoothing and refresh invalidate freshness, while folded curves retain their displayed version", async () => {
  const h = harness(async (id, metrics, smoothing) => metrics.map((metric) => ({ metric, points: metric === "empty" ? [] : points(smoothing) })));
  const requests = [request("a", "loss"), request("a", "psnr"), request("a", "empty")];
  let stop = h.start(requests); await tick(); h.flush(); stop();
  const oldPsnr = h.store.read().get("psnr");
  stop = h.start([requests[0]], { smoothing: .8 }); await tick(); h.flush(); stop();
  assert.equal(h.store.read().get("psnr"), oldPsnr);
  assert.equal(h.store.read().get("loss").get("a")[0].value, .8);
  assert.equal(h.store.has(requests[1], version(.8)), false);
  stop = h.start(requests, { smoothing: .8 }); await tick(); h.flush(); stop();
  assert.deepEqual(h.calls.at(-1)[1], ["psnr", "empty"]);
  stop = h.start(requests, { smoothing: .8 }); await tick(); h.flush(); stop();
  assert.equal(h.calls.length, 3);
  stop = h.start(requests, { smoothing: .8, refreshTick: 1 });
  assert.equal(h.publications.at(-1).pending.size, 3);
  await tick(); h.flush(); stop();
  assert.equal(h.calls.length, 4);
  assert.ok(h.store.has(requests[2], version(.8, 1)));
});

test("cancelling before a frame pins completed results even beyond the request cache capacity", async () => {
  const h = harness(), requests = Array.from({ length: 180 }, (_, i) => request("a", `m-${i}`));
  let stop = h.start(requests); await tick();
  assert.equal(h.frames.size, 1);
  stop();
  assert.equal(h.frames.size, 0);
  assert.equal(h.publications.length, 1);
  assert.equal(h.store.read().size, 180);
  const calls = h.calls.length;
  stop = h.start(requests); await tick(); h.flush(); stop();
  assert.equal(h.calls.length, calls);
  assert.equal(h.publications.at(-1).pending.size, 0);
});

test("a cancelled older response cannot replace the current refresh", async () => {
  const releases = [], h = harness((id, metrics) => new Promise((resolve) => releases.push((value) => resolve(metrics.map((metric) => ({ metric, points: points(value) }))))));
  const requests = [request("a", "loss")];
  const oldStop = h.start(requests); await tick(); oldStop();
  const newStop = h.start(requests, { refreshTick: 1 }); await tick();
  releases[1](2); await tick(); h.flush();
  const snapshot = h.store.read(), published = h.publications.length;
  releases[0](1); await tick(); h.flush();
  assert.equal(h.publications.length, published);
  assert.equal(h.store.read(), snapshot);
  assert.equal(snapshot.get("loss").get("a")[0].value, 2);
  assert.ok(h.store.has(requests[0], version(.6, 1)));
  newStop();
});

test("folding and reopening in flight shares downloads and ignores the cancelled subscriber", async () => {
  let release;
  const h = harness((id, metrics) => new Promise((resolve) => { release = () => resolve(metrics.map((metric) => ({ metric, points: points(1) }))); }));
  const requests = [request("a", "loss"), request("a", "loss"), request("a", "psnr")];
  const stop = h.start(requests); await tick(); stop();
  const reopened = h.start(requests); await tick();
  assert.equal(h.calls.length, 1);
  release(); await tick(); h.flush();
  assert.equal(h.publications.length, 3);
  assert.equal(h.store.read().size, 2);
  assert.equal(h.publications.at(-1).pending.size, 0);
  reopened();
});

test("failed batches retain old curves, clear loading and can retry", async () => {
  let fail = false;
  const h = harness(async (id, metrics) => {
    if (fail) throw Error("offline");
    return metrics.map((metric) => ({ metric, points: points(1) }));
  });
  const requests = [request("a", "loss")];
  let stop = h.start(requests); await tick(); h.flush(); stop();
  const snapshot = h.store.read(); fail = true;
  stop = h.start(requests, { refreshTick: 1 }); await tick(); h.flush(); stop();
  assert.equal(h.errors, 1);
  assert.equal(h.store.read(), snapshot);
  assert.equal(h.publications.at(-1).pending.size, 0);
  assert.equal(h.store.has(requests[0], version(.6, 1)), false);
  fail = false;
  stop = h.start(requests, { refreshTick: 1 }); await tick(); h.flush(); stop();
  assert.ok(h.store.has(requests[0], version(.6, 1)));
  assert.equal(h.calls.length, 3);
});

test("incomplete responses cannot silently mark missing metrics as loaded", async () => {
  const h = harness(async () => [{ metric: "loss", points: [] }]);
  const requests = [request("a", "loss"), request("a", "psnr")];
  const stop = h.start(requests); await tick(); h.flush(); stop();
  assert.equal(h.errors, 1);
  assert.equal(h.publications.at(-1).pending.size, 0);
  assert.equal(h.store.has(requests[1], version()), false);
  assert.equal(h.store.read().size, 0);
  assert.deepEqual([...h.publications[0].pending], requests.map((r) => seriesKey(r.runId, r.metric)));
});
