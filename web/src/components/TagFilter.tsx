import Select from "./Select";
import { TagIcon, XIcon } from "@phosphor-icons/react";
import type { RunSummary } from "../api";
import type { WorkspaceState } from "../workspace";
import CheckControl from "./CheckControl";
import Popover from "./Popover";

export default function TagFilter({ runs, state, onChange }: { runs: RunSummary[]; state: WorkspaceState; onChange: (patch: Partial<WorkspaceState>) => void }) {
  const tags = [...new Set([...runs.flatMap((run) => run.tags ?? []), ...state.tag_filter])].sort();
  const active = state.tag_filter.length > 0 || state.tag_filter_match === "empty";
  return <Popover className={`tag-filter${active ? " filter-active" : ""}`} label="Filter runs by tags" trigger={<><TagIcon size={16} />{active && <span className="filter-dot" />}</>}>
    <div className="menu-heading">Filter list by tags</div>
    <div className="settings-form"><label>Match<Select value={state.tag_filter_match} onChange={(event) => onChange({ tag_filter_match: event.target.value as WorkspaceState["tag_filter_match"] })}><option value="any">Any selected tag</option><option value="all">All selected tags</option><option value="none">None of selected tags</option><option value="empty">No tags</option></Select></label>
      {state.tag_filter_match !== "empty" && <div className="tag-options">{tags.map((tag) => <CheckControl key={tag} checked={state.tag_filter.includes(tag)} onChange={(checked) => onChange({ tag_filter: checked ? [...state.tag_filter, tag] : state.tag_filter.filter((value) => value !== tag) })}>{tag}</CheckControl>)}{!tags.length && <p className="field-help">No tags yet. Add tags from a run’s menu.</p>}</div>}
      <p className="field-help">Filters visible rows. Your run selection stays unchanged.</p>
      <button className="button quiet-button" disabled={!active} onClick={() => onChange({ tag_filter: [], tag_filter_match: "any" })}>Clear tag filter</button>
    </div>
  </Popover>;
}

export function TagFilterChips({ state, onChange }: { state: WorkspaceState; onChange: (patch: Partial<WorkspaceState>) => void }) {
  const empty = state.tag_filter_match === "empty";
  if (!empty && !state.tag_filter.length) return null;
  function clearTags() { onChange({ tag_filter: [], tag_filter_match: "any" }); }
  function removeTag(tag: string) {
    const remaining = state.tag_filter.filter((value) => value !== tag);
    onChange({ tag_filter: remaining, tag_filter_match: remaining.length ? state.tag_filter_match : "any" });
  }
  const mode = { any: "Any", all: "All", none: "Exclude", empty: "No tags" }[state.tag_filter_match];
  return <div className="run-filter-tags" role="group" aria-label="Active tag filters">
    {!empty && (state.tag_filter.length > 1 || state.tag_filter_match !== "any") && <span className="tag-filter-mode">{mode}:</span>}
    {empty ? <span className="tag-chip">No tags<button type="button" aria-label="Remove no-tags filter" onClick={clearTags}><XIcon size={12} /></button></span> : state.tag_filter.map((tag) => <span className="tag-chip" key={tag}>
      <span className="filter-tag-name" title={tag}>{tag}</span><button type="button" aria-label={`Remove tag filter ${tag}`} onClick={() => removeTag(tag)}><XIcon size={12} /></button>
    </span>)}
    <button type="button" className="text-button clear-run-filters" onClick={() => onChange({ search: "", tag_filter: [], tag_filter_match: "any" })}>Clear filters</button>
  </div>;
}
