import { useEffect, useState, useSyncExternalStore } from "react";
import { getJson } from "./api";
import { WorkspaceSession } from "./workspaceSession";

export function useWorkspace() {
  const [session] = useState(() => new WorkspaceSession(getJson, () => localStorage));
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const [loadTick, setLoadTick] = useState(0);
  useEffect(() => {
    void session.load(window.location.href).then(fromLink => {
      if (!fromLink) return;
      const clean = new URL(window.location.href); clean.hash = ""; clean.searchParams.delete("view");
      window.history.replaceState(null, "", clean);
    });
    return () => session.stop();
  }, [session, loadTick]);
  return {
    ...snapshot,
    update: session.update, setRefreshInterval: session.setRefreshInterval, openView: session.openView,
    saveView: session.saveView, renameView: session.renameView, deleteView: session.deleteView, promoteView: session.promoteView,
    saveTargets: session.saveTargets, savePalette: session.savePalette, deletePalette: session.deletePalette,
    importConfig: session.importConfig, retryStorage: session.retryStorage,
    retry: () => setLoadTick(tick => tick + 1),
  };
}
