import { EyeIcon, CaretDownIcon } from "@phosphor-icons/react";
import type { TopK } from "../features";
import CheckControl from "./CheckControl";
import Popover from "./Popover";
import SegmentedControl from "./SegmentedControl";

export default function DisplayMenu({ settings, metrics, onChange }: { settings: TopK; metrics: string[]; onChange: (settings: TopK) => void }) {
  function patch(values: Partial<TopK>) { onChange({ ...settings, ...values }); }
  return <Popover className="display-menu" label="Display settings" trigger={<><EyeIcon size={15} aria-hidden="true" /><span>Display</span><CaretDownIcon size={11} aria-hidden="true" /></>}>
    <div className="menu-heading">Run emphasis</div>
    <div className="settings-form">
      <CheckControl checked={settings.enabled} onChange={(enabled) => patch({ enabled, metric: settings.metric || metrics[0] || "" })}>Emphasize Top-K</CheckControl>
      {settings.enabled && <>
        <label>Metric<select className="text-input" value={settings.metric} onChange={(e) => patch({ metric: e.target.value })}>{metrics.map((m) => <option key={m}>{m}</option>)}{!metrics.includes(settings.metric) && <option value={settings.metric}>{settings.metric || "No metrics"}</option>}</select></label>
        <div className="setting-row"><span className="field-label">Direction</span><SegmentedControl label="Top-K direction" value={settings.direction} options={[{ value: "max", label: "Higher" }, { value: "min", label: "Lower" }]} onChange={(direction) => patch({ direction })} /></div>
        <div className="setting-row"><span className="field-label">Readout</span><SegmentedControl label="Top-K readout" value={settings.readout} options={[{ value: "latest", label: "Latest raw" }, { value: "best", label: "Best raw" }]} onChange={(readout) => patch({ readout })} /></div>
        <label className="setting-row"><span>Runs</span><input type="number" className="text-input number-input" aria-label="Runs to emphasize" min={1} max={1000} value={settings.k} onChange={(e) => { const k = Number(e.target.value); if (e.target.value && Number.isInteger(k) && k > 0 && k <= 1000) patch({ k }); }} /></label>
      </>}
      <p className="field-help">Emphasize top runs in Charts and Table. Colors stay fixed.</p>
      {settings.enabled && <p className="field-help">The baseline stays visible.</p>}
    </div>
  </Popover>;
}
