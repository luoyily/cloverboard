import { useRef, useState } from "react";
import type { SavedView, WorkspaceState } from "../workspace";
import { exportLocalFile, MAX_CONFIG_BYTES } from "../viewingConfig";

export default function ConfigTransfer({ state, views, onImport }: {
  state: WorkspaceState;
  views: SavedView[];
  onImport: (text: string) => Promise<void>;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(""), [message, setMessage] = useState("");
  async function perform(operation: () => Promise<void>, success: string) {
    setBusy(true); setError(""); setMessage("");
    try { await operation(); setMessage(success); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update viewing configuration."); }
    finally { setBusy(false); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([exportLocalFile(state, views)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = "cloverboard-local-config.json";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div className="config-transfer-actions">
    <div className="menu-heading">Configuration</div>
    <button className="menu-item" disabled={busy} onClick={download}>Export my Views &amp; settings (JSON)</button>
    <button className="menu-item" disabled={busy} onClick={() => input.current?.click()}>Import JSON…</button>
    <input ref={input} type="file" accept=".json,application/json" hidden aria-label="Import viewing configuration" onChange={event => {
      const file = event.currentTarget.files?.[0]; event.currentTarget.value = "";
      if (!file) return;
      void perform(async () => {
        if (file.size > MAX_CONFIG_BYTES) throw new Error("Choose a configuration file smaller than 2 MB.");
        await onImport(await file.text());
      }, "Imported.");
    }} />
    {message && <p className="menu-note" role="status">{message}</p>}
    {error && <p className="menu-error" role="alert">{error}</p>}
  </div>;
}
