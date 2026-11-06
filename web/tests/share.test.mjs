import assert from "node:assert/strict";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test, { after } from "node:test";
import ts from "typescript";

const directory = await mkdtemp(join(tmpdir(), "cloverboard-share-"));
after(() => rm(directory, { recursive: true, force: true }));
for (const name of ["palettes", "comparison", "features", "workspace", "viewingConfig", "share"]) {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
  await writeFile(join(directory, `${name}.mjs`), outputText.replace(/from "\.\/([^"]+)"/g, 'from "./$1.mjs"'));
}
const workspace = await import(pathToFileURL(join(directory, "workspace.mjs")));
const { shareTarget } = await import(pathToFileURL(join(directory, "share.mjs")));
const state = (patch = {}) => workspace.normalizeState(patch);

test("an unmodified Shared View shares a short View link", () => {
  const viewState = state({ smoothing: .2 });
  const view = { view_id: "a".repeat(32), name: "Team", state: viewState };
  const active = { scope: "shared", view_id: view.view_id, base_key: workspace.viewStateKey(viewState) };
  assert.deepEqual(shareTarget(viewState, active, [view]), { kind: "view", viewId: view.view_id });
});

test("My Views and modified Shared Views share a full state snapshot", () => {
  const viewState = state({ smoothing: .2 });
  const view = { view_id: "a".repeat(32), name: "Team", state: viewState };
  const local = shareTarget(viewState, { scope: "local", view_id: view.view_id, base_key: workspace.viewStateKey(viewState) }, [view]);
  assert.equal(local.kind, "state");
  assert.deepEqual(workspace.readSharedLocation(`http://localhost/#state=${local.encoded}`).state, viewState);
  const modified = shareTarget(state({ ...viewState, smoothing: .1 }), { scope: "shared", view_id: view.view_id, base_key: workspace.viewStateKey(viewState) }, [view]);
  assert.equal(modified.kind, "state");
  assert.equal(workspace.readSharedLocation(`http://localhost/#state=${modified.encoded}`).state.smoothing, .1);
});

test("a Shared View that disappeared falls back to a state snapshot", () => {
  const plain = state();
  const target = shareTarget(plain, { scope: "shared", view_id: "b".repeat(32), base_key: workspace.viewStateKey(plain) }, []);
  assert.equal(target.kind, "state");
  assert.deepEqual(workspace.readSharedLocation(`http://localhost/#state=${target.encoded}`).state, plain);
});

test("an oversized session cannot be shared and asks for JSON export", () => {
  const huge = state({ selected_ids: Array.from({ length: 1000 }, (_, index) => `experiment-${index}`) });
  assert.deepEqual(shareTarget(huge, null, []), { kind: "too-large" });
  assert.throws(() => workspace.encodeSharedState(huge), /Export.*JSON/);
});
