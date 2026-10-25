import { useEffect, useRef, useState } from "react";
import { XIcon } from "@phosphor-icons/react";
import { assetUrl, fetchRunDetails, type RunDetails, type RunSummary } from "../api";
import { runLabel } from "../comparison";
import { formatWallTime } from "../readout";
import RunNotes from "./RunNotes";

export default function RunDetailsDialog({ run, initialTab = "overview", onClose, onNoteSaved, onEditTags }: { run: RunSummary; initialTab?: "overview" | "notes"; onClose: () => void; onNoteSaved: () => void; onEditTags: (runs: RunSummary[]) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [details, setDetails] = useState<RunDetails | null>(null), [error, setError] = useState(false);
  const [tab, setTab] = useState<"overview" | "notes" | "files">(initialTab);
  const [dirty, setDirty] = useState(false);
  function close() { if (!dirty || window.confirm("Discard unsaved note changes?")) onClose(); }
  useEffect(() => {
    if (!dirty) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", prevent); return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty]);
  useEffect(() => {
    ref.current?.showModal();
    const controller = new AbortController();
    void fetchRunDetails(run.run_id, controller.signal).then(setDetails).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [run.run_id]);
  const snapshot = details?.snapshot;
  return <dialog ref={ref} className="run-details-dialog" aria-label={`Details for ${runLabel(run)}`} onCancel={(event) => { event.preventDefault(); close(); }} onClick={(e) => { if (e.target === e.currentTarget) close(); }}>
    <div className="dialog-title"><h2>{runLabel(run)}</h2><button className="button icon-button" aria-label="Close run details" onClick={close}><XIcon size={16} /></button></div>
    <div className="run-detail-tabs line-tabs" role="tablist" aria-label="Run details">{(["overview", "notes", "files"] as const).map((value, index, tabs) => <button role="tab" id={`run-detail-${value}`} aria-controls="run-detail-panel" aria-selected={tab === value} tabIndex={tab === value ? 0 : -1} key={value} onClick={() => setTab(value)} onKeyDown={(event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[tabs.length - 1] : tabs[(index + (event.key === "ArrowLeft" ? tabs.length - 1 : 1)) % tabs.length];
      setTab(next); document.getElementById(`run-detail-${next}`)?.focus();
    }}>{value === "overview" ? "Overview" : value === "notes" ? "Notes" : "Files"}</button>)}</div>
    <div id="run-detail-panel" role="tabpanel" aria-labelledby={`run-detail-${tab}`}>
    {tab === "overview" && <div className="run-tags-section"><div className="run-tags-heading"><h3>Tags</h3><button className="text-button" onClick={() => onEditTags([run])}>{run.tags_writable === false ? "View tags" : "Edit tags"}</button></div><div className="tag-chips">{run.tags?.length ? run.tags.map((tag) => <span className="tag-chip" key={tag}>{tag}</span>) : <span className="field-help">No tags</span>}</div></div>}
    <div hidden={tab !== "notes"}><RunNotes runId={run.run_id} onDirty={setDirty} onSaved={onNoteSaved} /></div>
    {tab === "notes" ? null : error ? <p role="alert">Unable to load run details. Check the source connection and reopen.</p> : !details ? <p role="status">Loading…</p> : tab === "overview" ? <div className="run-detail-body"><dl><dt>Source</dt><dd>{run.source_label ?? "Local"}{run.source_online === false ? " · Offline" : ""}</dd><dt>Started</dt><dd>{formatWallTime(run.started_at, true)}</dd><dt>Run ID</dt><dd>{run.run_id}</dd></dl><h3>Parameters</h3><pre>{JSON.stringify(details.config, null, 2)}</pre><h3>Environment</h3><pre>{JSON.stringify(details.env, null, 2)}</pre></div> : <div className="run-detail-body">{snapshot ? <><div className="snapshot-heading"><span>Code snapshot · {snapshot.status}</span>{snapshot.archive_bytes != null && <a className="button quiet-button" href={assetUrl(run.run_id, "snapshot")} download>Download ZIP</a>}</div><p className="field-help">{snapshot.files?.length ?? 0} files · {((snapshot.archive_bytes ?? 0) / 1024).toFixed(1)} KB · {formatWallTime(snapshot.created_at, true)}</p>{snapshot.root && <p className="snapshot-root">{snapshot.root}</p>}{(snapshot.error || snapshot.limit_reason) && <p className="workspace-notice">{snapshot.error || snapshot.limit_reason}</p>}<ul className="snapshot-files">{snapshot.files?.map((file) => <li key={file.path}><span>{file.path}</span><small>{(file.bytes / 1024).toFixed(1)} KB</small></li>)}</ul>{Boolean(snapshot.skipped?.length) && <details><summary>{snapshot.skipped!.length} skipped files</summary><ul>{snapshot.skipped!.map((file) => <li key={file.path}>{file.path} · {file.reason}</li>)}</ul></details>}</> : <><h3>No code snapshot</h3><p className="field-help">Call <code>snapshot_code(root=..., include=[...])</code> after init to save a standalone ZIP.</p></>}</div>}
    </div>
  </dialog>;
}
