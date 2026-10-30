import type { RunSummary } from "./api";
import type { ResolvedTheme } from "./theme";
import { categoryKey, parameterValue, runColorKey, type ColorSettings } from "./features";
import { paletteBaseColors, paletteKey, type PaletteSelection } from "./palettes";
import { ensureContrast, hex, hexToHsv, hslToHex, lab, labDistance, rgb } from "./colorMath";

const BACKGROUNDS: Record<ResolvedTheme, string> = { light: "#ffffff", dark: "#222831" };
const CONTRAST: Record<ResolvedTheme, number> = { light: 2, dark: 3 };
const MISSING = "#747e8b";
export const SIMILAR_COLOR_DISTANCE = .065;

export function hash(value: string): number {
  let n = 2166136261;
  for (const c of value) { n ^= c.charCodeAt(0); n = Math.imul(n, 16777619); }
  return n >>> 0;
}

const themed = new Map<string, string>();
// One source color per run; light/dark are derived from it with a single rule.
export function themeColor(color: string, theme: ResolvedTheme): string {
  const key = `${theme}:${color.toLowerCase()}`;
  let value = themed.get(key);
  if (value === undefined) {
    value = ensureContrast(key.slice(key.indexOf(":") + 1), BACKGROUNDS[theme], CONTRAST[theme]);
    if (themed.size >= 4096) themed.delete(themed.keys().next().value!);
    themed.set(key, value);
  }
  return value;
}

interface LabPair { light: number[]; dark: number[] }
const sourceLabCache = new Map<string, LabPair>();
function sourceLabs(color: string): LabPair {
  const key = color.toLowerCase();
  let labs = sourceLabCache.get(key);
  if (!labs) {
    labs = { light: lab(themeColor(key, "light")), dark: lab(themeColor(key, "dark")) };
    if (sourceLabCache.size >= 4096) sourceLabCache.delete(sourceLabCache.keys().next().value!);
    sourceLabCache.set(key, labs);
  }
  return labs;
}
function labSeparation(a: LabPair, b: LabPair): number {
  return Math.min(labDistance(a.light, b.light), labDistance(a.dark, b.dark));
}
// Distance between what the user actually sees in both themes.
export function colorSeparation(a: string, b: string): number {
  return labSeparation(sourceLabs(a), sourceLabs(b));
}

interface PoolEntry { color: string; labs: LabPair }
interface PalettePool { bases: PoolEntry[]; generated: PoolEntry[] }
const pools = new Map<string, PalettePool>();
const poolEntry = (color: string): PoolEntry => { const normalized = color.toLowerCase(); return { color: normalized, labs: sourceLabs(normalized) }; };

// Every palette follows one pipeline: its authored base colors first, then shades
// derived from those hues. No separate hand-authored light/dark identities.
function poolFor(selection: PaletteSelection): PalettePool {
  const key = paletteKey(selection);
  let pool = pools.get(key);
  if (!pool) {
    const baseColors = paletteBaseColors(selection);
    const bases = baseColors.map(poolEntry);
    const known = new Set(bases.map((base) => base.color));
    const generated: PoolEntry[] = [];
    for (const base of baseColors) {
      const hue = hexToHsv(base).h;
      for (const offset of [-8, 0, 8]) for (const s of [.45, .6, .75]) for (const l of [.45, .55, .65]) {
        const color = hslToHex((hue + offset + 360) % 360, s, l).toLowerCase();
        if (known.has(color)) continue;
        known.add(color);
        generated.push(poolEntry(color));
      }
    }
    pool = { bases, generated };
    if (pools.size >= 64) pools.delete(pools.keys().next().value!);
    pools.set(key, pool);
  }
  return pool;
}

interface PaletteSequence {
  pool: PalettePool;
  sequence: PoolEntry[];
  baseDistance: number[];
  generatedDistance: number[];
  seen: number;
}
const sequences = new Map<string, PaletteSequence>();

