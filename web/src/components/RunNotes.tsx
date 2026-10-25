import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { fetchNote, saveNote, type RunNote } from "../api";
import { formatWallTime } from "../readout";

export default function RunNotes({ runId, onDirty, onSaved }: { runId: string; onDirty: (dirty: boolean) => void; onSaved: () => void }) {
  const [note, setNote] = useState<RunNote | null>(null), [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false), [preview, setPreview] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState(""), [reload, setReload] = useState(0);
  const dirty = note !== null && draft !== note.body;
  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  useEffect(() => {
    const controller = new AbortController(); setError("");
    void fetchNote(runId, controller.signal).then((value) => { if (!controller.signal.aborted) { setNote(value); setDraft(value.body); setEditing(false); } }).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load note."); });
    return () => controller.abort();
  }, [runId, reload]);
  async function save() {
    if (!note) return;
    setBusy(true); setError("");
    try { const saved = await saveNote(runId, draft, note.revision); setNote(saved); setDraft(saved.body); setEditing(false); setPreview(false); onSaved(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save note."); }
    finally { setBusy(false); }
  }
  const rendered = <div className="notes-markdown"><Markdown skipHtml components={{ img: () => null, a: ({ children, ...props }) => <a {...props} target="_blank" rel="noreferrer">{children}</a> }}>{editing ? draft : note?.body || "No notes yet."}</Markdown></div>;
  return <div className="run-detail-body run-notes">
    {note ? <><div className="notes-heading"><span className="field-help">{note.updated_at ? `Updated ${formatWallTime(note.updated_at, true)}` : "Observations, decisions and results for this run."}</span>{note.writable && !editing && <button className="button quiet-button" onClick={() => { setEditing(true); setPreview(false); }}>{note.body ? "Edit note" : "Add note"}</button>}</div>
      {!note.writable && <p className="field-help">Read-only{note.body ? "" : " · No note recorded"}. Open the source workspace to edit.</p>}
      {editing ? <><div className="notes-editor-tabs line-tabs" role="tablist" aria-label="Note editor">{([false, true] as const).map((value) => <button key={String(value)} role="tab" id={value ? "note-preview-tab" : "note-write-tab"} aria-controls="note-editor-panel" aria-selected={preview === value} tabIndex={preview === value ? 0 : -1} onClick={() => setPreview(value)} onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? false : event.key === "End" ? true : !value;
          setPreview(next); document.getElementById(next ? "note-preview-tab" : "note-write-tab")?.focus();
        }}>{value ? "Preview" : "Write"}</button>)}</div>
        <div id="note-editor-panel" role="tabpanel" aria-labelledby={preview ? "note-preview-tab" : "note-write-tab"}>{preview ? rendered : <textarea className="text-input notes-textarea" aria-label="Run note" maxLength={20000} value={draft} disabled={busy} onChange={(event) => setDraft(event.target.value)} autoFocus placeholder="What changed? What did you observe?" />}</div>
        <div className="notes-editor-footer"><span className="field-help">Markdown · {draft.length} / 20000{dirty ? " · Unsaved" : ""}</span><div className="form-actions"><button className="button quiet-button" disabled={busy} onClick={() => { setDraft(note.body); setEditing(false); setPreview(false); setError(""); }}>Cancel</button><button className="button primary-button" disabled={busy || !dirty} onClick={() => void save()}>{busy ? "Saving…" : "Save note"}</button></div></div>
      </> : rendered}
    </> : !error && <p role="status">Loading note…</p>}
    {error && <div role="alert"><p className="menu-error">{error}</p><button className="button quiet-button" disabled={busy} onClick={() => { if (!dirty || window.confirm("Discard your draft and reload the saved note?")) setReload((value) => value + 1); }}>Reload note</button></div>}
  </div>;
}
