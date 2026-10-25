import { useRef, useState } from "react";
import { TargetIcon } from "@phosphor-icons/react";
import type { Target } from "../features";
import type { WorkspaceState } from "../workspace";
import { formatMetric } from "../comparison";
import Popover from "./Popover";
import TargetEditor from "./TargetEditor";

export function TargetCell({ metric, target, decimals, onChange }: { metric: string; target?: Target; decimals: number; onChange: (target: Target | null) => void }) {
  const root = useRef<HTMLDivElement>(null);
  return <div ref={root}><Popover className="target-cell-menu" label={`Edit target for ${metric}`} trigger={target ? `${target.direction === "gte" ? "≥" : "≤"} ${formatMetric(target.value, decimals)}` : "Set target"}>
    <div className="menu-heading">{metric}</div>
    <TargetEditor metric={metric} target={target} onChange={onChange} onDone={() => root.current?.querySelector("details")?.removeAttribute("open")} />
  </Popover></div>;
}

export default function TargetsMenu({ metrics, state, onChange }: { metrics: string[]; state: WorkspaceState; onChange: (patch: Partial<WorkspaceState>) => void }) {
  const [chosen, setChosen] = useState("");
  const names = [...new Set([...metrics, ...Object.keys(state.targets)])].sort();
  const metric = names.includes(chosen) ? chosen : names[0];
  function save(name: string, target: Target | null) {
    const targets = { ...state.targets };
    if (target) targets[name] = target; else delete targets[name];
    onChange({ targets });
  }
  return <Popover className="targets-menu" label="Targets" trigger={<><TargetIcon size={16} /><span>Targets</span>{Object.keys(state.targets).length > 0 && <small>{Object.keys(state.targets).length}</small>}</>}>
    <div className="menu-heading">Target reference values</div><p className="menu-note">Shared by Charts and Table. Attainment uses latest raw.</p>
    {metric ? <><label className="target-metric-choice">Metric<select className="text-input" aria-label="Target metric" value={metric} onChange={(event) => setChosen(event.target.value)}>{names.map((name) => <option key={name}>{name}</option>)}</select></label>
      <TargetEditor metric={metric} target={state.targets[metric]} onChange={(value) => save(metric, value)} />
      {Object.keys(state.targets).length > 0 && <div className="configured-targets"><div className="settings-divider" /><div className="menu-heading">Configured targets</div>{Object.entries(state.targets).map(([name, value]) => <button className="menu-item" key={name} onClick={() => setChosen(name)}><span>{name}</span><span>{value.direction === "gte" ? "≥" : "≤"} {formatMetric(value.value)}</span></button>)}</div>}</> : <p className="menu-note">Select a run with metrics to set a target.</p>}
  </Popover>;
}