function sequenceFor(selection: PaletteSelection): PaletteSequence {
  const key = paletteKey(selection);
  let state = sequences.get(key);
  if (!state) {
    const pool = poolFor(selection);
    state = {
      pool,
      sequence: [],
      baseDistance: pool.bases.map(() => Infinity),
      generatedDistance: pool.generated.map(() => Infinity),
      seen: 0,
    };
    if (sequences.size >= 64) sequences.delete(sequences.keys().next().value!);
    sequences.set(key, state);
  }
  return state;
}

function foldPicked(state: PaletteSequence) {
  for (const picked of state.sequence.slice(state.seen)) {
    for (let i = 0; i < state.pool.bases.length; i++) {
      state.baseDistance[i] = Math.min(state.baseDistance[i], labSeparation(state.pool.bases[i].labs, picked.labs));
    }
    for (let i = 0; i < state.pool.generated.length; i++) {
      state.generatedDistance[i] = Math.min(state.generatedDistance[i], labSeparation(state.pool.generated[i].labs, picked.labs));
    }
  }
  state.seen = state.sequence.length;
}

function fallbackEntry(state: PaletteSequence): PoolEntry {
  const used = new Set(state.sequence.map((entry) => entry.color));
  for (let attempt = 0; attempt < 4096; attempt++) {
    const n = state.sequence.length + attempt;
    const color = hslToHex((n * 137.507764 + hash("cloverboard")) % 360, .6 + (n % 5) * .03, .3 + (n % 17) * .01).toLowerCase();
    if (!used.has(color)) return poolEntry(color);
  }
  return poolEntry(hslToHex((state.sequence.length * 137.507764) % 360, .7, .5));
}

function extendSequence(state: PaletteSequence) {
  const { pool } = state;
  foldPicked(state);
  const fromBases = state.sequence.length < pool.bases.length;
  const entries = fromBases ? pool.bases : pool.generated;
  const distances = fromBases ? state.baseDistance : state.generatedDistance;
  let best: PoolEntry | undefined, bestScore = -Infinity;
  for (let i = 0; i < entries.length; i++) {
    const candidate = entries[i];
    if (state.sequence.some((entry) => entry.color === candidate.color)) continue;
    const score = distances[i] + hash(`cloverboard${candidate.color}`) / 2 ** 32 * 1e-6;
    if (score > bestScore) { bestScore = score; best = candidate; }
  }
  state.sequence.push(best ?? fallbackEntry(state));
}

export function paletteColor(slot: number, selection: PaletteSelection = { kind: "builtin", id: "default" }): string {
  const state = sequenceFor(selection);
  while (state.sequence.length <= slot) extendSequence(state);
  return state.sequence[slot].color;
}

const VIRIDIS = ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"];
export function continuousColor(value: number, domain: [number, number], theme: ResolvedTheme): string {
  const t = Math.max(0, Math.min(1, (value - domain[0]) / (domain[1] - domain[0]))) * (VIRIDIS.length - 1);
  const index = Math.min(VIRIDIS.length - 2, Math.floor(t)), fraction = t - index;
  const a = rgb(VIRIDIS[index]), b = rgb(VIRIDIS[index + 1]);
  return themeColor(hex(a.map((v, i) => v + (b[i] - v) * fraction)), theme);
}

// Colors are materialized once in settings.runs. Rendering only adapts contrast.
export function resolveColors(runs: RunSummary[], settings: ColorSettings, theme: ResolvedTheme): Map<string, string> {
  return new Map(runs.map(run => [run.run_id, themeColor(settings.runs[runColorKey(run)]?.color ?? MISSING, theme)]));
}

function generatedColor(run: RunSummary, settings: ColorSettings): string {
  const value = parameterValue(run, settings.field);
  if (settings.mode === "continuous") return typeof value === "number" && Number.isFinite(value) ? continuousColor(value, settings.domain, "light") : MISSING;
  if (value === undefined) return MISSING;
  const slot = hash(categoryKey(settings.field, value)) % paletteBaseColors(settings.palette).length;
  const base = rgb(paletteColor(slot, settings.palette)), blend = (hash(runColorKey(run)) % 3) * .07;
  return hex(base.map(channel => channel * (1 - blend)));
}

