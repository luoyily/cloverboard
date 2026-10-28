import assert from "node:assert/strict";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test, { after } from "node:test";
import ts from "typescript";

const directory = await mkdtemp(join(tmpdir(), "cloverboard-selection-"));
after(() => rm(directory, { recursive: true, force: true }));
const source = await readFile(new URL("../src/runSelection.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
await writeFile(join(directory, "runSelection.mjs"), outputText);
const { availableSelection, reconcileSelection, selectionPatch, replaceSelection } = await import(pathToFileURL(join(directory, "runSelection.mjs")));

const run = (id, started_at = 1) => ({ run_id: id, source: id.includes(":") ? id.split(":")[0] : "local", started_at, has_scalars: true });
const status = (source_id, online = true) => ({ source_id, online, label: source_id, kind: source_id === "local" ? "local" : "peer" });
const state = (patch = {}) => ({ selected_ids: [], baseline_id: null, ...patch });

test("displayed selections count only current runs and an available baseline", () => {
  const saved = state({ selected_ids: ["visible", "gpu:old"], baseline_id: "gpu:old" });
  assert.deepEqual([...availableSelection(saved, [run("visible")])], ["visible"]);
  assert.equal(availableSelection(saved, []).size, 0);
  assert.deepEqual([...availableSelection(state({ baseline_id: "baseline" }), [run("baseline")])], ["baseline"]);
  assert.deepEqual(saved.selected_ids, ["visible", "gpu:old"]);
  assert.equal(saved.baseline_id, "gpu:old");
});

test("an online empty source confirms deletion of selection and baseline", () => {
  const saved = state({ selected_ids: ["gpu:old"], baseline_id: "gpu:old" });
  const catalog = { runs: [], sources: [status("gpu")] };
  assert.deepEqual(reconcileSelection(saved, catalog), { selected_ids: [], baseline_id: null });
  const cleaned = { ...saved, ...reconcileSelection(saved, catalog) };
  assert.equal(reconcileSelection(cleaned, catalog), null);
});

test("offline sources disappear from the display while saved choices recover on reconnect", () => {
  const saved = state({ selected_ids: ["gpu:old"], baseline_id: "gpu:old" });
  const offline = { runs: [], sources: [status("gpu", false)] };
  assert.equal(reconcileSelection(saved, offline), null);
  assert.equal(availableSelection(saved, offline.runs).size, 0);
  const recovered = { runs: [run("gpu:old")], sources: [status("gpu")] };
  assert.equal(reconcileSelection(saved, recovered), null);
  assert.deepEqual([...availableSelection(saved, recovered.runs)], ["gpu:old"]);
});

test("a complete catalog removes unconfigured sources but preserves failed configured sources", () => {
  const saved = state({ selected_ids: ["local-gone", "gpu:gone", "offline:kept", "removed:gone"], baseline_id: "offline:kept" });
  assert.deepEqual(reconcileSelection(saved, {
    runs: [], sources: [status("local"), status("gpu"), status("offline", false)],
  }), { selected_ids: ["offline:kept"] });
});

test("search and tag filters cannot remove selections from the complete run list", () => {
  const saved = state({ selected_ids: ["hidden", "gpu:visible"], search: "does not match", tag_filter: ["missing-tag"] });
  const catalog = { runs: [run("hidden"), run("gpu:visible")], sources: [status("local"), status("gpu")] };
  assert.equal(reconcileSelection(saved, catalog), null);
  assert.equal(availableSelection(saved, catalog.runs).size, 2);
});

test("null selections keep automatic defaults and only a confirmed missing baseline is cleared", () => {
  const saved = state({ selected_ids: null, baseline_id: "gpu:gone" });
  assert.deepEqual(reconcileSelection(saved, { runs: [], sources: [status("gpu")] }), { baseline_id: null });
  assert.equal(reconcileSelection(state({ selected_ids: null }), { runs: [], sources: [] }), null);
  const runs = [run("old", 1), run("new", 4), run("middle", 3), run("earlier", 2)];
  assert.deepEqual([...availableSelection(state({ selected_ids: null }), runs)], ["new", "middle", "earlier"]);
  assert.equal(saved.selected_ids, null);
});

test("ordinary selection edits preserve offline choices; explicit replacement clears them", () => {
  const saved = state({ selected_ids: ["visible", "offline:kept"], baseline_id: "offline:baseline" });
  const active = availableSelection(saved, [run("visible"), run("another")]);
  const changed = selectionPatch(saved, active, ["visible", "another"], true);
  assert.deepEqual(changed.selected_ids, ["visible", "offline:kept", "offline:baseline", "another"]);
  assert.equal(changed.baseline_id, "offline:baseline");
  const unchecked = selectionPatch(saved, active, ["visible"], false);
  assert.deepEqual(unchecked.selected_ids, ["offline:kept", "offline:baseline"]);
  assert.equal(unchecked.baseline_id, "offline:baseline");
  assert.deepEqual(replaceSelection(saved, ["another"]), { selected_ids: ["another"], baseline_id: null });
});

test("reconciliation changes only current selection fields, preserving colors and view identity", () => {
  const saved = state({
    selected_ids: ["gpu:gone"], baseline_id: "gpu:gone", active_view_id: "saved-view",
    colors: { runs: { "gpu:gone": { color: "#abcdef", fixed: true } } },
  });
  const before = structuredClone(saved);
  const patch = reconcileSelection(saved, { runs: [], sources: [status("gpu")] });
  assert.deepEqual(Object.keys(patch).sort(), ["baseline_id", "selected_ids"]);
  assert.deepEqual(saved, before);
  assert.deepEqual({ ...saved, ...patch }.colors, before.colors);
  assert.equal({ ...saved, ...patch }.active_view_id, "saved-view");
});
