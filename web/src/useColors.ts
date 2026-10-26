import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getJson, type RunSummary } from "./api";
import { categoryKey, parameterValue, runColorKey, type ColorRegistry, type ColorSettings } from "./features";
import { extendRegistry, resolveColors } from "./colors";
import type { ResolvedTheme } from "./theme";
import type { CustomPalette, PaletteInput } from "./palettes";

export function useColors(runs: RunSummary[], settings: ColorSettings, theme: ResolvedTheme, workspaceId: string, writable: boolean, retryTick: number) {
  const [registry, setRegistry] = useState<ColorRegistry>({ version: 1, seed: "preview", slots: {}, palettes: [] });
  const [localOnly, setLocalOnly] = useState(false);
  const [ready, setReady] = useState(false);
  const paletteRevision = useRef(0);
  const currentWorkspace = useRef(workspaceId);
  currentWorkspace.current = workspaceId;
  useEffect(() => { setReady(false); paletteRevision.current++; }, [workspaceId]);
  const keys = useMemo(() => runs.flatMap((r) => [runColorKey(r), ...(settings.mode === "parameter" ? [categoryKey(settings.field, parameterValue(r, settings.field))] : [])]), [runs, settings.mode, settings.field]);
  const keyString = JSON.stringify([...keys].sort());
  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    const cacheKey = `cloverboard:colors:${workspaceId}${writable ? "" : ":readonly"}`;
    async function load() {
      const revision = paletteRevision.current;
      try {
        let next = await getJson<ColorRegistry>("/api/workspace/colors", writable ? { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keys }) } : undefined);
        if (cancelled) return;
        if (!writable) {
          try {
            const cached = JSON.parse(localStorage.getItem(cacheKey) ?? "null") as ColorRegistry | null;
            if (cached?.version === 1 && typeof cached.seed === "string" && cached.slots && (cached.seed === next.seed || Object.keys(next.slots).length === 0)) {
              if (Object.keys(next.slots).length === 0) next = { ...next, seed: cached.seed, slots: cached.slots };
              else for (const [key, slot] of Object.entries(cached.slots)) if (!(key in next.slots) && Number.isInteger(slot) && slot >= 0 && !Object.values(next.slots).includes(slot)) next.slots[key] = slot;
            }
          } catch { /* Browser storage is optional. */ }
        }
        next = extendRegistry(next, keys);
        setRegistry((previous) => revision === paletteRevision.current ? next : { ...next, palettes: previous.palettes });
        setReady(true);
        setLocalOnly(false);
        try { localStorage.setItem(cacheKey, JSON.stringify(next)); } catch { /* Browser storage is optional. */ }
      } catch {
        if (!cancelled) { setRegistry((previous) => extendRegistry(previous, keys)); setLocalOnly(true); }
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [workspaceId, writable, keyString, retryTick]);
  const savePalette = useCallback(async (input: PaletteInput, id?: string): Promise<CustomPalette> => {
    if (!writable) throw new Error("Workspace is read-only.");
    const palette = await getJson<CustomPalette>(`/api/workspace/palettes${id ? `/${encodeURIComponent(id)}` : ""}`, { method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) });
    if (currentWorkspace.current === workspaceId) {
      paletteRevision.current++;
      setRegistry((previous) => ({ ...previous, palettes: id ? previous.palettes.map((item) => item.id === id ? palette : item) : [...previous.palettes, palette] }));
    }
    return palette;
  }, [workspaceId, writable]);
  const deletePalette = useCallback(async (id: string): Promise<void> => {
    if (!writable) throw new Error("Workspace is read-only.");
    await getJson<CustomPalette>(`/api/workspace/palettes/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (currentWorkspace.current === workspaceId) {
      paletteRevision.current++;
      setRegistry((previous) => ({ ...previous, palettes: previous.palettes.filter((item) => item.id !== id) }));
    }
  }, [workspaceId, writable]);
  const complete = useMemo(() => extendRegistry(registry, keys), [registry, keyString]);
  return { registry: complete, colors: useMemo(() => resolveColors(runs, complete, settings, theme), [runs, complete, settings, theme]), localOnly, ready, savePalette, deletePalette };
}
