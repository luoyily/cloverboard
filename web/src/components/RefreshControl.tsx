import { ArrowClockwiseIcon, CaretDownIcon } from "@phosphor-icons/react";
import Popover from "./Popover";

export default function RefreshControl({ interval, onRefresh, onInterval }: { interval: number; onRefresh: () => void; onInterval: (seconds: number) => void }) {
  const status = interval ? `Auto every ${interval}s` : "Auto refresh off";
  return <div className="refresh-control">
    <button type="button" className="button icon-button" onClick={onRefresh} aria-label="Refresh" title={`Refresh now · ${status}`}><ArrowClockwiseIcon size={16} aria-hidden="true" /></button>
    <Popover className="refresh-menu" label={`Refresh settings, ${status}`} trigger={<>{interval > 0 && <span>{interval}s</span>}<CaretDownIcon size={11} aria-hidden="true" /></>}>
      <div className="menu-heading">Refresh</div>
      <div className="settings-form compact-settings"><label className="setting-row"><span>Auto refresh</span><select className="text-input" aria-label="Auto refresh" value={interval} onChange={(event) => onInterval(Number(event.target.value))}>{[0, 5, 10, 30, 60].map((seconds) => <option key={seconds} value={seconds}>{seconds ? `Every ${seconds}s` : "Off"}</option>)}</select></label>
      <p className="field-help">Applies to Charts, Table and Media. Pauses in background tabs.</p>
      </div>
    </Popover>
  </div>;
}
