import Select from "./Select";
import { CaretDownIcon, ColumnsIcon } from "@phosphor-icons/react";
import type { ParameterColumn } from "../comparison";
import type { WorkspaceState } from "../workspace";
import { deltaColumns } from "../features";
import CheckControl from "./CheckControl";
import Popover from "./Popover";

interface CompareControlsProps {
  state: WorkspaceState;
  metrics: string[];
  chosenMetrics: string[];
  params: ParameterColumn[];
  onChange: (patch: Partial<WorkspaceState>) => void;
}

export default function CompareControls({ state, metrics, chosenMetrics, params, onChange }: CompareControlsProps) {
  const differences = deltaColumns(state.table_delta, true, true);
  function setDifferences(baseline: boolean, target: boolean) {
    let table_delta: WorkspaceState["table_delta"] = "hidden";
    if (baseline && target) table_delta = "both";
    else if (baseline) table_delta = "baseline";
    else if (target) table_delta = "target";
    onChange({ table_delta });
  }
  return <div className="compare-controls">
    <Popover className="columns-menu" label="Table settings" trigger={<>
      <ColumnsIcon size={16} aria-hidden="true" /><span>Table settings</span><CaretDownIcon size={11} aria-hidden="true" />
    </>}>
      <div className="menu-heading">Display</div>
      <div className="settings-form table-display-settings">
      <CheckControl checked={state.table_show_steps} onChange={(table_show_steps) => onChange({ table_show_steps })}>Show steps</CheckControl>
      <CheckControl checked={differences.baseline} onChange={(checked) => setDifferences(checked, differences.target)}>Show Δ Baseline</CheckControl>
      <CheckControl checked={differences.target} onChange={(checked) => setDifferences(differences.baseline, checked)}>Show Δ Target</CheckControl>
      <p className="field-help">Differences can be hidden without removing references.</p>
      <div className="settings-divider" />
      <CheckControl checked={state.differences_only} onChange={(checked) => onChange({ differences_only: checked })}>Different parameters only</CheckControl>
      <label className="setting-row"><span>Decimals</span><input className="text-input number-input" aria-label="Metric decimal places" type="number" min={0} max={8} value={state.table_decimals} onChange={(event) => { const value = Number(event.target.value); if (event.target.value && Number.isInteger(value) && value >= 0 && value <= 8) onChange({ table_decimals: value }); }} /></label>
      <label className="setting-row"><span>Time column</span><Select aria-label="Table time column" value={state.table_time} onChange={(event) => onChange({ table_time: event.target.value as WorkspaceState["table_time"] })}><option value="last_recorded_at">Last recorded</option><option value="started_at">Started</option><option value="hidden">Hidden</option></Select></label>
      <CheckControl checked={state.show_tags_column} onChange={(show_tags_column) => onChange({ show_tags_column })}>Show tags column</CheckControl>
      </div>
      <div className="settings-divider" />
      <div className="column-options">
        <div className="menu-heading">Latest metrics</div>
        {metrics.length === 0 && <p className="menu-note">No metrics recorded.</p>}
        {metrics.map((metric) => <CheckControl key={metric} checked={chosenMetrics.includes(metric)} title={metric} onChange={(checked) => {
          onChange({ metric_columns: checked ? [...chosenMetrics, metric] : chosenMetrics.filter((name) => name !== metric) });
        }}>{metric}</CheckControl>)}
        <div className="menu-heading parameter-options-heading">Parameters</div>
        {params.length === 0 && <p className="menu-note">No parameters recorded.</p>}
        {params.map((param) => <CheckControl key={param.key} label={param.key} checked={!state.hidden_params.includes(param.key)} title={!param.different && state.differences_only ? `${param.key} · Hidden by Differences only` : param.key} onChange={(checked) => {
          onChange({ hidden_params: checked ? state.hidden_params.filter((key) => key !== param.key) : [...state.hidden_params, param.key] });
        }}><span>{param.key}</span>{!param.different && <span className="column-identical">Same</span>}</CheckControl>)}
      </div>
      <button className="menu-item column-reset" type="button" onClick={() => onChange({ metric_columns: null, hidden_params: [] })}>Reset columns</button>
    </Popover>
  </div>;
}
