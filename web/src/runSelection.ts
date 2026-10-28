import type { RunCatalog, RunSummary } from "./api";
import type { WorkspaceState } from "./workspace";

export function sortRuns(runs: RunSummary[], order: WorkspaceState["run_sort"]): RunSummary[] {
  return [...runs].sort((a, b) => {
    let difference = 0;
    if (order === "name_asc" || order === "name_desc") {
      difference = (a.name ?? a.run_id).localeCompare(b.name ?? b.run_id, undefined, { numeric: true });
      if (order === "name_desc") difference = -difference;
    } else difference = order === "started_asc" ? a.started_at - b.started_at : b.started_at - a.started_at;
    return difference || a.source.localeCompare(b.source) || a.run_id.localeCompare(b.run_id);
  });
}

export function recentRunIds(runs: RunSummary[], count = 3): string[] {
  return sortRuns(runs.filter((run) => run.has_scalars), "started_desc").slice(0, count).map((run) => run.run_id);
}

export function availableSelection(state: WorkspaceState, runs: RunSummary[]): Set<string> {
  const available = new Set(runs.map(run => run.run_id));
  const selected = new Set((state.selected_ids ?? recentRunIds(runs)).filter(id => available.has(id)));
  if (state.baseline_id && available.has(state.baseline_id)) selected.add(state.baseline_id);
  return selected;
}

// Only a complete catalog can confirm deletion. Failed sources keep their saved
// choices, while a source absent from this instance's configuration is removed.
export function reconcileSelection(state: WorkspaceState, catalog: RunCatalog): Partial<WorkspaceState> | null {
  const available = new Set(catalog.runs.map(run => run.run_id));
  const sources = new Map(catalog.sources.map(source => [source.source_id, source]));
  const keep = (id: string) => {
    if (available.has(id)) return true;
    const separator = id.indexOf(":");
    const source = sources.get(separator < 0 ? "local" : id.slice(0, separator));
    return source?.online === false;
  };
  const patch: Partial<WorkspaceState> = {};
  if (state.selected_ids !== null) {
    const selected = state.selected_ids.filter(keep);
    if (selected.length !== state.selected_ids.length) patch.selected_ids = selected;
  }
  if (state.baseline_id && !keep(state.baseline_id)) patch.baseline_id = null;
  return Object.keys(patch).length ? patch : null;
}

export function selectionPatch(state: WorkspaceState, selectedIds: Set<string>, ids: string[], selected: boolean): Partial<WorkspaceState> {
  const next = new Set(state.selected_ids ?? selectedIds);
  if (state.baseline_id) next.add(state.baseline_id);
  for (const id of ids) {
    if (selected) next.add(id);
    else next.delete(id);
  }
  return {
    selected_ids: [...next],
    baseline_id: state.baseline_id && next.has(state.baseline_id) ? state.baseline_id : null,
  };
}

export function replaceSelection(state: WorkspaceState, ids: string[]): Partial<WorkspaceState> {
  return { selected_ids: ids, baseline_id: state.baseline_id && ids.includes(state.baseline_id) ? state.baseline_id : null };
}

export interface RunSearch {
  matches: (run: RunSummary) => boolean;
  error: string | null;
}

export function createRunSearch(text: string, regex: boolean): RunSearch {
  const query = regex ? text : text.trim();
  let pattern: RegExp;
  try {
    pattern = new RegExp(regex ? query : query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
  } catch (reason) {
    return { matches: () => false, error: reason instanceof Error ? reason.message : "Invalid regular expression" };
  }
  return {
    matches: (run) => [run.name ?? run.run_id, run.run_id, run.source_label ?? run.source].some((value) => pattern.test(value)),
    error: null,
  };
}
