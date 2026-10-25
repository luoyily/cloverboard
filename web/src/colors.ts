import type { RunSummary } from "./api";
import type { ResolvedTheme } from "./theme";
import { categoryKey, parameterValue, runColorKey, type ColorRegistry, type ColorSettings } from "./features";

type Pair = { light: string; dark: string };
const BACKGROUNDS = { light: "#ffffff", dark: "#222831" };
const INITIAL: Pair[] = [
  { light: "#3276b8", dark: "#72b2ef" }, { light: "#c05a24", dark: "#f4a266" },
  { light: "#267f6c", dark: "#65c5ac" }, { light: "#9955af", dark: "#c993df" },
  { light: "#b74666", dark: "#ed8ca9" }, { light: "#847113", dark: "#c5b657" },
  { light: "#4d5eaf", dark: "#97a4ee" }, { light: "#a54938", dark: "#e79783" },
  { light: "#347f98", dark: "#6fc2dc" }, { light: "#697c30", dark: "#b1c877" },
  { light: "#875c46", dark: "#cfa58a" }, { light: "#7b658b", dark: "#bca2ce" },
];

export function hash(value: string): number {
  let n = 2166136261;
  for (const c of value) { n ^= c.charCodeAt(0); n = Math.imul(n, 16777619); }
  return n >>> 0;
}
function rgb(hex: string): number[] { return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255); }
function linear(v: number): number { return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }
function hex(values: number[]): string { return `#${values.map((v) => Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, "0")).join("")}`; }
export type Hsv = { h: number; s: number; v: number };
export function hexToHsv(color: string): Hsv {
  const [r, g, b] = rgb(color), high = Math.max(r, g, b), low = Math.min(r, g, b), delta = high - low;
  let h = 0;
  if (delta) h = high === r ? (g - b) / delta : high === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
  return { h: (h * 60 + 360) % 360, s: high ? delta / high : 0, v: high };
}
export function hsvToHex({ h, s, v }: Hsv): string {
  const hue = ((h % 360) + 360) % 360 / 60;
  const chroma = v * s, x = chroma * (1 - Math.abs(hue % 2 - 1)), m = v - chroma;
  const channels = [[chroma, x, 0], [x, chroma, 0], [0, chroma, x], [0, x, chroma], [x, 0, chroma], [chroma, 0, x]][Math.floor(hue)];
  return hex(channels.map((channel) => channel + m));
}
function luminance(color: string): number { const [r, g, b] = rgb(color).map(linear); return r * 0.2126 + g * 0.7152 + b * 0.0722; }
export function contrast(a: string, b: string): number { const x = luminance(a), y = luminance(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }

export function visibleColor(color: string, theme: ResolvedTheme): string {
  const source = rgb(color), destination = theme === "light" ? [0, 0, 0] : [1, 1, 1];
  for (let i = 0; i <= 100; i++) {
    const next = hex(source.map((v, j) => v + (destination[j] - v) * i / 100));
    if (contrast(next, BACKGROUNDS[theme]) >= 3) return next;
  }
  return hex(destination);
}

function lab(color: string): number[] {
  const [r, g, b] = rgb(color).map(linear);
  const l = Math.cbrt(.4122214708 * r + .5363325363 * g + .0514459929 * b);
  const m = Math.cbrt(.2119034982 * r + .6806995451 * g + .1073969566 * b);
  const s = Math.cbrt(.0883024619 * r + .2817188376 * g + .6299787005 * b);
  return [.2104542553 * l + .793617785 * m - .0040720468 * s, 1.9779984951 * l - 2.428592205 * m + .4505937099 * s, .0259040371 * l + .7827717662 * m - .808675766 * s];
}
export function colorDistance(a: string, b: string): number { const x = lab(a), y = lab(b); return Math.hypot(...x.map((v, i) => v - y[i])); }
function hsl(h: number, s: number, l: number): string {
  const a = s * Math.min(l, 1 - l);
  const channel = (n: number) => { const k = (n + h / 30) % 12; return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)); };
  return hex([channel(0), channel(8), channel(4)]);
}
function pairDistance(a: Pair, b: Pair): number { return Math.min(colorDistance(a.light, b.light), colorDistance(a.dark, b.dark)); }
const palettes = new Map<string, Pair[]>();
const candidateScores = new Map<string, { distances: number[]; seen: number }>();
const candidates: Pair[] = [];
for (let h = 0; h < 360; h += 7) for (const s of [.48, .68, .86]) for (const l of [.34, .44, .54]) {
  candidates.push({ light: visibleColor(hsl(h, s, l), "light"), dark: visibleColor(hsl(h, s, Math.min(.8, l + .25)), "dark") });
}
const candidateLabs = candidates.map((p) => ({ light: lab(p.light), dark: lab(p.dark) }));
function labDistance(a: number[], b: number[]): number { return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]); }

