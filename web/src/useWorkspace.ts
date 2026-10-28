import { useEffect, useState, useSyncExternalStore } from "react";
import { getJson } from "./api";
import { WorkspaceSession } from "./workspaceSession";

export function useWorkspace() {
  const [session] = useState(() => new WorkspaceSession(getJson, () => localStorage));
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const { remote } = snapshot;
  const [loadTick, setLoadTick] = useState(0);
  useEffect(() => {
    void session.load(window.location.href).then(fromLink => {
      if (!fromLink) return;
      const clean = new URL(window.location.href); clean.hash = ""; clean.searchParams.delete("view");
      window.history.replaceState(null, "", clean);
    });
    const flush = () => { void session.flushRemote(true); };
    window.addEventListener("pagehide", flush);
    return () => { window.removeEventListener("pagehide", flush); session.stop(); };
  }, [session, loadTick]);
  const config = session.getConfig();
  return {
    ...snapshot, config, views: config.views, palettes: config.palettes, sharedViews: remote?.workspace.views ?? [], setMode: session.setMode,
    writable: remote?.workspace.writable ?? false, workspaceId: remote?.workspace.workspace_id ?? "",
    remoteError: snapshot.stateError,
    update: session.update, openView: session.openView,
    saveView: session.saveView, renameView: session.renameView, deleteView: session.deleteView,
    savePalette: session.savePalette, deletePalette: session.deletePalette,
    importConfig: session.importConfig, restoreRemote: session.restoreRemote,
    retryStorage: session.retryStorage, retryRemote: session.retryRemote, retry: () => setLoadTick(tick => tick + 1),
  };
}
