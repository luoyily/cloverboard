import { GearSixIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import type { SavedView, WorkspaceState } from "../workspace";
import Popover from "./Popover";
import ConfigTransfer from "./ConfigTransfer";

export default function SettingsMenu({ ready, state, views, onImport, children }: {
  ready: boolean;
  state: WorkspaceState;
  views: SavedView[];
  onImport: (text: string) => Promise<void>;
  children: ReactNode;
}) {
  return <Popover className="general-settings-menu" label="Settings" trigger={<GearSixIcon size={16} />}>
    <div className="menu-heading">Settings</div>
    <p className="menu-note">Viewing settings and My Views are saved in this browser. Targets, palettes and shared Views come from the workspace.</p>
    {children}
    {ready && <ConfigTransfer state={state} views={views} onImport={onImport} />}
  </Popover>;
}
