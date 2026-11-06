import {
  DesktopIcon,
  MoonIcon,
  SunIcon,
  CloudSlashIcon,
} from "@phosphor-icons/react";
import type { ReactNode } from "react";
import type { ThemeMode } from "../theme";
import type { SavedView, WorkspaceState } from "../workspace";
import type { ActiveView, ViewScope } from "../viewingConfig";
import type { TargetLibrary } from "../targets";
import ChartToolbar from "./ChartToolbar";
import ViewMenu from "./ViewMenu";
import ImageSettings from "./ImageSettings";
import RefreshControl from "./RefreshControl";
import TargetsMenu from "./TargetsMenu";
import SettingsMenu from "./SettingsMenu";

interface TopBarProps {
  themeMode: ThemeMode;
  onThemeToggle: () => void;
  onRefresh: () => void;
  state: WorkspaceState;
  sharedViews: SavedView[];
  localViews: SavedView[];
  activeView: ActiveView | null;
  writable: boolean;
  ready: boolean;
  storageError: string;
  onChange: (patch: Partial<WorkspaceState>) => void;
  targets: TargetLibrary;
  onSaveTargets: (targets: TargetLibrary) => void;
  onImportConfig: (text: string) => Promise<void>;
  onOpenView: (view: SavedView, scope: ViewScope) => void;
  onSaveView: (name: string, scope: ViewScope, viewId?: string) => Promise<void>;
  onDeleteView: (scope: ViewScope, viewId: string) => Promise<void>;
  onRenameView: (scope: ViewScope, viewId: string, name: string) => Promise<void>;
  onPromoteView: (viewId: string) => Promise<void>;
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
  sharedViews, localViews, activeView,
  writable,
  ready,
  storageError,
  onChange,
  targets, onSaveTargets,
  onImportConfig,
  onOpenView,
  onSaveView,
  onDeleteView, onRenameView, onPromoteView,
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
        <ViewMenu state={state} sharedViews={sharedViews} localViews={localViews} activeView={activeView} writable={writable} ready={ready} onOpen={onOpenView} onSave={onSaveView} onDelete={onDeleteView} onRename={onRenameView} onPromote={onPromoteView} />
      </div>
      <div className="toolbar-right">
        {colorControls}
        {state.panel !== "images" && <TargetsMenu metrics={metrics} targets={targets} editable={writable} onChange={onSaveTargets} />}
        {state.panel === "charts" ? <ChartToolbar
          metrics={metrics} topK={state.colors.top_k} onTopKChange={(top_k) => onChange({ colors: { ...state.colors, top_k } })}
          metricFilter={state.metric_filter} onMetricFilterChange={(metric_filter) => onChange({ metric_filter })}
          density={state.chart_density} onDensityChange={(chart_density) => onChange({ chart_density })}
          layout={state.chart_layout} onLayoutChange={(chart_layout) => onChange({ chart_layout })}
          cardStyle={state.chart_card_style} onCardStyleChange={(chart_card_style) => onChange({ chart_card_style })}
          legendMode={state.show_chart_legend ? state.chart_legend_layout : "hidden"}
          onLegendModeChange={(mode) => onChange(mode === "hidden" ? { show_chart_legend: false } : { show_chart_legend: true, chart_legend_layout: mode })}
          legendTrimPrefix={state.chart_legend_trim_prefix} onLegendTrimPrefixChange={(chart_legend_trim_prefix) => onChange({ chart_legend_trim_prefix })}
          yScale={state.y_scale} onYScaleChange={(y_scale) => onChange({ y_scale })}
          warmup={state.y_warmup} onWarmupChange={(y_warmup) => onChange({ y_warmup })}
          smoothing={state.smoothing} onSmoothingChange={(value) => onChange({ smoothing: value })}
          tooltipDecimals={state.tooltip_decimals} onTooltipDecimalsChange={(value) => onChange({ tooltip_decimals: value })}
          tooltipSort={state.tooltip_sort} onTooltipSortChange={(value) => onChange({ tooltip_sort: value })}
          tooltipPlacement={state.tooltip_placement} onTooltipPlacementChange={(tooltip_placement) => onChange({ tooltip_placement })}
          timeDisplay={state.tooltip_time} onTimeDisplayChange={(value) => onChange({ tooltip_time: value })}
          deltaDisplay={state.tooltip_delta} onDeltaDisplayChange={(value) => onChange({ tooltip_delta: value })}
        /> : state.panel === "table" ? compareControls : <ImageSettings state={state} onChange={onChange} />}
        <div className="toolbar-actions">
          {storageError && <span className="local-save-indicator" title={storageError} role="status" aria-label="Local settings could not be saved"><CloudSlashIcon size={15} aria-hidden="true" /></span>}
        <RefreshControl interval={state.refresh_interval} onRefresh={onRefresh} onInterval={(refresh_interval) => onChange({ refresh_interval })} />
        <SettingsMenu ready={ready} state={state} views={localViews} onImport={onImportConfig}>
          <button type="button" className="menu-item theme-menu-item" onClick={onThemeToggle} aria-label={`Change theme, current: ${THEME_LABELS[themeMode]}`}><ThemeIcon size={16} aria-hidden="true" /><span>Theme: {THEME_LABELS[themeMode]}</span></button>
        </SettingsMenu>
        </div>
      </div>
    </header>
  );
}
