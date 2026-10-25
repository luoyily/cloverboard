import { useEffect, useRef, useState } from "react";
import { XIcon } from "@phosphor-icons/react";
import { pooledResults, updateRunTags, type RunSummary } from "../api";
import { runLabel } from "../comparison";
import CheckControl from "./CheckControl";

export default function TagsDialog({ runs, onClose, onSaved }: { runs: RunSummary[]; onClose: () => void; onSaved: (id: string, tags: string[]) => void }) {
  const ref = useRef<HTMLDialogElement>(null), input = useRef<HTMLInputElement>(null);
  const editable = runs.filter((run) => run.tags_writable !== false);
  const single = runs.length === 1;
  const [chosen, setChosen] = useState<string[]>(single ? runs[0].tags ?? [] : []);
  const [removed, setRemoved] = useState<string[]>([]), [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false), [error, setError] = useState("");
  const [completed, setCompleted] = useState<Set<string>>(new Set());
  const available = [...new Set(editable.flatMap((run) => run.tags ?? []))].sort();
  useEffect(() => { ref.current?.showModal(); input.current?.focus(); }, []);
  function addTag(): boolean {
    const value = draft.trim();
    if (!value) return true;
    if (value.length > 80 || /[\x00-\x1f]/.test(value) || (!chosen.includes(value) && chosen.length >= 64)) { setError("Use up to 64 tags, each 1–80 characters without control characters."); return false; }
    setChosen((tags) => [...new Set([...tags, value])].sort()); setRemoved((tags) => tags.filter((tag) => tag !== value)); setDraft(""); setError("");
    return true;
  }
  async function save() {
    const pendingTag = draft.trim();
    if (pendingTag && !addTag()) return;
    const nextChosen = [...new Set([...chosen, ...(pendingTag ? [pendingTag] : [])])];
    setSaving(true); setError("");
    const pending = editable.filter((run) => !completed.has(run.run_id));
    const results = await pooledResults(pending, async (run) => {
      const add = single ? nextChosen.filter((tag) => !run.tags?.includes(tag)) : nextChosen;
      const remove = single ? (run.tags ?? []).filter((tag) => !nextChosen.includes(tag)) : removed;
      const result = await updateRunTags(run.run_id, add, remove);
      onSaved(run.run_id, result.tags);
      return run.run_id;
    });
    const failed = results.filter((result) => result.status === "rejected");
    if (!failed.length) { onClose(); return; }
    setCompleted((ids) => new Set([...ids, ...results.filter((result) => result.status === "fulfilled").map((result) => result.value)]));
    setError(`${failed.length} run(s) could not be updated. ${failed[0].reason instanceof Error ? failed[0].reason.message : "Check the source and retry."}`); setSaving(false);
  }
  return <dialog ref={ref} className="tags-dialog" aria-label={single ? `Edit tags for ${runLabel(runs[0])}` : "Edit tags for selected runs"} onCancel={(event) => { if (saving) event.preventDefault(); else onClose(); }} onClick={(event) => { if (event.target === event.currentTarget && !saving) onClose(); }}>
    <div className="dialog-title"><h2>{single ? "Edit tags" : "Edit tags for selection"}</h2><button className="button icon-button" disabled={saving} onClick={onClose} aria-label="Close tags"><XIcon size={16} /></button></div>
    <p className="field-help">{single ? runLabel(runs[0]) : `${editable.length} editable of ${runs.length} selected runs`}</p>
    {editable.length < runs.length && <p className="workspace-notice">Peer and read-only runs keep their tags unchanged.</p>}
    <label className="tag-entry-label">{single ? "Tags" : "Add to selected runs"}<div className="tag-entry"><input ref={input} className="text-input" aria-label="New tag" maxLength={80} value={draft} disabled={saving || !editable.length} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addTag(); } }} /><button className="button quiet-button" disabled={saving || !editable.length || !draft.trim()} onClick={addTag}>Add</button></div></label>
    <div className="tag-chips">{chosen.map((tag) => <span className="tag-chip" key={tag}>{tag}<button aria-label={`Remove ${tag}`} disabled={saving || !editable.length} onClick={() => setChosen(chosen.filter((value) => value !== tag))}><XIcon size={12} /></button></span>)}</div>
    {!single && available.length > 0 && <div className="settings-form"><div className="menu-heading">Remove from selected runs</div><div className="tag-options">{available.map((tag) => <CheckControl key={tag} disabled={saving} checked={removed.includes(tag)} onChange={(checked) => { setRemoved(checked ? [...removed, tag] : removed.filter((value) => value !== tag)); if (checked) setChosen(chosen.filter((value) => value !== tag)); }}>{tag}</CheckControl>)}</div></div>}
    <p className="field-help">Tags are case-sensitive. Enter adds a tag; Apply saves your changes.</p>
    {error && <p className="data-error" role="alert">{error}</p>}
    <div className="dialog-footer"><button className="button quiet-button" disabled={saving} onClick={onClose}>Cancel</button><button className="button primary-button" disabled={saving || !editable.length} onClick={() => void save()}>{saving ? "Saving…" : completed.size ? "Retry remaining" : "Apply"}</button></div>
  </dialog>;
}
