import type { CSSProperties } from "react";
import { CaretDownIcon, SlidersHorizontalIcon } from "@phosphor-icons/react";
import Popover from "./Popover";
import CheckControl from "./CheckControl";
import SegmentedControl from "./SegmentedControl";
import WarmupControl from "./WarmupControl";
import type { TimeDisplay, DeltaDisplay } from "../features";
import type { ReadoutPlacement } from "../readout";

interface ChartToolbarProps {
  metricFilter: string; onMetricFilterChange: (value: string) => void;
  density: "normal" | "compact"; onDensityChange: (value: "normal" | "compact") => void;
  showLegend: boolean; onShowLegendChange: (value: boolean) => void;
  yScale: "linear" | "log"; onYScaleChange: (value: "linear" | "log") => void;
  warmup: number | null; onWarmupChange: (step: number | null) => void;
  smoothing: number; onSmoothingChange: (value: number) => void;
  tooltipDecimals: number; onTooltipDecimalsChange: (value: number) => void;
  tooltipSort: "value" | "list"; onTooltipSortChange: (value: "value" | "list") => void;
  tooltipPlacement: ReadoutPlacement; onTooltipPlacementChange: (value: ReadoutPlacement) => void;
  timeDisplay: TimeDisplay; onTimeDisplayChange: (value: TimeDisplay) => void;
  deltaDisplay: DeltaDisplay; onDeltaDisplayChange: (value: DeltaDisplay) => void;
}

export default function ChartToolbar({ metricFilter, onMetricFilterChange, density, onDensityChange, showLegend, onShowLegendChange, yScale, onYScaleChange, warmup, onWarmupChange, smoothing, onSmoothingChange, tooltipDecimals, onTooltipDecimalsChange, tooltipSort, onTooltipSortChange, tooltipPlacement, onTooltipPlacementChange, timeDisplay, onTimeDisplayChange, deltaDisplay, onDeltaDisplayChange }: ChartToolbarProps) {
  const showBase = deltaDisplay === "baseline" || deltaDisplay === "both";
  const showTarget = deltaDisplay === "target" || deltaDisplay === "both";
  function setDelta(baseline: boolean, target: boolean) {
    onDeltaDisplayChange(baseline && target ? "both" : baseline ? "baseline" : target ? "target" : "hidden");
  }
  return <Popover className="chart-settings-menu" label={`Chart settings, smoothing: ${smoothing.toFixed(2)}`} trigger={<><SlidersHorizontalIcon size={16} aria-hidden="true" /><span>Chart settings</span><CaretDownIcon size={12} aria-hidden="true" /></>}>
    <div className="menu-heading">Charts</div>
    <div className="settings-form compact-settings">
      <label className="setting-row"><span>Filter</span><input className="text-input" type="search" aria-label="Filter metrics" value={metricFilter} onChange={(event) => onMetricFilterChange(event.target.value)} placeholder="e.g. psnr" /></label>
      <div className="setting-row"><span className="field-label">Density</span><SegmentedControl label="Chart density" value={density} options={[{ value: "normal", label: "Normal" }, { value: "compact", label: "Compact" }]} onChange={onDensityChange} /></div>
      <div className="setting-row"><span className="field-label">Legend</span><CheckControl checked={showLegend} onChange={onShowLegendChange}>Show legend</CheckControl></div>
      <div className="setting-row"><span className="field-label">Y scale</span><SegmentedControl label="Y scale" value={yScale} options={[{ value: "linear", label: "Linear" }, { value: "log", label: "Log₁₀" }]} onChange={onYScaleChange} /></div>
      <div className="setting-row"><span className="field-label">Y range</span><SegmentedControl label="Y range" value={warmup === null ? "auto" : "warmup"} options={[{ value: "auto", label: "Auto" }, { value: "warmup", label: "Ignore warmup" }]} onChange={(mode) => onWarmupChange(mode === "auto" ? null : warmup ?? 0)} /></div>
      {warmup !== null && <WarmupControl step={warmup} onChange={onWarmupChange} />}
      <div className="smoothing-heading"><label htmlFor="smoothing-weight">Smoothing</label><output className="smoothing-output" htmlFor="smoothing-weight">{smoothing.toFixed(2)}</output></div>
      <input id="smoothing-weight" className="smoothing-slider" type="range" min={0} max={.95} step={.05} value={smoothing} style={{ "--range-fill": `${smoothing / .95 * 100}%` } as CSSProperties} onChange={(event) => onSmoothingChange(Number(event.target.value))} />
      <p className="field-help">0 shows raw values. Y range keeps all steps visible.</p>
    </div>
    <details className="settings-section"><summary>Hover</summary><div className="settings-form compact-settings">
      <div className="setting-row"><span className="field-label">Placement</span><SegmentedControl label="Hover placement" value={tooltipPlacement} options={[{ value: "vertical", label: "Vertical first" }, { value: "horizontal", label: "Horizontal first" }]} onChange={onTooltipPlacementChange} /></div>
      <label className="setting-row"><span>Decimals</span><input className="text-input number-input" type="number" aria-label="Hover decimal places" min={0} max={8} value={tooltipDecimals} onChange={(event) => { const value = Number(event.target.value); if (event.target.value && Number.isInteger(value) && value >= 0 && value <= 8) onTooltipDecimalsChange(value); }} /></label>
      <div className="setting-row"><span className="field-label">Order</span><SegmentedControl label="Hover order" value={tooltipSort} options={[{ value: "value", label: smoothing > 0 ? "EMA ↓" : "Raw ↓" }, { value: "list", label: "Run list" }]} onChange={onTooltipSortChange} /></div>
      <label className="setting-row"><span>Time</span><select className="text-input" aria-label="Hover time" value={timeDisplay} onChange={(event) => onTimeDisplayChange(event.target.value as TimeDisplay)}><option value="hidden">Hidden</option><option value="elapsed">Elapsed</option><option value="wall">Wall time</option></select></label>
      <div className="setting-row"><span className="field-label">Differences</span><div className="delta-options"><CheckControl checked={showBase} onChange={(checked) => setDelta(checked, showTarget)}>Δ Base</CheckControl><CheckControl checked={showTarget} onChange={(checked) => setDelta(showBase, checked)}>Δ Target</CheckControl></div></div>
      <p className="field-help">Baseline stays first. Differences use raw values.</p>
    </div></details>
  </Popover>;
}