export function slotColor(slot: number, seed: string, theme: ResolvedTheme): string {
  let palette = palettes.get(seed);
  if (!palette) { palette = []; palettes.set(seed, palette); }
  while (palette.length <= slot) {
    const pool = palette.length < INITIAL.length ? INITIAL : candidates;
    const usedLight = new Set(palette.map((p) => p.light)), usedDark = new Set(palette.map((p) => p.dark));
    let distances: number[] | undefined;
    if (pool === candidates) {
      let scores = candidateScores.get(seed);
      if (!scores) { scores = { distances: candidates.map(() => Infinity), seen: 0 }; candidateScores.set(seed, scores); }
      for (const previous of palette.slice(scores.seen)) {
        const light = lab(previous.light), dark = lab(previous.dark);
        for (let i = 0; i < candidates.length; i++) scores.distances[i] = Math.min(scores.distances[i], labDistance(candidateLabs[i].light, light), labDistance(candidateLabs[i].dark, dark));
      }
      scores.seen = palette.length;
      distances = scores.distances;
    }
    let best: Pair | undefined, score = -Infinity;
    for (let i = 0; i < pool.length; i++) {
      const candidate = pool[i];
      if (usedLight.has(candidate.light) || usedDark.has(candidate.dark)) continue;
      const separation = distances?.[i] ?? (palette.length ? Math.min(...palette.map((p) => pairDistance(p, candidate))) : 0);
      const nextScore = separation + hash(seed + candidate.light) / 2 ** 32 * .000001;
      if (nextScore > score) { score = nextScore; best = candidate; }
    }
    // Beyond the vetted candidates, expand instead of cycling through occupied slots.
    if (!best) {
      let n = palette.length;
      do {
        best = { light: visibleColor(hsl((n * 137.507764 + hash(seed)) % 360, .6 + (n % 5) * .03, .3 + (n % 17) * .01), "light"), dark: visibleColor(hsl((n * 137.507764 + hash(seed)) % 360, .6, .55 + (n % 19) * .01), "dark") };
        n++;
      } while (usedLight.has(best.light) || usedDark.has(best.dark));
    }
    palette.push(best);
  }
  return palette[slot][theme];
}

export function extendRegistry(registry: ColorRegistry, keys: string[]): ColorRegistry {
  const slots = { ...registry.slots };
  let next = Math.max(-1, ...Object.values(slots)) + 1;
  for (const key of [...new Set(keys)].sort()) if (!(key in slots)) slots[key] = next++;
  return { ...registry, slots };
}

const VIRIDIS = ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"];
export function continuousColor(value: number, domain: [number, number], theme: ResolvedTheme): string {
  const t = Math.max(0, Math.min(1, (value - domain[0]) / (domain[1] - domain[0]))) * (VIRIDIS.length - 1);
  const index = Math.min(VIRIDIS.length - 2, Math.floor(t)), fraction = t - index;
  const a = rgb(VIRIDIS[index]), b = rgb(VIRIDIS[index + 1]);
  return visibleColor(hex(a.map((v, i) => v + (b[i] - v) * fraction)), theme);
}

export function resolveColors(runs: RunSummary[], registry: ColorRegistry, settings: ColorSettings, theme: ResolvedTheme): Map<string, string> {
  const missing = theme === "light" ? "#747e8b" : "#a3adba";
  const prefix = `parameter:[${JSON.stringify(settings.field)},`;
  const families = new Map(Object.entries(registry.slots).filter(([key]) => key.startsWith(prefix)).sort((a, b) => a[1] - b[1]).map(([key], i) => [key, i]));
  return new Map(runs.map((run) => {
    const key = runColorKey(run), value = parameterValue(run, settings.field);
    let color: string;
    if (settings.mode === "continuous") {
      color = typeof value === "number" && Number.isFinite(value) ? continuousColor(value, settings.domain, theme) : missing;
    } else if (settings.mode === "parameter") {
      const slot = families.get(categoryKey(settings.field, value));
      if (value === undefined || slot === undefined) color = missing;
      else {
        const base = rgb(slotColor(slot, registry.seed, theme));
        const blend = (hash(key) % 3) * .07, toward = theme === "light" ? 0 : 1;
        color = visibleColor(hex(base.map((v) => v + (toward - v) * blend)), theme);
      }
    } else {
      color = settings.overrides[key] ? visibleColor(settings.overrides[key], theme) : slotColor(settings.assignments[key] ?? registry.slots[key] ?? 0, registry.seed, theme);
    }
    return [run.run_id, color];
  }));
}

export function reassignColors(runs: RunSummary[], selected: Set<string>, registry: ColorRegistry, settings: ColorSettings): ColorSettings {
  const next = { ...settings, assignments: { ...settings.assignments } };
  const movable = runs.filter((r) => selected.has(r.run_id) && !settings.overrides[runColorKey(r)]);
  const fixed = runs.filter((r) => !movable.includes(r));
  const light = resolveColors(fixed, registry, settings, "light"), dark = resolveColors(fixed, registry, settings, "dark");
  const occupied: Pair[] = fixed.map((r) => ({ light: light.get(r.run_id)!, dark: dark.get(r.run_id)! }));
  for (const run of movable) {
    const key = runColorKey(run), oldSlot = settings.assignments[key] ?? registry.slots[key];
    let chosen = -1, score = -Infinity;
    for (let slot = 0; slot < Math.max(32, runs.length * 2); slot++) {
      if (slot === oldSlot) continue;
      const candidate = { light: slotColor(slot, registry.seed, "light"), dark: slotColor(slot, registry.seed, "dark") };
      if (occupied.some((p) => p.light === candidate.light || p.dark === candidate.dark)) continue;
      const distance = occupied.length ? Math.min(...occupied.map((p) => pairDistance(p, candidate))) : 1;
      if (distance > score) { score = distance; chosen = slot; }
    }
    if (chosen >= 0) {
      next.assignments[key] = chosen;
      occupied.push({ light: slotColor(chosen, registry.seed, "light"), dark: slotColor(chosen, registry.seed, "dark") });
    }
  }
  return next;
}
