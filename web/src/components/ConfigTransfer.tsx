import { useRef, useState } from "react";
import type { SettingsMode } from "../workspaceSession";
import { exportConfig, MAX_CONFIG_BYTES, type ViewingConfig } from "../viewingConfig";

export default function ConfigTransfer({ config, mode, onImport, onRestore }: {
  mode: SettingsMode;
  config: ViewingConfig;
  onImport: (text: string) => Promise<void>;
  onRestore: () => Promise<void>;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false), [confirmRestore, setConfirmRestore] = useState(false);
  const [error, setError] = useState(""), [message, setMessage] = useState("");
  async function perform(operation: () => Promise<void>, success: string) {
    setBusy(true); setError(""); setMessage("");
    try { await operation(); setMessage(success); setConfirmRestore(false); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update viewing configuration."); }
    finally { setBusy(false); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([exportConfig(config)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = "cloverboard-config.json";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div className="config-transfer-actions">
    <div className="menu-heading">Configuration</div>
    <button className="menu-item" disabled={busy} onClick={download}>Export Views &amp; palettes (JSON)</button>
    <button className="menu-item" disabled={busy} onClick={() => input.current?.click()}>Import JSON…</button>
    <input ref={input} type="file" accept=".json,application/json" hidden aria-label="Import viewing configuration" onChange={event => {
      const file = event.currentTarget.files?.[0]; event.currentTarget.value = "";
      if (!file) return;
      void perform(async () => {
        if (file.size > MAX_CONFIG_BYTES) throw new Error("Choose a configuration file smaller than 2 MB.");
        await onImport(await file.text());
      }, "Imported.");
    }} />
    {mode === "local" && <button className="menu-item" disabled={busy} onClick={() => { setConfirmRestore(!confirmRestore); setError(""); setMessage(""); }}>Restore remote settings…</button>}
    {confirmRestore && <div className="view-delete-confirm">
      <p>Restore remote viewing settings?</p>
      <div className="form-actions"><button className="button quiet-button" disabled={busy} onClick={() => setConfirmRestore(false)}>Cancel</button><button className="button primary-button" disabled={busy} onClick={() => void perform(onRestore, "Restored.")}>{busy ? "Restoring…" : "Restore remote"}</button></div>
    </div>}
    {message && <p className="menu-note" role="status">{message}</p>}
    {error && <p className="menu-error" role="alert">{error}</p>}
  </div>;
}
