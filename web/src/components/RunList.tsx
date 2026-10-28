import { ArrowDownIcon, ArrowUpIcon, CloverIcon, MagnifyingGlassIcon, DotsThreeIcon } from "@phosphor-icons/react";
import type { RunSummary } from "../api";
import { useEffect, useId, useMemo, useRef, useState, type MouseEvent } from "react";
import RunActions from "./RunActions";
import { matchesTagFilter } from "../features";
import type { WorkspaceState } from "../workspace";
import TagFilter, { TagFilterChips } from "./TagFilter";
import Popover from "./Popover";
import { createRunSearch, recentRunIds } from "../runSelection";
import Checkbox from "./Checkbox";

interface RunListProps {
  runs: RunSummary[];
  selectedIds: Set<string>;
  colors: Map<string, string>;
  onToggle: (runId: string) => void;
  onBulkSelect: (ids: string[], selected: boolean) => void;
  onSelectOnly: (ids: string[]) => void;
  sidebarWidth: number;
  onSidebarWidth: (value: number) => void;
  filter: string;
  onFilterChange: (value: string) => void;
  state: WorkspaceState;
  onChange: (patch: Partial<WorkspaceState>) => void;
  onEditTags: (runs: RunSummary[]) => void;
  baselineId: string | null;
  focusedId: string | null;
  onFocusRun: (id: string | null) => void;
  locateRequest: { id: string; sequence: number } | null;
  onAppearance: (run: RunSummary) => void;
  onDetails: (run: RunSummary, tab: "overview" | "notes") => void;
  onBaseline: (id: string | null) => void;
}

