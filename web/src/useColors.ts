import { useEffect, useMemo, useState } from "react";
import { getJson, type RunSummary } from "./api";
import { categoryKey, parameterValue, runColorKey, type ColorRegistry, type ColorSettings } from "./features";
import { extendRegistry, resolveColors } from "./colors";
import type { ResolvedTheme } from "./theme";

export function useColors(runs: RunSummary[], settings: ColorSettings, theme: ResolvedTheme, workspaceId: string, writable: boolean, retryTick: number) {
  const [registry, setRegistry] = useState<ColorRegistry>({ version: 1, seed: "preview", slots: {} });
  const [localOnly, setLocalOnly] = useState(false);
  const keys = useMemo(() => runs.flatMap((r) => [runColorKey(r), ...(settings.mode === "parameter" ? [categoryKey(settings.field, parameterValue(r, settings.field))] : [])]), [runs, settings.mode, settings.field]);
  const keyString = JSON.stringify([...keys].sort());
  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    const cacheKey = `cloverboard:colors:${workspaceId}${writable ? "" : ":readonly"}`;
    async function load() {
      try {
        let next = await getJson<ColorRegistry>("/api/workspace/colors", writable ? { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keys }) } : undefined);
        if (cancelled) return;
        if (!writable) {
          try {
            const cached = JSON.parse(localStorage.getItem(cacheKey) ?? "null") as ColorRegistry | null;
            if (cached?.version === 1 && typeof cached.seed === "string" && cached.slots && (cached.seed === next.seed || Object.keys(next.slots).length === 0)) {
              if (Object.keys(next.slots).length === 0) next = cached;
              else for (const [key, slot] of Object.entries(cached.slots)) if (!(key in next.slots) && Number.isInteger(slot) && slot >= 0 && !Object.values(next.slots).includes(slot)) next.slots[key] = slot;
            }
          } catch { /* Browser storage is optional. */ }
        }
        next = extendRegistry(next, keys);
        setRegistry(next);
        setLocalOnly(false);
        try { localStorage.setItem(cacheKey, JSON.stringify(next)); } catch { /* Browser storage is optional. */ }
      } catch {
        if (!cancelled) { setRegistry((previous) => extendRegistry(previous, keys)); setLocalOnly(true); }
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [workspaceId, writable, keyString, retryTick]);
  const complete = useMemo(() => extendRegistry(registry, keys), [registry, keyString]);
  return { registry: complete, colors: useMemo(() => resolveColors(runs, complete, settings, theme), [runs, complete, settings, theme]), localOnly };
}
