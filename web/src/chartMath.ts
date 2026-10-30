import type { ScalarPoint, ScalarBounds } from "./api";
import { scaleLinear } from "d3-scale";

interface ReadoutSample {
  run: { run_id: string };
  point?: ScalarPoint;
}

export function stepAxis(minimum: number, maximum: number, width: number) {
  const padding = Math.min(20, Math.max(12, width * .04));
  const span = maximum - minimum;
  const dataSpan = Math.max(1, width - padding * 2);
  // Equal insets keep endpoint markers visible on both sides. For a normal range
  // the domain is extended past the last sample so a nearby round tick (1000 for
  // a 999 maximum) can sit in the right inset without moving the data endpoint.
  // A single Step uses a symmetric domain and centers in the data area instead.
  const scale = span
    ? scaleLinear().domain([minimum, minimum + span * (width - padding) / dataSpan]).range([padding, width])
    : scaleLinear().domain([minimum - .5, minimum + .5]).range([padding, width - padding]);
  const count = Math.max(2, Math.min(5, Math.floor(width / 85)));
  const regular = scale.ticks(count).filter(Number.isInteger);
  const format = scale.tickFormat(count, Math.max(Math.abs(minimum), Math.abs(maximum)) >= 1000 ? "s" : "d");
  const last = regular.at(-1);
  const end = last !== undefined && last >= maximum ? last : maximum;
  function tick(step: number) {
    // Automatic SI precision can round an irregular endpoint to a different Step.
    const label = regular.includes(step) ? format(step) : String(step);
    const labelWidth = label.length * 7;
    const x = scale(step);
    const anchor: "start" | "middle" | "end" = x < labelWidth / 2 ? "start" : x + labelWidth / 2 > width ? "end" : "middle";
    const left = anchor === "start" ? x : anchor === "end" ? x - labelWidth : x - labelWidth / 2;
    return { step, label, anchor, left, right: left + labelWidth };
  }
  const endTick = tick(end);
  let previousRight = -Infinity;
  const ticks = regular.filter((step) => step < end).map(tick).filter((item) => {
    if (item.left < previousRight + 8 || item.right + 8 > endTick.left) return false;
    previousRight = item.right;
    return true;
  });
  const dataWidth = scale(maximum);
  const stepAt = (pixel: number) => Math.max(minimum, Math.min(maximum, scale.invert(pixel)));
  return { scale, dataWidth, ticks: [...ticks, endTick], stepAt };
}

export function tailStepRange(extent: [number, number], percent: 20 | 50): [number, number] {
  const [start, end] = extent;
  if (end <= start) return extent;
  return [Math.min(end - 1, Math.ceil(start + (end - start) * (1 - percent / 100))), end];
}

export function compareReadouts(a: ReadoutSample, b: ReadoutSample, smoothed: boolean, baselineId: string | null, order: "value" | "list"): number {
  const baselineOrder = Number(b.run.run_id === baselineId) - Number(a.run.run_id === baselineId);
  if (baselineOrder !== 0 || order === "list") return baselineOrder;
  const av = smoothed ? a.point?.smoothed : a.point?.value;
  const bv = smoothed ? b.point?.smoothed : b.point?.value;
  const aMissing = av == null || !Number.isFinite(av), bMissing = bv == null || !Number.isFinite(bv);
  if (aMissing || bMissing) return Number(aMissing) - Number(bMissing);
  return bv! - av!;
}

export function nearestPoint(points: ScalarPoint[], step: number): ScalarPoint | undefined {
  if (!points.length || step < points[0].step) return undefined;
  let low = 0, high = points.length;
  while (low < high) { const mid = (low + high) >>> 1; if (points[mid].step <= step) low = mid + 1; else high = mid; }
  const before = points[Math.max(0, low - 1)], after = points[Math.min(points.length - 1, low)];
  return step - before.step <= after.step - step ? before : after;
}

