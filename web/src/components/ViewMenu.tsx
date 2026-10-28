import { useState } from "react";
import { BookmarkSimpleIcon, CaretDownIcon, CheckIcon, DotsThreeIcon } from "@phosphor-icons/react";
import { viewStateKey, type SavedView, type WorkspaceState } from "../workspace";
import Popover from "./Popover";
import ShareLink from "./ShareLink";

interface ViewMenuProps {
  state: WorkspaceState;
  views: SavedView[];
  sharedViews: SavedView[];
  ready: boolean;
  onOpen: (view: SavedView) => void;
  onSave: (name: string, viewId?: string) => Promise<void>;
  onDelete: (viewId: string) => Promise<void>;
  onRename: (viewId: string, name: string) => Promise<void>;
}

export default function ViewMenu({ state, views, sharedViews, ready, onOpen, onSave, onDelete, onRename }: ViewMenuProps) {
  const [creating, setCreating] = useState(false), [name, setName] = useState("");
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [manageId, setManageId] = useState<string | null>(null);
  const [action, setAction] = useState<"menu" | "rename" | "delete">("menu");
  const candidate = views.find((view) => view.view_id === manageId);
  const active = views.find((view) => view.view_id === state.active_view_id);
  const modified = active !== undefined && viewStateKey(active.state) !== viewStateKey(state);
  async function submit(operation: () => Promise<void>) {
    setBusy(true); setError("");
    try { await operation(); setCreating(false); setManageId(null); setName(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save view."); }
    finally { setBusy(false); }
  }
  return <Popover className="views-menu" label={active ? `Views: ${active.name}${modified ? ", modified" : ""}` : "Views"} trigger={<>
    <BookmarkSimpleIcon size={15} /><span className="view-name">{active?.name ?? "Views"}</span>{modified && <span className="modified-dot" title="Unsaved changes" aria-label="Modified" />}<CaretDownIcon size={11} />
  </>}>
    <div className="menu-heading">Views</div>
    <div className="saved-view-list">
      {!views.length && <p className="menu-note">Save a comparison to return to it later.</p>}
      {views.map((view) => <div key={view.view_id} className="saved-view-item">
        <button className="menu-item" title={view.name} disabled={busy || !ready} onClick={(event) => { onOpen(view); setError(""); setCreating(false); setManageId(null); event.currentTarget.closest("details")?.removeAttribute("open"); }}><span>{view.name}</span>{view.view_id === active?.view_id && <CheckIcon size={14} />}</button>
        <button className="view-delete-button" aria-label={`Manage view ${view.name}`} title="View actions" disabled={busy || !ready} onClick={() => { setManageId(manageId === view.view_id ? null : view.view_id); setCreating(false); setAction("menu"); setName(view.name); setError(""); }}><DotsThreeIcon size={17} /></button>
      </div>)}
    </div>
    {candidate && <div className="view-delete-confirm">
      {action === "menu" ? <><p>{candidate.name}</p><button className="menu-item" onClick={() => setAction("rename")}>Rename</button><button className="menu-item" onClick={() => setAction("delete")}>Delete</button></> : action === "rename" ? <form onSubmit={(event) => { event.preventDefault(); if (name.trim()) void submit(() => onRename(candidate.view_id, name.trim())); }} onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setManageId(null); } }}>
        <label className="field-label" htmlFor="rename-view">View name</label><input id="rename-view" className="text-input" autoFocus maxLength={80} value={name} onChange={(event) => setName(event.target.value)} />
        <div className="form-actions"><button className="button quiet-button" type="button" disabled={busy || !ready} onClick={() => setManageId(null)}>Cancel</button><button className="button primary-button" disabled={busy || !name.trim()}>Save name</button></div>
      </form> : <><p>Delete “{candidate.name}”?</p><div className="form-actions"><button className="button quiet-button" disabled={busy || !ready} onClick={() => setManageId(null)}>Cancel</button><button className="button danger-button" disabled={busy || !ready} onClick={() => void submit(() => onDelete(candidate.view_id))}>Delete view</button></div></>}
    </div>}
    <div className="view-save-actions">
      {active && <button className="menu-item" disabled={!modified || busy} onClick={() => void submit(() => onSave(active.name, active.view_id))}>Update view</button>}
      {!creating ? <button className="menu-item" disabled={busy || !ready} onClick={() => { setCreating(true); setManageId(null); setName(""); setError(""); }}>Save as View…</button> : <form className="view-save-form" onSubmit={(event) => { event.preventDefault(); if (name.trim()) void submit(() => onSave(name.trim())); }}>
        <label htmlFor="view-name">View name</label><input id="view-name" className="text-input" autoFocus maxLength={80} value={name} onChange={(event) => setName(event.target.value)} />
        <div className="form-actions"><button className="button quiet-button" type="button" onClick={() => setCreating(false)}>Cancel</button><button className="button primary-button" disabled={!name.trim() || busy}>Save view</button></div>
      </form>}
    </div>
    {error && <p className="menu-error" role="alert">{error}</p>}
    <ShareLink state={state} views={sharedViews} />
  </Popover>;
}
