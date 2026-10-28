import { memo, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { scaleLinear, scaleLog } from "d3-scale";
import { ArrowCounterClockwiseIcon, ArrowsHorizontalIcon, ArrowsInLineHorizontalIcon, WarningIcon } from "@phosphor-icons/react";
import { seriesKey, type RunSummary, type ScalarPoint, type ScalarSummary } from "../api";
import { baselineDelta, deltaColumns, targetDelta, targetMet, type ChartZoom, type TimeDisplay, type DeltaDisplay } from "../features";
import type { NamedTarget } from "../targets";
import type { WorkspaceUpdate } from "../workspace";
import type { MetricSeries } from "../scalarSeries";
import { formatMetric, runLabel } from "../comparison";
import { buildPath, compareReadouts, fittedDomain, logTicks, nearestPoint, stepAxis, visiblePoints } from "../chartMath";
import MetricMenu from "./MetricMenu";
import { elapsedTime, formatDuration, formatWallTime, timeOriginLabel, type ReadoutPlacement } from "../readout";
import { useSettledDomain, useWarmupRange } from "../useChartRange";
import FloatingReadout from "./FloatingReadout";
import ChartLegend from "./ChartLegend";

// Leave room for the target name even when its value reaches the top of the Y range.
const HEIGHT = 256, TOP = 22, BOTTOM = 22, RIGHT = 18;

function formatReadout(value: number | null | undefined, decimals: number): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const magnitude = Math.abs(value);
  // Scientific notation keeps tiny nonzero values and large values readable in compact rows.
  if (magnitude !== 0 && (magnitude < 10 ** -Math.max(decimals, 3) || magnitude >= 1e6)) return value.toExponential(decimals);
  const formatted = value.toFixed(decimals);
  return Number(formatted) === 0 ? (0).toFixed(decimals) : formatted;
}

function formatDelta(value: number | null, decimals: number): string {
  const label = formatReadout(value, decimals);
  return value !== null && value > 0 && Number(label) !== 0 ? `+${label}` : label;
}

interface MetricChartProps {
  loading: boolean;
  dataTick: number;
  expanded: boolean; onChange: WorkspaceUpdate;
  metric: string; title: string; yScale: "linear" | "log"; scaleOverride?: "linear" | "log"; runs: RunSummary[]; series: MetricSeries; colors: Map<string, string>; smoothing: number;
  tooltipDecimals: number;
  tooltipSort: "value" | "list";
  tooltipPlacement: ReadoutPlacement;
  showLegend: boolean;
  onLocateRun: (id: string) => void;
  timeDisplay: TimeDisplay; deltaDisplay: DeltaDisplay;
  warmupStep?: number; warmupOverride?: number | null; globalWarmup: number | null;
  summaries: Map<string, ScalarSummary>; target?: NamedTarget; baselineId: string | null;
  focusedId: string | null; onFocusRun: (id: string | null) => void; topIds: Set<string> | null;
}

