import { DotsThreeIcon } from "@phosphor-icons/react";
import type { MouseEvent } from "react";
import type { RunSummary } from "../api";
import { runLabel } from "../comparison";
import Popover from "./Popover";

interface RunActionsProps {
  run: RunSummary;
  baselineId: string | null;
  onBaseline: (id: string | null) => void;
  onDetails: (run: RunSummary, tab: "overview" | "notes") => void;
  onEditTags: (runs: RunSummary[]) => void;
}

export default function RunActions({ run, baselineId, onBaseline, onDetails, onEditTags }: RunActionsProps) {
  function close(event: MouseEvent<HTMLButtonElement>) {
    event.currentTarget.closest("details")?.removeAttribute("open");
  }
  return <Popover className="run-actions-menu" label={`Run actions for ${runLabel(run)}`} trigger={<DotsThreeIcon size={18} aria-hidden="true" />}>
    <div className="menu-heading">{runLabel(run)}</div>
    <button className="menu-item" onClick={(event) => { close(event); onDetails(run, "overview"); }}>Details</button>
    <button className="menu-item" onClick={(event) => { close(event); onEditTags([run]); }}>{run.tags_writable === false ? "View tags" : "Edit tags"}</button>
    <button className="menu-item" onClick={(event) => { close(event); onDetails(run, "notes"); }}><span>Notes</span>{run.has_notes && <span className="note-indicator" aria-label="Note recorded" />}</button>
    <div className="settings-divider" />
    <button className="menu-item" onClick={(event) => { close(event); onBaseline(baselineId === run.run_id ? null : run.run_id); }}>{baselineId === run.run_id ? "Clear baseline" : "Set as baseline"}</button>
    <p className="menu-note">One baseline per View. It stays selected.</p>
  </Popover>;
}
