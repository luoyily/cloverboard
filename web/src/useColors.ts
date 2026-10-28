import { useEffect, useMemo } from "react";
import type { RunSummary } from "./api";
import type { ColorSettings } from "./features";
import { assignColors, resolveColors } from "./colors";
import type { ResolvedTheme } from "./theme";
import type { WorkspaceUpdate } from "./workspace";

export function useColors(runs: RunSummary[], settings: ColorSettings, theme: ResolvedTheme, ready: boolean, update: WorkspaceUpdate) {
  const complete = useMemo(() => assignColors(runs, settings), [runs, settings]);
  useEffect(() => {
    if (ready && complete !== settings) update(current => ({ colors: assignColors(runs, current.colors) }));
  }, [ready, runs, settings, complete, update]);
  return useMemo(() => resolveColors(runs, complete, theme), [runs, complete, theme]);
}