export default function RunList({
  runs,
  selectedIds,
  colors,
  onToggle,
  onBulkSelect, onSelectOnly, sidebarWidth, onSidebarWidth,
  filter,
  onFilterChange,
  baselineId, focusedId, onFocusRun, locateRequest, onAppearance, onDetails, onBaseline, state, onChange, onEditTags,
}: RunListProps) {
  const checkboxPrefix = useId();
  const listRef = useRef<HTMLUListElement>(null);
  const rows = useRef(new Map<string, HTMLDivElement>());
  const [locatedId, setLocatedId] = useState<string | null>(null);
  useEffect(() => {
    if (!locateRequest) { setLocatedId(null); return; }
    const row = rows.current.get(locateRequest.id), list = listRef.current;
    if (!row || !list) return;
    const bounds = row.getBoundingClientRect(), viewport = list.getBoundingClientRect();
    // Scroll only the run list, leaving the chart and page in place.
    if (bounds.top < viewport.top) list.scrollTop += bounds.top - viewport.top;
    else if (bounds.bottom > viewport.bottom) list.scrollTop += bounds.bottom - viewport.bottom;
    setLocatedId(locateRequest.id);
    const timer = window.setTimeout(() => setLocatedId(null), 1500);
    return () => window.clearTimeout(timer);
  }, [locateRequest]);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const resize = (value: number) => onSidebarWidth(Math.round(Math.max(220, Math.min(value, 600, window.innerWidth * .45))));
  const search = useMemo(() => createRunSearch(filter, state.search_regex), [filter, state.search_regex]);
  const lastValidSearch = useRef(createRunSearch("", false));
  useEffect(() => { if (!search.error) lastValidSearch.current = search; }, [search]);
  const activeSearch = search.error ? lastValidSearch.current : search;
  const visibleRuns = runs.filter((run) => {
    if (run.run_id === locateRequest?.id) return true;
    if (!matchesTagFilter(run, state.tag_filter, state.tag_filter_match)) return false;
    return activeSearch.matches(run);
  });
  const visibleIds = visibleRuns.map((run) => run.run_id);
  const selectedVisible = visibleIds.filter((id) => selectedIds.has(id)).length;
  const selectedRuns = runs.filter((run) => selectedIds.has(run.run_id));
  const hiddenSelected = selectedRuns.length - selectedVisible;
  const filtering = (state.search_regex ? filter !== "" : filter.trim() !== "") || state.tag_filter.length > 0 || state.tag_filter_match === "empty";
  const timeSort = state.run_sort.startsWith("started_");
  const SortArrow = state.run_sort.endsWith("desc") ? ArrowDownIcon : ArrowUpIcon;
  function menuAction(event: MouseEvent<HTMLButtonElement>, action: () => void) {
    event.currentTarget.closest("details")?.removeAttribute("open");
    action();
  }

  return (
    <aside className="sidebar" aria-label="Runs">
      <div className="sidebar-brand">
        <CloverIcon
          className="brand-mark"
          size={24}
          weight="fill"
          aria-hidden="true"
        />
        <span className="brand">CloverBoard</span>
      </div>
      <div className="sidebar-content">
        <div className="sidebar-heading">
          <h2>Runs <span className="run-count" aria-label={filtering ? `${visibleRuns.length} of ${runs.length} runs shown` : `${runs.length} runs`} title={filtering ? `${visibleRuns.length} of ${runs.length} runs shown` : "Total runs"}>{filtering ? `${visibleRuns.length}/${runs.length}` : runs.length}</span></h2>
          <div className="run-heading-tools">
            <div className="run-sort-controls" role="group" aria-label="Run sorting">
              <button type="button" className="button quiet-button run-sort-button" aria-label="Sort by start time" aria-pressed={timeSort} title={state.run_sort === "started_asc" ? "Started · oldest first. Click for newest first." : "Started · newest first. Click to sort or reverse."} onClick={() => onChange({ run_sort: state.run_sort === "started_desc" ? "started_asc" : "started_desc" })}>Time{timeSort && <SortArrow size={11} aria-hidden="true" />}</button>
              <button type="button" className="button quiet-button run-sort-button" aria-label="Sort by name" aria-pressed={!timeSort} title={state.run_sort === "name_desc" ? "Name · Z–A. Click for A–Z." : "Name · A–Z. Click to sort or reverse."} onClick={() => onChange({ run_sort: state.run_sort === "name_asc" ? "name_desc" : "name_asc" })}>Name{!timeSort && <SortArrow size={11} aria-hidden="true" />}</button>
            </div>
            <TagFilter runs={runs} state={state} onChange={onChange} />
          </div>
        </div>
        <div className="run-search-row"><div className={`search-field${search.error ? " search-invalid" : ""}`}>
          <MagnifyingGlassIcon size={16} aria-hidden="true" />
          <input
            className="search"
            type="search"
            aria-label="Search runs"
            placeholder="Search runs"
            maxLength={1000}
            aria-invalid={Boolean(search.error)}
            aria-describedby={search.error ? `${checkboxPrefix}-search-error` : undefined}
            value={filter}
            onChange={(event) => onFilterChange(event.target.value)}
          />
          <button type="button" className="regex-toggle" aria-label="Use regular expression" aria-pressed={state.search_regex} title="Regular expression · case-insensitive" onClick={() => onChange({ search_regex: !state.search_regex })}>.*</button>
        </div>
          {search.error && <p className="run-search-error" id={`${checkboxPrefix}-search-error`} role="alert" title={search.error}>Invalid regular expression. Showing previous results.</p>}
        </div>
        <TagFilterChips state={state} onChange={onChange} />
        <div className="run-selection-bar">
          <label className="run-checkbox" title={`Select or deselect the ${visibleIds.length} visible runs. Other selections are kept.`}>
            <Checkbox label="Select filtered runs" disabled={Boolean(search.error) || !visibleIds.length} checked={visibleIds.length > 0 && selectedVisible === visibleIds.length} indeterminate={selectedVisible > 0 && selectedVisible < visibleIds.length} onChange={(checked) => onBulkSelect(visibleIds, checked)} />
          </label>
          <span className="run-selection-count" role="status" title={hiddenSelected ? `${selectedRuns.length} selected runs. ${hiddenSelected} hidden by list filters; they remain selected.` : `${selectedRuns.length} selected runs`}>{selectedRuns.length} selected{hiddenSelected > 0 && <span className="selection-hidden"> · {hiddenSelected} hidden</span>}</span>
          <Popover className="selection-menu" label="Selection actions" trigger={<DotsThreeIcon size={18} />}>
            <div className="menu-heading">Select latest with metrics</div>
            <div className="recent-run-options">{[1, 3, 5, 10].map((count) => <button className="button quiet-button" disabled={!runs.some((run) => run.has_scalars)} key={count} onClick={(event) => menuAction(event, () => onSelectOnly(recentRunIds(runs, count)))}>Latest {count}</button>)}</div>
            <div className="settings-divider" />
            <button className="menu-item" disabled={!selectedRuns.length} onClick={(event) => menuAction(event, () => onEditTags(selectedRuns))}>Edit tags for selection…</button>
          </Popover>
        </div>
        <ul className="run-list" ref={listRef}>
          {visibleRuns.map((run) => {
            const selected = selectedIds.has(run.run_id);
            return (
              <li key={run.run_id}>
                <div ref={(element) => { if (element) rows.current.set(run.run_id, element); else rows.current.delete(run.run_id); }} className={`run-item${selected ? " selected" : ""}${focusedId === run.run_id || locatedId === run.run_id ? " run-focused" : ""}${locatedId === run.run_id ? " run-located" : ""}`} onClick={(event) => { if (event.target instanceof Element && !event.target.closest("button,input,label,details")) onToggle(run.run_id); }} onPointerEnter={() => onFocusRun(run.run_id)} onPointerLeave={() => onFocusRun(null)} onFocus={() => onFocusRun(run.run_id)} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) onFocusRun(null); }}>
                  <label className="run-checkbox">
                    <Checkbox
                      id={`${checkboxPrefix}-${run.run_id}`}
                      label={`Select ${run.name ?? run.run_id}`}
                      checked={selected}
                      onChange={() => onToggle(run.run_id)}
                    />
                  </label>
                  <button className="run-swatch-button" type="button" aria-label={`Color for ${run.name ?? run.run_id}`} title="Change color" onClick={() => onAppearance(run)}><span className="swatch" style={{ background: colors.get(run.run_id) }} /></button>
                  <label className="run-name" htmlFor={`${checkboxPrefix}-${run.run_id}`}
                    title={`${run.name ?? run.run_id}\n${run.source_label ?? run.source}\n${run.run_id}`}
                  >
                    <span className="run-name-primary"><span>{run.name ?? run.run_id}</span>{baselineId === run.run_id && <span className="baseline-mark" title="Baseline" aria-label="Baseline">B</span>}</span>
                    {run.source_label && <small className="run-source">@{run.source_label}</small>}
                  </label>
                  <RunActions run={run} baselineId={baselineId} onBaseline={onBaseline} onDetails={onDetails} onEditTags={onEditTags} />
                </div>
              </li>
            );
          })}
          {visibleRuns.length === 0 && (
            <li className="run-empty muted">
              {runs.length ? "No matching runs" : "No runs yet"}
            </li>
          )}
        </ul>
      </div>
      <div className="sidebar-resize" role="separator" aria-label="Resize experiment list" aria-orientation="vertical" aria-valuemin={220} aria-valuemax={600} aria-valuenow={sidebarWidth} tabIndex={0}
        onDoubleClick={() => onSidebarWidth(280)}
        onKeyDown={(event) => { if (["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) { event.preventDefault(); resize(event.key === "Home" ? 280 : sidebarWidth + (event.key === "ArrowRight" ? 16 : -16)); } }}
        onPointerDown={(event) => { if (event.button !== 0) return; event.preventDefault(); drag.current = { x: event.clientX, width: sidebarWidth }; event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={(event) => { if (drag.current) resize(drag.current.width + event.clientX - drag.current.x); }}
        onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }} />
    </aside>
  );
}
