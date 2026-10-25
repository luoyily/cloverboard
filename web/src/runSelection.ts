import type { RunSummary } from "./api";
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

export function selectionPatch(state: WorkspaceState, selectedIds: Set<string>, ids: string[], selected: boolean): Partial<WorkspaceState> {
  const next = new Set(selectedIds);
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