export default memo(function MetricChart({ metric, title, loading, dataTick, expanded, onChange, yScale, scaleOverride, runs, series, colors, smoothing, tooltipDecimals, tooltipSort, tooltipPlacement, showLegend, onLocateRun, timeDisplay, deltaDisplay, warmupStep, warmupOverride, globalWarmup, summaries, target, baselineId, focusedId, onFocusRun, topIds }: MetricChartProps) {
  const containerRef = useRef<HTMLDivElement>(null), clipId = useId(), helpId = useId();
  const cardRef = useRef<HTMLElement>(null);
  const targetLabelRef = useRef<SVGGElement>(null);
  const [targetLabelWidth, setTargetLabelWidth] = useState(0);
  const [measuredWidth, setWidth] = useState(0);
  const width = measuredWidth || 320;
  const [hoverStep, setHoverStep] = useState<number | null>(null);
  const [pinned, setPinned] = useState(false);
  const [zoom, setZoom] = useState<ChartZoom>();
  const [detailId, setDetailId] = useState<string | null>(null);
  const [box, setBox] = useState<{ x: number; y: number; endX: number; endY: number } | null>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const pinTimer = useRef<number | undefined>(undefined);
  const [includeTarget, setIncludeTarget] = useState(false);
  const hoverFrame = useRef(0), pendingHover = useRef<number | null>(null);
  function clearHover() { cancelAnimationFrame(hoverFrame.current); hoverFrame.current = 0; setHoverStep(null); }
  function moveHover(step: number) {
    pendingHover.current = step;
    if (!hoverFrame.current) hoverFrame.current = requestAnimationFrame(() => { hoverFrame.current = 0; setHoverStep(pendingHover.current); });
  }
  function dismissReadout() { clearHover(); setPinned(false); onFocusRun(null); }
  useLayoutEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => { if (entry?.contentRect.width) setWidth(entry.contentRect.width); });
    observer.observe(element); return () => observer.disconnect();
  }, []);
  useEffect(() => { setIncludeTarget(false); }, [target?.value, target?.direction, zoom]);
  useEffect(() => { if (!pinned) setDetailId(null); }, [pinned]);
  useEffect(() => () => { window.clearTimeout(pinTimer.current); cancelAnimationFrame(hoverFrame.current); }, []);
  useEffect(() => { window.clearTimeout(pinTimer.current); clearHover(); setPinned(false); }, [zoom?.x_min, zoom?.x_max, metric]);
  useEffect(() => { setZoom(current => current ? { ...current, y_min: null, y_max: null } : current); }, [yScale, warmupStep]);

  const onExpand = useCallback(() => onChange((state) => ({ expanded_charts: state.expanded_charts.includes(metric) ? state.expanded_charts.filter((item) => item !== metric) : [...state.expanded_charts, metric] })), [metric, onChange]);
  const onScale = useCallback((scale: "linear" | "log" | null) => onChange((state) => {
    const chart_scales = { ...state.chart_scales };
    if (scale) chart_scales[metric] = scale; else delete chart_scales[metric];
    return { chart_scales };
  }), [metric, onChange]);
  const onWarmup = useCallback((step: number | null | undefined) => onChange((state) => {
    const chart_warmup = { ...state.chart_warmup };
    if (step === undefined) delete chart_warmup[metric]; else chart_warmup[metric] = step;
    return { chart_warmup };
  }), [metric, onChange]);
  const onZoom = useCallback((value: ChartZoom | null) => setZoom(value ?? undefined), []);
  const entries = useMemo(() => runs.map((run) => ({ run, points: series.get(run.run_id) ?? [], color: colors.get(run.run_id) ?? "#747e8b" })), [runs, series, colors]);
  const drawing = useMemo(() => entries.map((entry) => ({ ...entry, points: zoom ? visiblePoints(entry.points, zoom.x_min, zoom.x_max) : entry.points })), [entries, zoom?.x_min, zoom?.x_max]);
  const points = useMemo(() => drawing.flatMap((entry) => entry.points), [drawing]);
  const valid = useMemo(() => points.filter((point) => point.value !== null && Number.isFinite(point.value)), [points]);
  const plotHeight = HEIGHT - TOP - BOTTOM;
  const stepExtent = useMemo<[number, number] | undefined>(() => {
    const starts = entries.filter((entry) => entry.points.length).map((entry) => entry.points[0].step);
    const ends = entries.filter((entry) => entry.points.length).map((entry) => entry.points[entry.points.length - 1].step);
    return starts.length ? [Math.min(...starts), Math.max(...ends)] : undefined;
  }, [entries]);
  const minStep = zoom?.x_min ?? stepExtent?.[0] ?? 0;
  const maxStep = zoom?.x_max ?? stepExtent?.[1] ?? 1;
  const logarithmic = yScale === "log";
  const manualY = zoom?.y_min != null && zoom?.y_max != null && (!logarithmic || zoom.y_min > 0);
  const fit = useWarmupRange(runs, metric, manualY || zoom ? undefined : warmupStep, undefined, undefined, dataTick);
  const fitting = useMemo(() => fittedDomain(valid, smoothing > 0, logarithmic, warmupStep, fit?.bounds), [valid, smoothing, logarithmic, warmupStep, fit?.bounds]);
  const [fitLow, fitHigh] = fitting.domain;
  const fitNote = manualY ? "Box zoom fixes Y. Change Y range to refit." : fitting.empty && !loading ? "No usable samples after cutoff · Using Auto." : fit?.status === "error" ? "Exact Y bounds unavailable · Using loaded samples." : undefined;
  const [autoLow, autoHigh] = useSettledDomain(fitLow, fitHigh, JSON.stringify([metric, runs.map((r) => r.run_id), warmupStep, zoom, smoothing, fit?.status, yScale]));
  let low = manualY ? zoom!.y_min! : autoLow, high = manualY ? zoom!.y_max! : autoHigh;
  const targetInvalid = Boolean(target && logarithmic && target.value <= 0);
  if (includeTarget && target && !targetInvalid) { low = Math.min(low, target.value); high = Math.max(high, target.value); }
  const y = useMemo(() => {
    const scale = (logarithmic ? scaleLog() : scaleLinear()).domain([low, high]).range([plotHeight, 0]);
    if (!manualY && !logarithmic) scale.nice();
    return scale;
  }, [low, high, plotHeight, manualY, logarithmic]);
  const ticks = logarithmic ? logTicks(low, high) : y.ticks(4).filter((value) => Number.isFinite(value));
  const magnitude = Math.max(Math.abs(low), Math.abs(high));
  const formatY = logarithmic ? (value: number) => formatMetric(value) : y.tickFormat(4, magnitude > 0 && magnitude < .001 ? "e" : magnitude >= 10000 ? "s" : "f");
  const targetY = target && !targetInvalid ? y(target.value) : null;
  const targetOutside = targetY !== null && (targetY < 0 || targetY > plotHeight);
  const targetVisible = targetY !== null && !targetOutside;
  const targetName = target?.chart_label || target?.name || "";
  const targetValueLabel = target ? `${target.direction === "gte" ? "≥" : "≤"} ${formatMetric(target.value)}` : "";
  const targetNameY = (targetY ?? 0) - 14;
  const targetLabelTop = Math.min(targetNameY, targetY ?? 0) - 8;
  const targetLabelBottom = Math.max(targetNameY, targetY ?? 0) + 8;
  useLayoutEffect(() => {
    const element = targetLabelRef.current;
    if (!element) { setTargetLabelWidth(0); return; }
    const measure = () => setTargetLabelWidth(Math.ceil(element.getBBox().width));
    const observer = new ResizeObserver(measure);
    observer.observe(element); measure();
    return () => observer.disconnect();
  }, [targetName, targetValueLabel, targetVisible]);
  const left = Math.max(42, ...ticks.map((t) => formatY(t).length * 7 + 12), targetVisible ? targetLabelWidth + 12 : 0);
  const plotWidth = Math.max(60, width - left - RIGHT);
  const { scale: x, dataWidth, ticks: xTicks, stepAt } = useMemo(() => stepAxis(minStep, maxStep, plotWidth), [minStep, maxStep, plotWidth]);
  const paths = useMemo(() => measuredWidth ? drawing.map((entry) => ({ ...entry,
    rawPath: smoothing > 0 ? buildPath(entry.points, x, y, false, logarithmic) : "",
    path: buildPath(entry.points, x, y, smoothing > 0, logarithmic),
  })) : [], [drawing, x, y, smoothing, logarithmic, measuredWidth]);
  const hover = hoverStep === null ? [] : entries.map((entry) => ({ ...entry, point: nearestPoint(entry.points, hoverStep) }))
    .sort((a, b) => compareReadouts(a, b, smoothing > 0, baselineId, tooltipSort));
  const baseline = hover.find((e) => e.run.run_id === baselineId);
  const columns = deltaColumns(deltaDisplay, Boolean(baselineId), Boolean(target));
  const detail = pinned ? hover.find((entry) => entry.run.run_id === detailId) : undefined;
  const basePoint = baseline?.point;
  const focused = entries.find((e) => e.run.run_id === focusedId);
  const first = focused ? summaries.get(seriesKey(focused.run.run_id, metric))?.first_hit : undefined;

  function position(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - bounds.left) * width / bounds.width - left;
    const y = (event.clientY - bounds.top) * HEIGHT / bounds.height - TOP;
    return { x: Math.max(0, Math.min(dataWidth, x)), y: Math.max(0, Math.min(plotHeight, y)), inside: x >= 0 && x <= plotWidth && y >= 0 && y <= plotHeight };
  }
  function cancelDrag() { drag.current = null; setBox(null); }
  function reset() { window.clearTimeout(pinTimer.current); onZoom(null); dismissReadout(); setIncludeTarget(false); cancelDrag(); }
  const hasData = valid.some((point) => { const value = smoothing > 0 ? point.smoothed : point.value; return value != null && (!logarithmic || value > 0); });
  const omitted = logarithmic ? valid.filter((point) => point.value! <= 0 || (smoothing > 0 && point.smoothed != null && point.smoothed <= 0)).length : 0;
  const targetLabel = target ? `Target ${targetValueLabel}` : "";
  return <article ref={cardRef} className={`chart-card${expanded ? " chart-expanded" : ""}`} aria-label={metric}>
    <div className="chart-head"><h3 className="chart-title" title={metric}>{title}</h3><div className="chart-head-actions">
      {loading && <span className="muted" role="status">Loading…</span>}
      {zoom && <button className="button icon-button" aria-label={`Reset zoom for ${metric}`} title="Reset zoom" onClick={reset}><ArrowCounterClockwiseIcon size={14} /></button>}
      <button className="button icon-button chart-expand-button" aria-label={`${expanded ? "Restore size for" : "Expand"} ${metric}`} aria-pressed={expanded} title={expanded ? "Restore size" : "Expand to full width"} onClick={onExpand}>{expanded ? <ArrowsInLineHorizontalIcon size={14} /> : <ArrowsHorizontalIcon size={14} />}</button>
      <MetricMenu scaleOverride={scaleOverride} onScale={onScale} metric={metric} zoom={zoom} onZoom={onZoom} warmupOverride={warmupOverride} globalWarmup={globalWarmup} onWarmup={onWarmup} stepExtent={stepExtent} fitNote={fitNote} />
    </div></div>
    {logarithmic && <div className="chart-range-note" role="status">Log₁₀{omitted ? ` · ${omitted} sampled points contain nonpositive values` : ""}</div>}
    {targetInvalid && <div className="chart-range-note">{targetLabel} · Outside log domain</div>}
    {target && targetOutside && <button className="target-edge" onClick={() => setIncludeTarget(true)}>{target.value > high ? "↑" : "↓"} Target {target.direction === "gte" ? "≥" : "≤"}{formatMetric(target.value)} · Include in range</button>}
    <div className="chart-plot" ref={containerRef}>
      <span className="sr-only" id={helpId}>Drag a box to zoom. Double-click or Escape to reset. Arrow keys move the readout. Click or Enter to pin it. Values show actual sampled steps.</span>
      <svg width={width} height={HEIGHT} role="group" tabIndex={0} aria-label={`${metric} interactive chart`} aria-describedby={helpId} className="interactive-chart"
        onPointerDown={(event) => {
          window.clearTimeout(pinTimer.current);
          const p = position(event);
          if (event.button !== 0 || !p.inside || !hasData) return;
          drag.current = { x: p.x, y: p.y }; setBox({ x: p.x, y: p.y, endX: p.x, endY: p.y }); event.currentTarget.setPointerCapture(event.pointerId); clearHover();
        }}
        onPointerMove={(event) => { const p = position(event); if (drag.current) setBox({ ...drag.current, endX: p.x, endY: p.y }); else if (!pinned) moveHover(stepAt(p.x)); }}
        onPointerUp={(event) => {
          const start = drag.current, end = position(event); cancelDrag();
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
          if (!start) return;
          if (Math.abs(start.x - end.x) < 8 && Math.abs(start.y - end.y) < 5) { const step = stepAt(end.x); setHoverStep(step); pinTimer.current = window.setTimeout(() => { setPinned((v) => !v); setHoverStep(step); }, 300); return; }
          if (Math.abs(start.x - end.x) < 8 || Math.abs(start.y - end.y) < 5) return;
          setPinned(false);
          const a = Math.ceil(stepAt(Math.min(start.x, end.x))), b = Math.floor(stepAt(Math.max(start.x, end.x)));
          if (a < b) onZoom({ x_min: a, x_max: b, y_min: y.invert(Math.max(start.y, end.y)), y_max: y.invert(Math.min(start.y, end.y)) });
        }} onPointerCancel={cancelDrag} onLostPointerCapture={cancelDrag}
        onPointerLeave={(e) => { const inside = e.relatedTarget instanceof Node && containerRef.current?.contains(e.relatedTarget); if (!pinned && !drag.current && !inside) { window.clearTimeout(pinTimer.current); dismissReadout(); } }}
        onDoubleClick={reset} onBlur={(e) => { const inside = e.relatedTarget instanceof Node && containerRef.current?.contains(e.relatedTarget); if (!pinned && !inside) { window.clearTimeout(pinTimer.current); dismissReadout(); } }}
        onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); reset(); } if (e.key === "Enter") { e.preventDefault(); setPinned((v) => !v); if (hoverStep === null) setHoverStep((minStep + maxStep) / 2); } if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) { e.preventDefault(); const stride = (maxStep - minStep) / 100; setHoverStep((previous) => e.key === "Home" ? minStep : e.key === "End" ? maxStep : Math.max(minStep, Math.min(maxStep, (previous ?? (minStep + maxStep) / 2) + (e.key === "ArrowLeft" ? -stride : stride)))); } }}>
        <defs>
          <clipPath id={clipId}><rect width={plotWidth} height={plotHeight} /></clipPath>
          <clipPath id={`${clipId}-series`}><rect width={dataWidth} height={plotHeight} /></clipPath>
        </defs>
        <g transform={`translate(${left},${TOP})`}>
          {ticks.map((t) => <g key={t}>{Math.abs(y(t) - plotHeight) > .5 && <line className="grid-line" x2={plotWidth} y1={y(t)} y2={y(t)} />}{(!targetVisible || y(t) < targetLabelTop - 6 || y(t) > targetLabelBottom + 6) && <text className="axis-text" x={-8} y={y(t)} dy=".32em" textAnchor="end">{formatY(t)}</text>}</g>)}
          {targetVisible && <g ref={targetLabelRef} className="target-axis-label" aria-label={`${targetName} ${targetValueLabel}`}>
            <title>{target?.name} {targetValueLabel}</title>
            <text className="target-axis-name" x={-8} y={targetNameY} dy=".32em" textAnchor="end">{targetName}</text>
            <text x={-8} y={targetY!} dy=".32em" textAnchor="end">{targetValueLabel}</text>
          </g>}
          <line className="axis-line" x2={plotWidth} y1={plotHeight} y2={plotHeight} />
          {xTicks.map((tick) => <g key={tick.step}>
            <line className="axis-line" x1={x(tick.step)} x2={x(tick.step)} y1={plotHeight} y2={plotHeight + 4} />
            <text className="axis-text" x={x(tick.step)} y={plotHeight + 17} textAnchor={tick.anchor}>{tick.label}</text>
          </g>)}
          <g clipPath={`url(#${clipId}-series)`}>
            {[...paths].sort((a, b) => Number(a.run.run_id === focusedId) - Number(b.run.run_id === focusedId)).map((entry) => {
              const isFocused = entry.run.run_id === focusedId;
              const dimmed = focusedId ? !isFocused : topIds && !topIds.has(entry.run.run_id) && entry.run.run_id !== baselineId;
              return <g key={entry.run.run_id} opacity={dimmed ? .18 : 1}>
                {smoothing > 0 && <path d={entry.rawPath} fill="none" stroke={entry.color} strokeOpacity={.15} strokeWidth={1} />}
                <path className="series-path" d={entry.path} fill="none" stroke={entry.color} strokeWidth={isFocused ? 2.6 : 1.8} strokeDasharray={entry.run.run_id === baselineId ? "6 3" : undefined} strokeLinecap="round" strokeLinejoin="round" />
                {entry.points.filter((p) => p.value === null).slice(0, 20).map((p, i) => <WarningIcon key={i} className="nan-marker" x={x(p.step) - 5} y={0} size={10} aria-label={`Invalid at step ${p.step}`} />)}
              </g>;
            })}
          </g>
          <g clipPath={`url(#${clipId})`}>
            {targetVisible && <g className="target-line"><line x2={plotWidth} y1={targetY!} y2={targetY!} /></g>}
            {first && first.step >= minStep && first.step <= maxStep && first.value !== null && (!logarithmic || first.value > 0) && focused && <g><circle cx={x(first.step)} cy={y(first.value)} r={4} fill="var(--surface)" stroke={focused.color} strokeWidth={1.5} /><text className="first-hit-text" x={Math.min(plotWidth - 90, Math.max(0, x(first.step) + 7))} y={Math.max(12, y(first.value) - 7)}>First hit @{first.step}</text></g>}
            {hoverStep !== null && !drag.current && <><line className="hover-line" x1={x(hoverStep)} x2={x(hoverStep)} y2={plotHeight} />{hover.map((e) => { const value = smoothing > 0 ? e.point?.smoothed : e.point?.value; return value != null && Number.isFinite(value) && (!logarithmic || value > 0) && e.point && e.point.step >= minStep && e.point.step <= maxStep ? <circle key={e.run.run_id} cx={x(e.point.step)} cy={y(value)} r={3} fill="var(--surface)" stroke={e.color} strokeWidth={1.6} /> : null; })}</>}
            {box && <rect className="brush-box" x={Math.min(box.x, box.endX)} y={Math.min(box.y, box.endY)} width={Math.abs(box.x - box.endX)} height={Math.abs(box.y - box.endY)} />}
          </g>
          {!hasData && <text className="axis-text" x={plotWidth / 2} y={plotHeight / 2} textAnchor="middle">{loading ? "Loading samples…" : points.length ? logarithmic ? "No positive values in this range" : "No finite values in this range" : "No samples in this range"}</text>}
        </g>
      </svg>
      {hoverStep !== null && !box && hasData && <FloatingReadout anchor={cardRef.current} pinned={pinned} preference={tooltipPlacement} onDismiss={dismissReadout}>
        <div className="tooltip-heading" title={pinned ? "Pinned samples" : "Click chart to pin"}>
          <span className="tooltip-context">Samples</span>
          <span className="tooltip-baseline" title={baseline ? `Baseline: ${runLabel(baseline.run)}` : undefined}>{baseline ? `vs ${runLabel(baseline.run)}` : pinned ? "Pinned" : "Click to pin"}</span>
          {pinned && <button className="text-button" aria-label="Close readout" onClick={dismissReadout}>×</button>}
        </div>
        <div className="tooltip-table" onPointerLeave={() => onFocusRun(null)} style={{ gridTemplateColumns: `minmax(120px, 1fr) repeat(${2 + Number(smoothing > 0) + Number(columns.baseline) + Number(columns.target) + Number(timeDisplay !== "hidden")}, max-content)` }}>
        <div className="tooltip-columns"><span>Run</span><span>Step</span><span>Raw{tooltipSort === "value" && smoothing === 0 ? " ↓" : ""}</span>{smoothing > 0 && <span>EMA{tooltipSort === "value" ? " ↓" : ""}</span>}{columns.baseline && <span title="Raw − baseline Raw using each run's nearest sample">Δ Base</span>}{columns.target && <span title="Raw − target; signed metric units">Δ Target</span>}{timeDisplay !== "hidden" && <span title={timeDisplay === "wall" ? "Sample time in your local timezone" : "Per-run time since Step 0, or first recorded sample"}>{timeDisplay === "wall" ? "Time" : "Elapsed"}</span>}</div>
        {hover.map((entry) => {
          const id = entry.run.run_id, delta = id === baselineId ? null : baselineDelta(entry.point, basePoint), summary = summaries.get(seriesKey(id, metric));
          const met = target ? targetMet(entry.point, target) : null;
          const targetDifference = target ? targetDelta(entry.point, target) : null;
          const elapsed = elapsedTime(entry.point, entry.run);
          const description = [runLabel(entry.run), logarithmic && entry.point && (entry.point.value ?? 1) <= 0 ? "Raw value is outside the log domain" : "", id === baselineId ? "Baseline" : "", !entry.point ? "Outside recorded range" : `Step ${entry.point.step}`, baselineId && id !== baselineId && delta === null ? "Δ unavailable: missing or invalid sample" : "", met === true ? "Target met" : "", summary?.first_hit ? `First target hit at step ${summary.first_hit.step}` : ""].filter(Boolean).join(" · ");
          return <button key={id} tabIndex={pinned ? 0 : -1} className={`tooltip-row${id === baselineId ? " tooltip-baseline-row" : ""}${id === focusedId ? " run-focused" : ""}`} onPointerEnter={() => onFocusRun(id)} onFocus={() => onFocusRun(id)} onBlur={() => onFocusRun(null)} onClick={() => { if (pinned) setDetailId(detailId === id ? null : id); }} aria-expanded={pinned ? detailId === id : undefined} title={description}>
            <span className="tooltip-run"><i className="swatch" style={{ background: entry.color }} /><span>{runLabel(entry.run)}</span>{id === baselineId && <small aria-label="Baseline">B</small>}{met === true && <small aria-label="Target met">✓</small>}</span>
            <span className="tooltip-step" title={entry.point ? `Step ${entry.point.step}` : "Outside recorded range"}>{entry.point?.step ?? "—"}</span>
            <span title={`Raw: ${entry.point?.value ?? "unavailable"}`}>{formatReadout(entry.point?.value, tooltipDecimals)}</span>
            {smoothing > 0 && <span title={`EMA: ${entry.point?.smoothed ?? "unavailable"}`}>{formatReadout(entry.point?.smoothed, tooltipDecimals)}</span>}
            {columns.baseline && <span title={delta !== null ? `Raw − baseline Raw: ${delta}` : id === baselineId ? "Baseline" : "Δ unavailable: missing or invalid sample"}>{formatDelta(delta, tooltipDecimals)}</span>}
            {columns.target && <span title={`Raw − target: ${targetDifference ?? "unavailable"}`}>{formatDelta(targetDifference, tooltipDecimals)}</span>}
            {timeDisplay !== "hidden" && <span title={`${timeOriginLabel(entry.run)} · ${formatWallTime(entry.point?.wall_time, true)}`}>{timeDisplay === "wall" ? formatWallTime(entry.point?.wall_time) : `${elapsed !== null && entry.run.time_origin_kind === "first_sample" ? "~" : ""}${formatDuration(elapsed)}`}</span>}
          </button>;
        })}
        </div>
        {detail && <div className="tooltip-detail"><strong>{runLabel(detail.run)} · Step {detail.point?.step ?? "—"}</strong><span>{formatWallTime(detail.point?.wall_time, true)}</span><span>{timeOriginLabel(detail.run)} · {formatDuration(elapsedTime(detail.point, detail.run))} · includes pauses</span></div>}
      </FloatingReadout>}
    </div>
    {showLegend && <ChartLegend runs={entries.filter((entry) => entry.points.length > 0).map((entry) => entry.run)} colors={colors} width={width} baselineId={baselineId} focusedId={focusedId} onFocusRun={onFocusRun} onLocateRun={onLocateRun} />}
  </article>;
});
