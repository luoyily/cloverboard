import { ArrowDownIcon, ArrowUpIcon, TargetIcon } from "@phosphor-icons/react";
import type { CSSProperties } from "react";
import { seriesKey, type RunSummary, type ScalarSummary } from "../api";
import { displayParameter, formatMetric as formatRecordedMetric, runLabel, stableValue, type ParameterColumn } from "../comparison";
import type { WorkspaceState } from "../workspace";
import { baselineDelta, deltaColumns, targetDelta, targetMet } from "../features";
import { activeTarget } from "../targets";
import { TargetCell } from "./TargetsMenu";
import { formatTableTime, formatWallTime } from "../readout";
import RunActions from "./RunActions";

interface CompareTableProps {
  runs: RunSummary[];
  configs: Map<string, Map<string, unknown>>;
  params: ParameterColumn[];
  metrics: string[];
  summaries: Map<string, ScalarSummary>;
  colors: Map<string, string>;
  state: WorkspaceState;
  loading: boolean;
  onChange: (patch: Partial<WorkspaceState>) => void;
  focusedId: string | null;
  onFocusRun: (id: string | null) => void;
  onAppearance: (run: RunSummary) => void;
  onDetails: (run: RunSummary, tab: "overview" | "notes") => void;
  onBaseline: (id: string | null) => void;
  topIds: Set<string> | null;
  onEditTags: (runs: RunSummary[]) => void;
}

