import {
  DesktopIcon,
  MoonIcon,
  SunIcon,
  CloudSlashIcon,
  LockSimpleIcon,
} from "@phosphor-icons/react";
import type { ReactNode } from "react";
import type { ThemeMode } from "../theme";
import type { SavedView, WorkspaceState } from "../workspace";
import ChartToolbar from "./ChartToolbar";
import { clearYZoom } from "../features";
import ViewMenu from "./ViewMenu";
import ImageSettings from "./ImageSettings";
import DisplayMenu from "./DisplayMenu";
import RefreshControl from "./RefreshControl";
import TargetsMenu from "./TargetsMenu";

interface TopBarProps {
  themeMode: ThemeMode;
  onThemeToggle: () => void;
  onRefresh: () => void;
  state: WorkspaceState;
  views: SavedView[];
  writable: boolean;
  ready: boolean;
  localOnly: boolean;
  onChange: (patch: Partial<WorkspaceState>) => void;
  onOpenView: (view: SavedView) => void;
  onSaveView: (name: string, viewId?: string) => Promise<void>;
  onDeleteView: (viewId: string) => Promise<void>;
  onRenameView: (viewId: string, name: string) => Promise<void>;
  compareControls: ReactNode;
  colorControls: ReactNode;
  metrics: string[];
}

const THEME_LABELS: Record<ThemeMode, string> = {
  light: "Light",
  dark: "Dark",
  system: "System",
};

export default function TopBar({
  themeMode,
  onThemeToggle,
  onRefresh,
  state,
  views,
  writable,
  ready,
  localOnly,
  onChange,
  onOpenView,
  onSaveView,
  onDeleteView, onRenameView,
  compareControls,
  colorControls, metrics,
}: TopBarProps) {
  const ThemeIcon =
    themeMode === "light"
      ? SunIcon
      : themeMode === "dark"
        ? MoonIcon
        : DesktopIcon;

  return (
    <header className="topbar" aria-label="Workspace controls">
      <div className="toolbar-left">
        <div className="workspace-tabs" role="tablist" aria-label="Workspace display">
          {(["charts", "table", "images"] as const).map((panel, index, panels) => <button
            type="button" role="tab" id={`${panel}-tab`} key={panel}
            aria-selected={state.panel === panel} aria-controls="workspace-panel"
            tabIndex={state.panel === panel ? 0 : -1}
            onClick={() => onChange({ panel })}
            onKeyDown={(event) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              let next: WorkspaceState["panel"] = panels[(index + (event.key === "ArrowLeft" ? panels.length - 1 : 1)) % panels.length];
              if (event.key === "Home") next = "charts";
              if (event.key === "End") next = "images";
              onChange({ panel: next });
              document.getElementById(`${next}-tab`)?.focus();
            }}
          >{panel === "charts" ? "Charts" : panel === "table" ? "Table" : "Media"}</button>)}
        </div>
        <ViewMenu state={state} views={views} writable={writable} onOpen={onOpenView} onSave={onSaveView} onDelete={onDeleteView} onRename={onRenameView} />
      </div>
      <div className="toolbar-right">
        {colorControls}
        <DisplayMenu metrics={metrics} settings={state.colors.top_k} onChange={(top_k) => onChange({ colors: { ...state.colors, top_k } })} />
        {state.panel !== "images" && <TargetsMenu metrics={metrics} state={state} onChange={onChange} />}
        {state.panel === "charts" ? <ChartToolbar
          metricFilter={state.metric_filter} onMetricFilterChange={(metric_filter) => onChange({ metric_filter })}
          density={state.chart_density} onDensityChange={(chart_density) => onChange({ chart_density })}
          yScale={state.y_scale} onYScaleChange={(y_scale) => onChange({ y_scale, chart_zoom: clearYZoom(state.chart_zoom, (metric) => !state.chart_scales[metric]) })}
          warmup={state.y_warmup} onWarmupChange={(y_warmup) => onChange({ y_warmup, chart_zoom: clearYZoom(state.chart_zoom, (metric) => !Object.hasOwn(state.chart_warmup, metric)) })}
          smoothing={state.smoothing} onSmoothingChange={(value) => onChange({ smoothing: value })}
          tooltipDecimals={state.tooltip_decimals} onTooltipDecimalsChange={(value) => onChange({ tooltip_decimals: value })}
          tooltipSort={state.tooltip_sort} onTooltipSortChange={(value) => onChange({ tooltip_sort: value })}
          timeDisplay={state.tooltip_time} onTimeDisplayChange={(value) => onChange({ tooltip_time: value })}
          deltaDisplay={state.tooltip_delta} onDeltaDisplayChange={(value) => onChange({ tooltip_delta: value })}
        /> : state.panel === "table" ? compareControls : <ImageSettings state={state} onChange={onChange} />}
        <div className="toolbar-actions">
          {ready && !writable && <span className="read-only-indicator" title="Viewing changes stay in this browser. Saved views cannot be changed." role="status">
            <LockSimpleIcon size={14} aria-hidden="true" /><span>Read-only</span>
          </span>}
          {writable && localOnly && <span className="local-save-indicator" title="Changes are saved in this browser" role="status" aria-label="Changes are saved in this browser"><CloudSlashIcon size={15} aria-hidden="true" /></span>}
        <RefreshControl interval={state.refresh_interval} onRefresh={onRefresh} onInterval={(refresh_interval) => onChange({ refresh_interval })} />
        <button
          type="button"
          className="button icon-button"
          onClick={onThemeToggle}
          aria-label={`Change theme, current: ${THEME_LABELS[themeMode]}`}
          title={`Theme: ${THEME_LABELS[themeMode]}`}
        >
          <ThemeIcon size={16} aria-hidden="true" />
        </button>
        </div>
      </div>
    </header>
  );
}