// Return the only visible sample for a run, if it has exactly one.
export function lonePoint(samples: ScalarPoint[], smoothed: boolean, logarithmic: boolean, minimum: number, maximum: number): { point: ScalarPoint; value: number } | undefined {
  let found: { point: ScalarPoint; value: number } | undefined;
  for (const point of samples) {
    const value = smoothed ? point.smoothed : point.value;
    if (value == null || !Number.isFinite(value) || (logarithmic && value <= 0) || point.step < minimum || point.step > maximum) continue;
    if (found) return undefined;
    found = { point, value };
  }
  return found;
}

export function visiblePoints(points: ScalarPoint[], minimum: number, maximum: number): ScalarPoint[] {
  if (!points.length || maximum < points[0].step || minimum > points[points.length - 1].step) return [];
  let low = 0, high = points.length;
  while (low < high) { const mid = (low + high) >>> 1; if (points[mid].step < minimum) low = mid + 1; else high = mid; }
  const start = Math.max(0, low - 1);
  high = points.length;
  while (low < high) { const mid = (low + high) >>> 1; if (points[mid].step <= maximum) low = mid + 1; else high = mid; }
  // Retain adjacent samples so clipping preserves lines and gaps at the viewport edges.
  return points.slice(start, Math.min(points.length, low + 1));
}

export function buildPath(points: ScalarPoint[], x: (v: number) => number, y: (v: number) => number, smoothed = false, positiveOnly = false): string {
  const parts: string[] = [];
  let drawing = false;
  for (const point of points) {
    const value = smoothed ? point.smoothed : point.value;
    if (value == null || !Number.isFinite(value) || (positiveOnly && value <= 0)) { drawing = false; continue; }
    const px = x(point.step), py = y(value);
    if (!Number.isFinite(px) || !Number.isFinite(py)) { drawing = false; continue; }
    parts.push(`${drawing ? "L" : "M"}${px.toFixed(3)},${py.toFixed(3)}`);
    drawing = true;
  }
  return parts.join("");
}

export function valueDomain(values: number[], logarithmic: boolean): [number, number] {
  const valid = values.filter((value) => Number.isFinite(value) && (!logarithmic || value > 0));
  if (!valid.length) return logarithmic ? [1, 10] : [0, 1];
  let low = Math.min(...valid), high = Math.max(...valid);
  if (logarithmic) {
    const a = Math.log10(low), b = Math.log10(high), pad = Math.max((b - a) * .07, .05);
    return [Math.max(Number.MIN_VALUE, 10 ** (a - pad)), Math.min(Number.MAX_VALUE, 10 ** (b + pad))];
  }
  const pad = (high - low || Math.abs(low) || 1) * .07;
  return [low - pad, high + pad];
}

export function fittedDomain(points: ScalarPoint[], smoothed: boolean, logarithmic: boolean, cutoff?: number, bounds: ScalarBounds[] = []) {
  const usable = (value: number | null | undefined): value is number => value != null && Number.isFinite(value) && (!logarithmic || value > 0);
  const values = (samples: ScalarPoint[]) => samples.flatMap((point) => [point.value, smoothed ? point.smoothed : null].filter(usable));
  const afterCutoff = cutoff === undefined ? points : points.filter((point) => point.step >= cutoff);
  const fit = values(afterCutoff);
  for (const bound of bounds) {
    const low = logarithmic ? bound.positive_minimum : bound.minimum;
    if (usable(low)) fit.push(low);
    if (usable(bound.maximum)) fit.push(bound.maximum);
  }
  const empty = cutoff !== undefined && fit.length === 0;
  return { domain: valueDomain(empty ? values(points) : fit, logarithmic), empty };
}

export function logTicks(low: number, high: number): number[] {
  const candidates: number[] = [];
  for (let exponent = Math.floor(Math.log10(low)); exponent <= Math.ceil(Math.log10(high)); exponent++) {
    for (const multiplier of [1, 2, 5]) {
      const tick = multiplier * 10 ** exponent;
      if (Number.isFinite(tick) && tick >= low && tick <= high) candidates.push(tick);
    }
  }
  if (candidates.length < 2) return [low, high];
  if (candidates.length <= 6) return candidates;
  return Array.from({ length: 6 }, (_, index) => candidates[Math.round(index * (candidates.length - 1) / 5)]);
}