export default function CompareTable({ runs, configs, params, metrics, summaries, colors, state, loading, onChange, focusedId, onFocusRun, onAppearance, onDetails, onBaseline, topIds, onEditTags }: CompareTableProps) {
  const formatMetric = (value: number | null | undefined) => formatRecordedMetric(value, state.table_decimals);
  const visibleParams = params.filter((param) => !state.hidden_params.includes(param.key) &&
    (!state.differences_only || param.different || runs.length < 2));
  const timeKey = state.table_time === "hidden" ? null : state.table_time;
  const timeColumn = timeKey !== null;
  const hasTargetRow = metrics.some((metric) => state.targets[metric]?.items.length);
  const differences = deltaColumns(state.table_delta, Boolean(state.baseline_id), true);
  const columns = ["run", ...(timeKey ? [timeKey] : []), ...metrics.map((name) => `metric:${name}`), ...visibleParams.map((param) => `param:${param.key}`)];
  const sortColumn = columns.includes(state.sort_column) ? state.sort_column : "run";
  const SortIcon = state.sort_desc ? ArrowDownIcon : ArrowUpIcon;
  function sortValue(run: RunSummary): unknown {
    if (sortColumn === "run") return runLabel(run);
    if (sortColumn === "last_recorded_at" || sortColumn === "started_at") return run[sortColumn];
    if (sortColumn.startsWith("metric:")) return summaries.get(seriesKey(run.run_id, sortColumn.slice(7)))?.latest?.value;
    return configs.get(run.run_id)?.get(sortColumn.slice(6));
  }
  const sortedRuns = [...runs].sort((a, b) => {
    if (a.run_id === state.baseline_id) return -1;
    if (b.run_id === state.baseline_id) return 1;
    const left = sortValue(a);
    const right = sortValue(b);
    const missing = (value: unknown) => value === undefined || value === null || (typeof value === "number" && !Number.isFinite(value));
    if (missing(left) || missing(right)) return Number(missing(left)) - Number(missing(right));
    const difference = typeof left === "number" && typeof right === "number"
      ? left - right
      : (typeof left === "string" ? left : stableValue(left)).localeCompare(typeof right === "string" ? right : stableValue(right), undefined, { numeric: true });
    return state.sort_desc ? -difference : difference;
  });

  function heading(key: string, label: string) {
    const sorted = sortColumn === key;
    return <th key={key} scope="col" className={key === "run" ? "identity-column" : ""} aria-sort={sorted ? (state.sort_desc ? "descending" : "ascending") : "none"}>
      <div className="comparison-heading"><button type="button" className="column-sort" title={label} aria-label={`Sort by ${label}`} onClick={() => onChange({ sort_column: key, sort_desc: sorted ? !state.sort_desc : false })}>
        <span className="column-title">{label}</span>
        {sorted && <SortIcon size={12} aria-hidden="true" />}
      </button></div>
    </th>;
  }

  return <section className="comparison" aria-label="Run comparison" aria-busy={loading} style={{ "--target-row-height": hasTargetRow ? "48px" : "0px" } as CSSProperties}>
    <div className="comparison-scroll" tabIndex={0} role="region" aria-label="Comparison table, scroll to see more columns">
      <table className="compare-table">
        <caption className="sr-only">Selected experiments, latest raw metric values, and hyperparameters.</caption>
        <thead><tr>
          {heading("run", "Run")}
          {timeKey && heading(timeKey, timeKey === "last_recorded_at" ? "Last recorded" : "Started")}
          {state.show_tags_column && <th scope="col">Tags</th>}
          {metrics.map((metric) => heading(`metric:${metric}`, metric))}
          {visibleParams.map((param) => heading(`param:${param.key}`, param.key))}
        </tr></thead>
        <tbody>
          {hasTargetRow && <tr className="table-target-row" aria-label="Target reference values">
            <th scope="row" className="identity-column"><span className="target-reference-label"><TargetIcon size={16} />Target <small>Reference</small></span></th>
            {timeColumn && <td>—</td>}{state.show_tags_column && <td>—</td>}
            {metrics.map((metric) => <td key={metric}><TargetCell metric={metric} group={state.targets[metric]} decimals={state.table_decimals} onChange={(group) => { const targets = { ...state.targets }; if (group.items.length) targets[metric] = group; else delete targets[metric]; onChange({ targets }); }} /></td>)}
            {visibleParams.map((param) => <td key={param.key}>—</td>)}
          </tr>}
          {sortedRuns.map((run) => <tr key={run.run_id} className={`${run.run_id === state.baseline_id ? "table-baseline-row " : ""}${focusedId === run.run_id ? "run-focused" : ""}${topIds && !topIds.has(run.run_id) && state.baseline_id !== run.run_id ? " ranked-muted" : ""}`} onPointerEnter={() => onFocusRun(run.run_id)} onPointerLeave={() => onFocusRun(null)} onFocus={() => onFocusRun(run.run_id)} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) onFocusRun(null); }}>
          <th scope="row" className="identity-column" title={`${runLabel(run)}\n${run.run_id}`}>
            <div className="compare-run"><button className="run-swatch-button" type="button" aria-label={`Color for ${runLabel(run)}`} title="Change color" onClick={() => onAppearance(run)}><span className="swatch" style={{ backgroundColor: colors.get(run.run_id) }} /></button>
              <span className="compare-run-name">{runLabel(run)}</span>
              {state.baseline_id === run.run_id && <span className="baseline-mark" title="Baseline" aria-label="Baseline">B</span>}
              <RunActions run={run} baselineId={state.baseline_id} onBaseline={onBaseline} onDetails={onDetails} onEditTags={onEditTags} />
            </div>
          </th>
          {timeKey && <td className="table-time" title={formatWallTime(run[timeKey], true)}>{formatTableTime(run[timeKey])}</td>}
          {state.show_tags_column && <td className="tags-cell"><div className="tag-chips">{run.tags?.length ? run.tags.map((tag) => <span className="tag-chip" key={tag}>{tag}</span>) : "—"}</div></td>}
          {metrics.map((metric) => {
            const summary = summaries.get(seriesKey(run.run_id, metric)), point = summary?.latest;
            const base = state.baseline_id ? summaries.get(seriesKey(state.baseline_id, metric))?.latest : undefined;
            const delta = run.run_id === state.baseline_id ? null : baselineDelta(point, base);
            const target = activeTarget(state.targets[metric]), met = target ? targetMet(point, target) : null;
            const targetDifference = target ? targetDelta(point, target) : null;
            const direction = target?.direction;
            const improved = delta !== null && delta !== 0 && direction ? (direction === "lte" ? delta < 0 : delta > 0) : false;
            const detail = [point ? `Latest raw ${point.value ?? "—"} · Step ${point.step}` : "Not recorded", base ? `Baseline raw ${base.value ?? "—"} · Step ${base.step}. Latest-to-latest; steps may differ. Absolute delta, not relative percent.` : "", summary?.first_hit ? `First target hit · Step ${summary.first_hit.step} · ${summary.first_hit.value}` : target ? "Target never reached" : ""].filter(Boolean).join("\n");
            return <td key={metric} className="metric-cell" title={loading ? "Updating…" : detail}>
              {loading ? <span className="cell-loading" aria-label="Loading">…</span> : <>
                <span>{formatMetric(point?.value)}</span>
                {state.table_show_steps && point && <span className="metric-secondary" title={formatWallTime(point.wall_time, true)}>Step {point.step}</span>}
                {differences.baseline && delta !== null && <span className={`metric-secondary${improved ? " delta-improved" : ""}`}>Δ Base {delta > 0 ? "+" : ""}{formatMetric(delta)}</span>}
                {differences.target && target && <span className="metric-secondary" title="Latest raw − target; signed metric units">Δ Target {targetDifference !== null && targetDifference > 0 ? "+" : ""}{formatMetric(targetDifference)}</span>}
                {target && <span className="metric-secondary target-status">{met === null ? "No valid latest" : met ? "✓ Target met" : "Target not met"}{state.table_show_steps && summary?.first_hit && <span className="first-hit-label"> · first @{summary.first_hit.step}</span>}</span>}
              </>}
            </td>;
          })}
          {visibleParams.map((param) => {
            const value = configs.get(run.run_id)?.get(param.key);
            const text = displayParameter(value);
            return <td key={param.key} className={param.different ? "param-cell param-difference" : "param-cell"} title={value === undefined ? "Not recorded" : text}>
              <span className="parameter-value">{text}</span>
            </td>;
          })}
        </tr>)}</tbody>
      </table>
    </div>
    {runs.length === 1 && <p className="comparison-note">Select another run to compare.</p>}
    {state.baseline_id && <p className="comparison-note">Baseline pinned first{differences.baseline && " · Δ compares latest raw values at each run’s own step."}</p>}
    {runs.length > 1 && state.differences_only && params.length > 0 && !params.some((param) => param.different) &&
      <p className="comparison-note">All recorded parameters match. Turn off Differences only to show them.</p>}
  </section>;
}
