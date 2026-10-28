import { useState } from "react";
import { GearSixIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import type { ViewingConfig } from "../viewingConfig";
import type { SettingsMode } from "../workspaceSession";
import Popover from "./Popover";
import Select from "./Select";
import ConfigTransfer from "./ConfigTransfer";

export default function SettingsMenu({ mode, writable, ready, config, onMode, onImport, onRestore, children }: {
  mode: SettingsMode; writable: boolean; ready: boolean; config: ViewingConfig;
  onMode: (mode: SettingsMode) => Promise<void>;
  onImport: (text: string) => Promise<void>; onRestore: () => Promise<void>; children: ReactNode;
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  return <Popover className="general-settings-menu" label="Settings" trigger={<GearSixIcon size={16} />}>
    <div className="menu-heading">Settings</div>
    <label className="settings-mode">Save settings to<Select aria-label="Settings mode" value={mode} disabled={busy || !ready || !writable} onChange={event => {
      const next = event.target.value as SettingsMode;
      setBusy(true); setError("");
      void onMode(next).catch(reason => setError(reason instanceof Error ? reason.message : "Could not change mode.")).finally(() => setBusy(false));
    }}>{writable && <option value="remote">Remote workspace</option>}<option value="local">Local · this browser</option></Select></label>
    {children}
    {error && <p className="menu-error" role="alert">{error}</p>}
    {ready && <ConfigTransfer key={mode} mode={mode} config={config} onImport={onImport} onRestore={onRestore} />}
  </Popover>;
}
