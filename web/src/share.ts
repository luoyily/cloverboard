import { encodeSharedState, viewStateKey, type SavedView, type WorkspaceState } from "./workspace";
import type { ActiveView } from "./viewingConfig";

export type ShareTarget =
  | { kind: "view"; viewId: string }
  | { kind: "state"; encoded: string }
  | { kind: "too-large" };

// A shared View that still matches the session can be linked by ID so the
// recipient follows later View updates. Everything else becomes a snapshot.
export function shareTarget(state: WorkspaceState, activeView: ActiveView | null, sharedViews: SavedView[]): ShareTarget {
  if (activeView?.scope === "shared") {
    const view = sharedViews.find(item => item.view_id === activeView.view_id);
    if (view && viewStateKey(view.state) === viewStateKey(state)) return { kind: "view", viewId: view.view_id };
  }
  try { return { kind: "state", encoded: encodeSharedState(state) }; }
  catch { return { kind: "too-large" }; }
}
