import { useState } from "react";
import type { NamedTarget } from "../targets";
import SegmentedControl from "./SegmentedControl";

export default function TargetEditor({ metric, target, onSave, onRemove, onCancel }: { metric: string; target?: NamedTarget; onSave: (value: NamedTarget) => void; onRemove: () => void; onCancel: () => void }) {
  const [name, setName] = useState(target?.name ?? "");
  const [chartLabel, setChartLabel] = useState(target?.chart_label ?? "");
  const [value, setValue] = useState(target ? String(target.value) : "");
  const [direction, setDirection] = useState<NamedTarget["direction"]>(target?.direction ?? "gte");
  const [error, setError] = useState("");
  const dirty = !target || name.trim() !== target.name || chartLabel.trim() !== target.chart_label || Number(value) !== target.value || direction !== target.direction;
  return <form className="settings-form target-editor" onSubmit={(event) => {
    event.preventDefault();
    const number = Number(value);
    if (!name.trim()) { setError("Enter a target name."); return; }
    if (!value.trim() || !Number.isFinite(number)) { setError("Enter a finite target value."); return; }
    onSave({ id: target?.id ?? crypto.randomUUID(), name: name.trim(), chart_label: chartLabel.trim(), value: number, direction });
  }}>
    <label className="setting-row"><span>Name</span><input className="text-input" aria-label={`Target name for ${metric}`} value={name} onChange={(event) => { setName(event.target.value); setError(""); }} autoFocus /></label>
    <label className="setting-row"><span>Chart label</span><input className="text-input" aria-label={`Target chart label for ${metric}`} placeholder="Use name" value={chartLabel} onChange={(event) => setChartLabel(event.target.value)} /></label>
    <p className="field-help">Optional short name for the Y axis.</p>
    <div className="setting-row"><span className="field-label">Target</span><div className="target-value setting-value"><SegmentedControl label={`Target direction for ${metric}`} value={direction} options={[{ value: "gte", label: "≥", title: "At least" }, { value: "lte", label: "≤", title: "At most" }]} onChange={setDirection} />
      <input className="text-input" type="number" step="any" aria-label={`Target value for ${metric}`} value={value} onChange={(event) => { setValue(event.target.value); setError(""); }} /></div></div>
    <div className="target-actions">{target && <button className="button quiet-button" type="button" onClick={onRemove}>Remove</button>}<button className="button quiet-button" type="button" onClick={onCancel}>Cancel</button><button className="button" type="submit" disabled={!dirty}>Save target</button></div>
    {error && <p className="menu-error" role="alert">{error}</p>}
  </form>;
}
