import Select from "./Select";
import { useId, useState } from "react";
import { PencilSimpleIcon, TargetIcon } from "@phosphor-icons/react";
import { activeTarget, removeTarget, saveTarget, type MetricTargets } from "../targets";
import type { WorkspaceState } from "../workspace";
import { formatMetric } from "../comparison";
import Popover from "./Popover";
import TargetEditor from "./TargetEditor";

export function TargetCell({ metric, group, decimals, onChange }: { metric: string; group?: MetricTargets; decimals: number; onChange: (group: MetricTargets) => void }) {
  const target = activeTarget(group);
  return <Popover className="target-cell-menu" label={`Edit target for ${metric}`} trigger={target ? `${target.direction === "gte" ? "≥" : "≤"} ${formatMetric(target.value, decimals)}` : "Set target"}>
    <div className="menu-heading">{metric}</div>
    <MetricTargetsEditor metric={metric} group={group} onChange={onChange} />
  </Popover>;
}

function MetricTargetsEditor({ metric, group, onChange }: { metric: string; group?: MetricTargets; onChange: (group: MetricTargets) => void }) {
  const radioName = useId();
  const [editor, setEditor] = useState<{ id?: string } | null>(null);
  const items = group?.items ?? [];
  const editing = items.find((item) => item.id === editor?.id);
  function closeEditor() { setEditor(null); }
  return <div className="metric-targets-editor">
    <div className="target-list-heading"><span className="field-label">Saved targets</span><button className="text-button" onClick={() => setEditor({})}>Add target</button></div>
    {items.length > 0 ? <div className="target-list" role="group" aria-label={`Targets for ${metric}`}>
      <label className="target-choice"><input type="radio" name={radioName} checked={!group?.active_id} onChange={() => onChange({ items, active_id: null })} /><span>No active target</span></label>
      {items.map((item) => <div className="target-list-row" key={item.id}>
        <label className="target-choice"><input type="radio" name={radioName} checked={group?.active_id === item.id} onChange={() => onChange({ items, active_id: item.id })} /><span>{item.name}</span><small>{item.direction === "gte" ? "≥" : "≤"} {formatMetric(item.value)}</small></label>
        <button className="button icon-button" aria-label={`Edit target ${item.name}`} onClick={() => setEditor({ id: item.id })}><PencilSimpleIcon size={14} /></button>
      </div>)}
    </div> : <p className="menu-note">No targets for this metric.</p>}
    {editor && <TargetEditor key={editor.id ?? "new"} metric={metric} target={editing} onCancel={closeEditor}
      onSave={(target) => { onChange(saveTarget(group, target)); closeEditor(); }}
      onRemove={() => { if (group && editing) onChange(removeTarget(group, editing.id)); closeEditor(); }} />}
  </div>;
}

export default function TargetsMenu({ metrics, state, onChange }: { metrics: string[]; state: WorkspaceState; onChange: (patch: Partial<WorkspaceState>) => void }) {
  const [chosen, setChosen] = useState("");
  const names = [...new Set([...metrics, ...Object.keys(state.targets)])].sort();
  const metric = names.includes(chosen) ? chosen : names[0];
  function save(name: string, group: MetricTargets) {
    const targets = { ...state.targets };
    if (group.items.length) targets[name] = group; else delete targets[name];
    onChange({ targets });
  }
  const count = Object.values(state.targets).reduce((total, group) => total + group.items.length, 0);
  return <Popover className="targets-menu" label="Targets" trigger={<><TargetIcon size={16} /><span>Targets</span>{count > 0 && <small>{count}</small>}</>}>
    <div className="menu-heading">Target reference values</div><p className="menu-note">Shared by Charts and Table. One active target per metric.</p>
    {metric ? <><label className="target-metric-choice">Metric<Select aria-label="Target metric" value={metric} onChange={(event) => setChosen(event.target.value)}>{names.map((name) => <option key={name}>{name}</option>)}</Select></label>
      <MetricTargetsEditor key={metric} metric={metric} group={state.targets[metric]} onChange={(group) => save(metric, group)} />
    </> : <p className="menu-note">Select a run with metrics to set a target.</p>}
  </Popover>;
}
