import { SlidersHorizontalIcon, CaretDownIcon } from "@phosphor-icons/react";
import type { WorkspaceState } from "../workspace";
import Popover from "./Popover";

export default function ImageSettings({ state, onChange }: { state: WorkspaceState; onChange: (patch: Partial<WorkspaceState>) => void }) {
  return <Popover className="image-settings-menu" label="Media settings" trigger={<><SlidersHorizontalIcon size={16} /><span>Media settings</span><CaretDownIcon size={12} /></>}>
    <div className="menu-heading">Media</div><div className="settings-form compact-settings">
      <label className="setting-row"><span>Timeline</span><select className="text-input number-input" aria-label="Timeline samples" value={state.image_timeline_samples} onChange={(event) => onChange({ image_timeline_samples: Number(event.target.value) })}>{[50, 100, 200, 500].map((value) => <option key={value}>{value}</option>)}</select></label>
      <p className="field-help">Sampled steps per run and name. Any step can be entered directly.</p>
      {state.media_type !== "text" && <><label className="setting-row"><span>Per step</span><select className="text-input number-input" aria-label="Records per step" value={state.images_per_step} onChange={(event) => onChange({ images_per_step: Number(event.target.value) })}>{[4, 8, 12, 24].map((value) => <option key={value}>{value}</option>)}</select></label>
      <p className="field-help">First page per run. Load more for additional images or audio.</p></>}
    </div>
  </Popover>;
}
