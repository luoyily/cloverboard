export type ThemeMode = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const THEME_STORAGE_KEY = "cloverboard.theme";

export function loadThemeMode(): ThemeMode {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    if (saved === "light" || saved === "dark" || saved === "system") return saved;
  } catch { /* Keep the UI usable when browser storage is unavailable. */ }
  return "light";
}
export function saveThemeMode(mode: ThemeMode): void {
  try { localStorage.setItem(THEME_STORAGE_KEY, mode); }
  catch { /* Workspace persistence reports browser storage failures. */ }
}

export function resolveTheme(mode: ThemeMode): ResolvedTheme {
  if (mode !== "system") {
    return mode;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function applyTheme(mode: ThemeMode): void {
  document.documentElement.dataset.theme = resolveTheme(mode);
}

export function nextThemeMode(mode: ThemeMode): ThemeMode {
  if (mode === "light") {
    return "dark";
  }
  if (mode === "dark") {
    return "system";
  }
  return "light";
}
