export type BuiltinPaletteId = "default" | "bright";
export interface CustomPalette { id: string; name: string; colors: string[] }
export interface PaletteInput { name: string; colors: string[] }
export type PaletteSelection = { kind: "builtin"; id: BuiltinPaletteId } | ({ kind: "custom" } & CustomPalette);

export const DEFAULT_PALETTE: PaletteSelection = { kind: "builtin", id: "default" };
export const DEFAULT_COLORS = ["#3276b8", "#c05a24", "#267f6c", "#9955af", "#b74666", "#847113", "#4d5eaf", "#a54938", "#347f98", "#697c30", "#875c46", "#7b658b"];
export const BRIGHT_COLORS = [
  "#7ECEF4", "#EA68A2", "#FF9A52", "#A2DB86",
  "#B59BE8", "#62D6C4", "#F3CA65", "#F47C78",
  "#879CF0", "#D6A06E", "#CADB68", "#D98BDD",
] as const;

export function paletteName(palette: PaletteSelection): string {
  return palette.kind === "custom" ? palette.name : palette.id === "bright" ? "Bright" : "Default";
}
export function paletteBaseColors(palette: PaletteSelection): readonly string[] {
  return palette.kind === "custom" ? palette.colors : palette.id === "bright" ? BRIGHT_COLORS : DEFAULT_COLORS;
}
export function paletteKey(palette: PaletteSelection): string {
  return palette.kind === "custom" ? JSON.stringify(["custom", palette.colors.map((color) => color.toLowerCase())]) : palette.id;
}
export function paletteError(name: string, colors: readonly string[]): string | null {
  if (!name.trim() || name.trim().length > 60) return "Use a palette name between 1 and 60 characters.";
  if (colors.length < 2 || colors.length > 24) return "Use between 2 and 24 colors.";
  if (colors.some((color) => !/^#[0-9a-f]{6}$/i.test(color))) return "Each color must be a six-digit HEX value, such as #7ECEF4.";
  if (new Set(colors.map((color) => color.toLowerCase())).size !== colors.length) return "Each palette color must be unique.";
  return null;
}
export function normalizePalette(value: unknown): PaletteSelection {
  if (typeof value !== "object" || value === null) return { ...DEFAULT_PALETTE };
  const candidate = value as Partial<CustomPalette> & { kind?: string };
  if (candidate.kind === "builtin" && (candidate.id === "default" || candidate.id === "bright")) return { kind: "builtin", id: candidate.id };
  if (candidate.kind === "custom" && typeof candidate.id === "string" && /^custom:[0-9a-f]{32}$/.test(candidate.id) && typeof candidate.name === "string" && Array.isArray(candidate.colors) && candidate.colors.every((color) => typeof color === "string") && !paletteError(candidate.name, candidate.colors)) {
    return { kind: "custom", id: candidate.id, name: candidate.name.trim(), colors: candidate.colors.map((color) => color.toLowerCase()) };
  }
  return { ...DEFAULT_PALETTE };
}
