import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/comparison.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
const { flattenConfig, parameterColumns, latestPoint, formatMetric, defaultMetricColumns, displayParameter } =
  await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

test("nested configs, literal dots, empty values and boolean false stay distinct", () => {
  const values = flattenConfig({ optimizer: { lr: 0 }, "optimizer.lr": 1, enabled: false, empty: "", object: {}, list: [1, 2] });
  assert.equal(values.get("optimizer.lr"), 0);
  assert.equal(values.get("optimizer\\.lr"), 1);
  assert.equal(displayParameter(values.get("enabled")), "false");
  assert.equal(displayParameter(values.get("empty")), '""');
  assert.equal(displayParameter(values.get("object")), "{}");
  assert.equal(displayParameter(values.get("list")), "[1,2]");
  assert.equal(displayParameter(values.get("missing")), "—");
  assert.equal(displayParameter(null), "null");
});

test("differences compare values and types, independent of object key order", () => {
  const columns = parameterColumns([
    flattenConfig({ zero: 0, typed: 1, nullable: null, layers: [{ a: 1, b: 2 }] }),
    flattenConfig({ zero: 0, typed: "1", layers: [{ b: 2, a: 1 }] }),
  ]);
  assert.deepEqual(Object.fromEntries(columns.map((column) => [column.key, column.different])), {
    layers: false, nullable: true, typed: true, zero: false,
  });
});

test("latest means the last raw sample including an invalid terminal value", () => {
  const points = [{ step: 1, value: 0.2 }, { step: 10, value: null }];
  assert.deepEqual(latestPoint(points), { step: 10, value: null });
  assert.equal(formatMetric(latestPoint(points)?.value), "—");
  assert.equal(latestPoint([]), undefined);
  assert.equal(formatMetric(0), "0");
  assert.equal(formatMetric(0.9), "0.9");
  assert.equal(formatMetric(0.000001), "1.000e-6");
  assert.equal(formatMetric(Infinity), "—");
});

test("table precision applies to recorded numbers while parameter representation stays exact", () => {
  assert.equal(formatMetric(0.9123456, 4), "0.9123");
  assert.equal(formatMetric(0, 4), "0.0000");
  assert.equal(formatMetric(-0.00000001, 0), "-1e-8");
  assert.equal(formatMetric(0.000001, 4), "1.0000e-6");
  assert.equal(formatMetric(1234567, 2), "1.23e+6");
  assert.equal(formatMetric(0.9123456789, 8), "0.91234568");
  assert.equal(displayParameter(0.00000123456789), "0.00000123456789");
});

test("default metrics favor validation and keep the supplied list unchanged", () => {
  const metrics = ["train/loss", "val/loss", "val/accuracy", "train/accuracy"];
  assert.deepEqual(defaultMetricColumns(metrics), ["val/accuracy", "val/loss"]);
  assert.equal(metrics[0], "train/loss");
});
