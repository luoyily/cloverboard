import { useEffect, useId, useState } from "react";
import { DotsThreeIcon } from "@phosphor-icons/react";
import type { ChartZoom } from "../features";
import { tailStepRange } from "../chartMath";
import Popover from "./Popover";
import SegmentedControl from "./SegmentedControl";
import WarmupControl from "./WarmupControl";

interface MetricMenuProps {
  metric: string;
  scaleOverride?: "linear" | "log";
  onScale: (scale: "linear" | "log" | null) => void;
  zoom?: ChartZoom;
  onZoom: (zoom: ChartZoom | null) => void;
  warmupOverride?: number | null;
  globalWarmup: number | null;
  onWarmup: (step: number | null | undefined) => void;
  stepExtent?: [number, number];
  fitNote?: string;
}

export default function MetricMenu({ metric, scaleOverride, onScale, zoom, onZoom, warmupOverride, globalWarmup, onWarmup, stepExtent, fitNote }: MetricMenuProps) {
  const initialRange = () => [String(zoom?.x_min ?? stepExtent?.[0] ?? ""), String(zoom?.x_max ?? stepExtent?.[1] ?? "")];
  const [range, setRange] = useState(initialRange), [error, setError] = useState("");
  const errorId = useId();
  useEffect(() => { setRange(initialRange()); setError(""); }, [zoom?.x_min, zoom?.x_max, stepExtent?.[0], stepExtent?.[1]]);
  const rangeMode = warmupOverride === undefined ? "global" : warmupOverride === null ? "auto" : "warmup";
  function selectRange(percent?: 20 | 50) {
    setError("");
    if (percent === undefined) { onZoom(null); setRange(stepExtent?.map(String) ?? ["", ""]); return; }
    if (!stepExtent || stepExtent[0] >= stepExtent[1]) return;
    const [from, to] = tailStepRange(stepExtent, percent);
    setRange([String(from), String(to)]);
    onZoom({ x_min: from, x_max: to, y_min: null, y_max: null });
  }
  function commitRange() {
    const [from, to] = range.map(Number);
    if (range.some((value) => !value.trim()) || !Number.isSafeInteger(from) || !Number.isSafeInteger(to) || from >= to) { setError("Enter two increasing whole steps."); return; }
    setError("");
    if (from === (zoom?.x_min ?? stepExtent?.[0]) && to === (zoom?.x_max ?? stepExtent?.[1])) return;
    onZoom({ x_min: from, x_max: to, y_min: null, y_max: null });
  }
  return <Popover className="metric-menu" label={`Settings for ${metric}`} trigger={<DotsThreeIcon size={18} />}>
    <div className="settings-form compact-settings">
      <div className="setting-row"><span className="field-label">Y scale</span><SegmentedControl label={`Y scale for ${metric}`} value={scaleOverride ?? "global"} options={[{ value: "global", label: "Global" }, { value: "linear", label: "Linear" }, { value: "log", label: "Log₁₀" }]} onChange={(scale) => onScale(scale === "global" ? null : scale)} /></div>
      <div className="setting-row"><span className="field-label">Y range</span><SegmentedControl label={`Y range for ${metric}`} value={rangeMode} options={[{ value: "global", label: "Global" }, { value: "auto", label: "Auto" }, { value: "warmup", label: "Ignore warmup" }]} onChange={(mode) => onWarmup(mode === "global" ? undefined : mode === "auto" ? null : warmupOverride ?? 0)} /></div>
      {rangeMode === "warmup" && <WarmupControl step={warmupOverride!} onChange={onWarmup} />}
      {rangeMode === "global" && globalWarmup !== null && <p className="field-help">Global · Y fit from step {globalWarmup}</p>}
      <div className="setting-row"><span className="field-label">Step range</span><div className="setting-value range-shortcuts" role="group" aria-label={`Step range shortcuts for ${metric}`}>
        <button className="button quiet-button" type="button" onClick={() => selectRange()}>Full</button>
        {([50, 20] as const).map((percent) => <button className="button quiet-button" type="button" key={percent} disabled={!stepExtent || stepExtent[0] >= stepExtent[1]} onClick={() => selectRange(percent)}>Last {percent}%</button>)}
      </div></div>
      <div className="setting-row"><span className="field-label">Step</span><div className="step-range setting-value" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) commitRange(); }} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); commitRange(); } }}>
        {([0, 1] as const).map((index) => <input key={index} className="text-input" type="number" step={1} aria-label={index === 0 ? "From step" : "To step"} aria-invalid={Boolean(error)} aria-describedby={error ? errorId : undefined} value={range[index]} onChange={(event) => { setRange(range.map((value, i) => i === index ? event.target.value : value)); setError(""); }} />)}
      </div></div>
      {error && <p className="field-help data-error" role="alert" id={errorId}>{error}</p>}
      {fitNote && <p className="field-help" role="status">{fitNote}</p>}
    </div>
  </Popover>;
}
