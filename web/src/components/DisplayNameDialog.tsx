import { useEffect, useRef, useState } from "react";
import { XIcon } from "@phosphor-icons/react";
import { updateRunDisplayName, type RunSummary } from "../api";
import { runName } from "../comparison";

export default function DisplayNameDialog({ run, onClose, onSaved }: { run: RunSummary; onClose: () => void; onSaved: (id: string, displayName: string | null) => void }) {
  const dialog = useRef<HTMLDialogElement>(null), input = useRef<HTMLInputElement>(null);
  const originalName = run.name ?? run.run_id;
  const [draft, setDraft] = useState(run.display_name ?? "");
  const [saving, setSaving] = useState(false), [error, setError] = useState("");
  useEffect(() => { dialog.current?.showModal(); input.current?.focus(); input.current?.select(); }, []);
  const normalized = draft.trim();
  async function save() {
    if (saving) return;
    if (normalized === (run.display_name ?? "")) { onClose(); return; }
    setSaving(true); setError("");
    try {
      const result = await updateRunDisplayName(run.run_id, normalized || null);
      onSaved(run.run_id, result.display_name);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save the display name.");
      setSaving(false);
    }
  }
  return <dialog ref={dialog} className="display-name-dialog" aria-label={`Edit display name for ${runName(run)}`} onCancel={(event) => { if (saving) event.preventDefault(); else onClose(); }} onClick={(event) => { if (event.target === event.currentTarget && !saving) onClose(); }}>
    <div className="dialog-title"><h2>Display name</h2><button className="button icon-button" disabled={saving} onClick={onClose} aria-label="Close display name"><XIcon size={16} /></button></div>
    <label className="display-name-label">Display name for {runName(run)}
      <input ref={input} className="text-input" value={draft} maxLength={200} disabled={saving} placeholder={originalName} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void save(); } }} />
    </label>
    <p className="field-help">Leave empty to use the original name. The run folder and Run ID stay unchanged.</p>
    {error && <p className="data-error" role="alert">{error}</p>}
    <div className="dialog-footer">
      <button className="button quiet-button" disabled={saving || !draft} onClick={() => { setDraft(""); input.current?.focus(); }}>Reset</button>
      <button className="button quiet-button" disabled={saving} onClick={onClose}>Cancel</button>
      <button className="button primary-button" disabled={saving} onClick={() => void save()}>{saving ? "Saving…" : "Save"}</button>
    </div>
  </dialog>;
}
