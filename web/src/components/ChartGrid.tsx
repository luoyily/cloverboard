import { useMemo } from "react";
import type { RunSummary, ScalarSummary, MetricField } from "../api";
import type { WorkspaceState, WorkspaceUpdate } from "../workspace";
import type { MetricSeries, SeriesSnapshot } from "../scalarSeries";
import { CaretDownIcon, CaretRightIcon } from "@phosphor-icons/react";
import MetricChart from "./MetricChart";
import { effectiveWarmup } from "../features";
import { activeTarget } from "../targets";

const EMPTY_SERIES: MetricSeries = new Map();

interface ChartGridProps {
  metrics: MetricField[];
  loadingMetrics: Set<string>;
  dataTick: number;
  runs: RunSummary[];
  series: SeriesSnapshot;
  colors: Map<string, string>;
  smoothing: number;
  state: WorkspaceState;
  summaries: Map<string, ScalarSummary>;
  onChange: WorkspaceUpdate;
  focusedId: string | null;
  onFocusRun: (id: string | null) => void;
  onLocateRun: (id: string) => void;
  topIds: Set<string> | null;
}

export default function ChartGrid({ metrics, loadingMetrics, dataTick, runs, series, colors, smoothing, state, summaries, onChange, focusedId, onFocusRun, onLocateRun, topIds }: ChartGridProps) {
  const groups = useMemo(() => {
    const grouped = new Map<string, MetricField[]>();
    for (const field of metrics) {
      const fields = grouped.get(field.group) ?? [];
      fields.push(field); grouped.set(field.group, fields);
    }
    return [...grouped].sort(([a], [b]) => a.localeCompare(b));
  }, [metrics]);
  if (!metrics.length) return <div className="empty">{state.metric_filter ? "No metrics match this filter." : "No metrics in the selected runs."}</div>;
  return <div className={`chart-grid chart-density-${state.chart_density}`}>
    {groups.map(([group, fields]) => {
      const collapsed = state.collapsed_groups.includes(group);
      const Icon = collapsed ? CaretRightIcon : CaretDownIcon;
      return <section className="metric-group" key={group} aria-label={group}>
        <button type="button" className="metric-group-heading" aria-label={`${group}, ${fields.length} ${fields.length === 1 ? "metric" : "metrics"}`} aria-expanded={!collapsed}
          onClick={() => onChange((current) => ({ collapsed_groups: current.collapsed_groups.includes(group) ? current.collapsed_groups.filter((item) => item !== group) : [...current.collapsed_groups, group] }))}>
          <Icon size={13} aria-hidden="true" />
          <span className="metric-group-name">{group}</span>
          <span className="metric-group-count" aria-hidden="true">({fields.length})</span>
        </button>
        {!collapsed && <div className="metric-group-charts">{fields.map(({ metric, name }) => <MetricChart key={metric}
          metric={metric} title={name} loading={loadingMetrics.has(metric)} dataTick={dataTick} runs={runs} series={series.get(metric) ?? EMPTY_SERIES} colors={colors} smoothing={smoothing}
          expanded={state.expanded_charts.includes(metric)}
          onChange={onChange}
          yScale={state.chart_scales[metric] ?? state.y_scale} scaleOverride={state.chart_scales[metric]}
          tooltipDecimals={state.tooltip_decimals} tooltipSort={state.tooltip_sort} timeDisplay={state.tooltip_time} deltaDisplay={state.tooltip_delta}
          showLegend={state.show_chart_legend} tooltipPlacement={state.tooltip_placement} onLocateRun={onLocateRun}
          warmupStep={effectiveWarmup(state.chart_warmup[metric], state.y_warmup)} warmupOverride={state.chart_warmup[metric]} globalWarmup={state.y_warmup}
          summaries={summaries} target={activeTarget(state.targets[metric])} baselineId={state.baseline_id} zoom={state.chart_zoom[metric]}
          focusedId={focusedId} onFocusRun={onFocusRun} topIds={topIds}
        />)}</div>}
      </section>;
    })}
  </div>;
}