export interface AssignScope {
  // Runs allowed to change. Without it, only runs that have no color yet move.
  selected?: Set<string>;
  // Primary avoidance set. Defaults to every run passed to assignColors.
  avoid?: RunSummary[];
  // Secondary tie-breaker, typically the unchecked runs. It never reduces the
  // spacing achieved among the primary set.
  fallback?: RunSummary[];
}

// New runs fill against the whole workspace. An explicit selection reassigns those
// runs and optimizes their spacing; other runs only break ties and block exact reuse.
export function assignColors(runs: RunSummary[], settings: ColorSettings, scope: AssignScope = {}): ColorSettings {
  const movable = runs.filter(run => {
    const entry = settings.runs[runColorKey(run)];
    return !entry?.fixed && (scope.selected ? scope.selected.has(run.run_id) : !entry);
  }).sort((a, b) => runColorKey(a).localeCompare(runColorKey(b)));
  if (!movable.length) return settings;
  const next = { ...settings, runs: { ...settings.runs } };
  const moving = new Set(movable.map(runColorKey));
  const colorList = (source: RunSummary[]) => source.filter(run => !moving.has(runColorKey(run))).flatMap(run => {
    const entry = settings.runs[runColorKey(run)];
    return entry ? [entry.color] : [];
  });
  const occupied = colorList(scope.avoid ?? runs), fallback = colorList(scope.fallback ?? []);
  const occupiedLabs = occupied.map(sourceLabs), fallbackLabs = fallback.map(sourceLabs);
  const used = new Set([...occupied, ...fallback].map(color => color.toLowerCase()));
  // Reserve enough palette slots for the whole visible scope, not just the movable
  // runs: unchecked colors block exact reuse, so a small pool can be fully occupied.
  const referenceCount = runs.length + (scope.fallback?.length ?? 0);
  const candidates = settings.mode === "random" ? Array.from({ length: Math.max(32, Math.min(512, referenceCount * 2)) }, (_, index) => {
    const color = paletteColor(index, settings.palette), labs = sourceLabs(color);
    return {
      color, labs,
      primary: occupiedLabs.length ? Math.min(...occupiedLabs.map(other => labSeparation(labs, other))) : Infinity,
      secondary: fallbackLabs.length ? Math.min(...fallbackLabs.map(other => labSeparation(labs, other))) : Infinity,
    };
  }) : [];
  const score = (candidate: typeof candidates[number]) => candidate.primary === Infinity ? candidate.secondary : candidate.primary + .02 * Math.min(candidate.secondary, 1);
  for (const run of movable) {
    const key = runColorKey(run);
    let color: string;
    if (settings.mode === "random") {
      const old = settings.runs[key]?.color;
      let best: typeof candidates[number] | undefined;
      for (const candidate of candidates) {
        if (candidate.color === old?.toLowerCase() || used.has(candidate.color)) continue;
        if (!best || score(candidate) > score(best)) best = candidate;
      }
      color = best?.color ?? old ?? paletteColor(used.size, settings.palette);
      if (best) for (const candidate of candidates) {
        const separation = labSeparation(candidate.labs, best.labs);
        candidate.primary = Math.min(candidate.primary, separation);
        candidate.secondary = Math.min(candidate.secondary, separation);
      }
    } else color = generatedColor(run, settings);
    next.runs[key] = { color, fixed: false };
    used.add(color.toLowerCase());
  }
  return next;
}

export function recolorRuns(runs: RunSummary[], settings: ColorSettings): ColorSettings {
  return assignColors(runs, settings, { selected: new Set(runs.map(run => run.run_id)) });
}

export interface ColorUndo { before: ColorSettings["runs"]; after: ColorSettings["runs"] }
export function undoColors(settings: ColorSettings, undo: ColorUndo): ColorSettings {
  const runs = { ...settings.runs };
  for (const [key, assigned] of Object.entries(undo.after)) {
    const current = runs[key], previous = undo.before[key];
    if (assigned === previous || current?.color !== assigned.color || current.fixed !== assigned.fixed) continue;
    if (previous) runs[key] = previous;
    else delete runs[key];
  }
  return { ...settings, runs };
}
