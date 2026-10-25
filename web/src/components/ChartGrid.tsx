import type { RunSummary, ScalarPoint, ScalarSummary, MetricField } from "../api";
import type { WorkspaceState } from "../workspace";
import { CaretDownIcon, CaretRightIcon } from "@phosphor-icons/react";
import MetricChart from "./MetricChart";
import { clearYZoom, effectiveWarmup } from "../features";

interface ChartGridProps {
  metrics: MetricField[];
  loadingMetrics: Set<string>;
  refreshTick: number;
  runs: RunSummary[];
  series: Map<string, ScalarPoint[]>;
  colors: Map<string, string>;
  smoothing: number;
  state: WorkspaceState;
  summaries: Map<string, ScalarSummary>;
  onChange: (patch: Partial<WorkspaceState>) => void;
  focusedId: string | null;
  onFocusRun: (id: string | null) => void;
  topIds: Set<string> | null;
}

export default function ChartGrid({ metrics, loadingMetrics, refreshTick, runs, series, colors, smoothing, state, summaries, onChange, focusedId, onFocusRun, topIds }: ChartGridProps) {
  if (!metrics.length) return <div className="empty">{state.metric_filter ? "No metrics match this filter." : "No metrics in the selected runs."}</div>;
  const groups = new Map<string, MetricField[]>();
  for (const field of metrics) groups.set(field.group, [...groups.get(field.group) ?? [], field]);
  return <div className={`chart-grid chart-density-${state.chart_density}`}>
    {[...groups].sort(([a], [b]) => a.localeCompare(b)).map(([group, fields]) => {
      const collapsed = state.collapsed_groups.includes(group);
      const Icon = collapsed ? CaretRightIcon : CaretDownIcon;
      return <section className="metric-group" key={group} aria-label={group}>
        <button className="metric-group-heading" aria-expanded={!collapsed} onClick={() => onChange({ collapsed_groups: collapsed ? state.collapsed_groups.filter((item) => item !== group) : [...state.collapsed_groups, group] })}><Icon size={13} /><span>{group}</span></button>
        {!collapsed && <div className="metric-group-charts">{fields.map(({ metric, name }) => <MetricChart key={metric}
          metric={metric} title={name} loading={loadingMetrics.has(metric)} refreshTick={refreshTick} runs={runs} series={series} colors={colors} smoothing={smoothing}
          expanded={state.expanded_charts.includes(metric)}
          onExpand={() => onChange({ expanded_charts: state.expanded_charts.includes(metric) ? state.expanded_charts.filter((item) => item !== metric) : [...state.expanded_charts, metric] })}
          yScale={state.chart_scales[metric] ?? state.y_scale} scaleOverride={state.chart_scales[metric]}
          onScale={(scale) => {
            const chart_scales = { ...state.chart_scales }, chart_zoom = { ...state.chart_zoom };
            if (scale) chart_scales[metric] = scale; else delete chart_scales[metric];
            if (chart_zoom[metric]) chart_zoom[metric] = { ...chart_zoom[metric], y_min: null, y_max: null };
            onChange({ chart_scales, chart_zoom });
          }}
          tooltipDecimals={state.tooltip_decimals} tooltipSort={state.tooltip_sort} timeDisplay={state.tooltip_time} deltaDisplay={state.tooltip_delta}
          warmupStep={effectiveWarmup(state.chart_warmup[metric], state.y_warmup)} warmupOverride={state.chart_warmup[metric]} globalWarmup={state.y_warmup}
          onWarmup={(step) => {
            const chart_warmup = { ...state.chart_warmup };
            if (step === undefined) delete chart_warmup[metric]; else chart_warmup[metric] = step;
            onChange({ chart_warmup, chart_zoom: clearYZoom(state.chart_zoom, (name) => name === metric) });
          }}
          summaries={summaries} target={state.targets[metric]} baselineId={state.baseline_id} zoom={state.chart_zoom[metric]}
          onZoom={(zoom) => { const chart_zoom = { ...state.chart_zoom }; if (zoom) chart_zoom[metric] = zoom; else delete chart_zoom[metric]; onChange({ chart_zoom }); }}
          focusedId={focusedId} onFocusRun={onFocusRun} topIds={topIds}
        />)}</div>}
      </section>;
    })}
  </div>;
}
