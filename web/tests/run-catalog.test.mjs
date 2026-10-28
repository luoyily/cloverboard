import assert from "node:assert/strict";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test, { after } from "node:test";
import ts from "typescript";

const directory = await mkdtemp(join(tmpdir(), "cloverboard-run-catalog-"));
after(() => rm(directory, { recursive: true, force: true }));
const source = await readFile(new URL("../src/api.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
await writeFile(join(directory, "api.mjs"), outputText);
const { fetchRunCatalogWithEtag } = await import(pathToFileURL(join(directory, "api.mjs")));

test("run catalog fetch keeps runs and source health in one revalidated snapshot", async t => {
  const catalog = { runs: [], sources: [{ source_id: "gpu", label: "GPU", kind: "peer", online: false }] };
  const controller = new AbortController();
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(catalog, { headers: { ETag: '"offline"' } }));
  const entry = await fetchRunCatalogWithEtag(null, controller.signal);
  assert.deepEqual(entry, { etag: '"offline"', data: catalog });
  assert.equal(fetch.mock.calls[0].arguments[0], "/api/run-catalog");
  assert.equal(fetch.mock.calls[0].arguments[1].signal, controller.signal);

  fetch.mock.mockImplementation(async () => new Response(null, { status: 304 }));
  assert.equal(await fetchRunCatalogWithEtag(entry), entry);
  assert.equal(fetch.mock.calls[1].arguments[1].headers["If-None-Match"], '"offline"');

  const online = { ...catalog, sources: [{ ...catalog.sources[0], online: true }] };
  fetch.mock.mockImplementation(async () => Response.json(online, { headers: { ETag: '"online-empty"' } }));
  assert.deepEqual(await fetchRunCatalogWithEtag(entry), { etag: '"online-empty"', data: online });
});

test("a failed catalog request rejects instead of displaying the previous cached runs", async t => {
  const cached = { etag: '"previous"', data: { runs: [{ run_id: "old" }], sources: [] } };
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "Catalog unavailable" }, { status: 503 }));
  await assert.rejects(fetchRunCatalogWithEtag(cached), /Catalog unavailable/);
});

test("an interrupted catalog request does not turn into a successful cached response", async t => {
  const cached = { etag: '"previous"', data: { runs: [{ run_id: "old" }], sources: [] } };
  t.mock.method(globalThis, "fetch", async () => { throw new DOMException("Aborted", "AbortError"); });
  await assert.rejects(fetchRunCatalogWithEtag(cached), { name: "AbortError" });
});
