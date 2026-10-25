import { useEffect, useState } from "react";
import type { Target } from "../features";
import SegmentedControl from "./SegmentedControl";

export default function TargetEditor({ metric, target, onChange, onDone }: { metric: string; target?: Target; onChange: (value: Target | null) => void; onDone?: () => void }) {
  const [value, setValue] = useState(target ? String(target.value) : "");
  const [direction, setDirection] = useState<Target["direction"]>(target?.direction ?? "gte");
  const [error, setError] = useState("");
  useEffect(() => { setValue(target ? String(target.value) : ""); setDirection(target?.direction ?? "gte"); setError(""); }, [metric, target?.value, target?.direction]);
  const dirty = value.trim() !== "" && (!target || Number(value) !== target.value || direction !== target.direction);
  return <form className="settings-form target-editor" onSubmit={(event) => {
    event.preventDefault();
    const number = Number(value);
    if (!value.trim() || !Number.isFinite(number)) { setError("Enter a finite target value."); return; }
    onChange({ value: number, direction }); setError(""); onDone?.();
  }}>
    <div className="setting-row"><span className="field-label">Target</span><div className="target-value setting-value"><SegmentedControl label={`Target direction for ${metric}`} value={direction} options={[{ value: "gte", label: "≥", title: "At least" }, { value: "lte", label: "≤", title: "At most" }]} onChange={setDirection} />
      <input className="text-input" type="number" step="any" aria-label={`Target value for ${metric}`} value={value} onChange={(event) => { setValue(event.target.value); setError(""); }} /></div></div>
    <div className="target-actions">{target && <button className="button quiet-button" type="button" onClick={() => { onChange(null); onDone?.(); }}>Remove target</button>}<button className="button" type="submit" disabled={!dirty}>Save target</button></div>
    {error && <p className="menu-error" role="alert">{error}</p>}
  </form>;
}
