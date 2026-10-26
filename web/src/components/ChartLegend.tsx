import { useEffect, useState } from "react";
import { CaretDownIcon, CaretUpIcon } from "@phosphor-icons/react";
import type { RunSummary } from "../api";
import { runLabel } from "../comparison";

const MIN_ITEM_WIDTH = 128, GAP = 8, INSET = 8;

interface ChartLegendProps {
  runs: RunSummary[];
  colors: Map<string, string>;
  width: number;
  baselineId: string | null;
  focusedId: string | null;
  onFocusRun: (id: string | null) => void;
  onLocateRun: (id: string) => void;
}

export default function ChartLegend({ runs, colors, width, baselineId, focusedId, onFocusRun, onLocateRun }: ChartLegendProps) {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => () => onFocusRun(null), [onFocusRun]);
  const columns = Math.max(1, Math.floor((width - INSET + GAP) / (MIN_ITEM_WIDTH + GAP)));
  const capacity = columns * 2;
  const overflowing = runs.length > capacity;
  const visible = expanded || !overflowing ? runs : runs.slice(0, capacity - 1);
  const Caret = expanded ? CaretUpIcon : CaretDownIcon;
  if (!runs.length) return null;
  return <div className="chart-legend" role="group" aria-label="Run legend" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
    {visible.map((run) => {
      const label = runLabel(run), baseline = run.run_id === baselineId;
      return <button type="button" key={run.run_id} className={`chart-legend-entry${focusedId === run.run_id ? " run-focused" : ""}`}
        title={`${label}${baseline ? " · Baseline" : ""} · Locate in run list`} aria-label={`Locate ${label} in run list`}
        onPointerEnter={() => onFocusRun(run.run_id)} onPointerLeave={() => onFocusRun(null)}
        onFocus={() => onFocusRun(run.run_id)} onBlur={() => onFocusRun(null)} onClick={() => onLocateRun(run.run_id)}>
        <i className="swatch" style={{ background: colors.get(run.run_id) }} aria-hidden="true" />
        <span>{label}</span>{baseline && <small className="baseline-mark" aria-label="Baseline">B</small>}
      </button>;
    })}
    {overflowing && <button type="button" className="text-button chart-legend-more" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}><span>{expanded ? "Show less" : `+${runs.length - visible.length} more`}</span><Caret size={10} weight="fill" aria-hidden="true" /></button>}
  </div>;
}
