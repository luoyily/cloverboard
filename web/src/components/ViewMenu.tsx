import { useState } from "react";
import { BookmarkSimpleIcon, CaretDownIcon, CheckIcon, DotsThreeIcon } from "@phosphor-icons/react";
import { viewStateKey, type SavedView, type WorkspaceState } from "../workspace";
import type { ActiveView, ViewScope } from "../viewingConfig";
import Popover from "./Popover";
import ShareLink from "./ShareLink";

interface ViewMenuProps {
  state: WorkspaceState;
  sharedViews: SavedView[];
  localViews: SavedView[];
  activeView: ActiveView | null;
  writable: boolean;
  ready: boolean;
  onOpen: (view: SavedView, scope: ViewScope) => void;
  onSave: (name: string, scope: ViewScope, viewId?: string) => Promise<void>;
  onDelete: (scope: ViewScope, viewId: string) => Promise<void>;
  onRename: (scope: ViewScope, viewId: string, name: string) => Promise<void>;
  onPromote: (viewId: string) => Promise<void>;
}

export default function ViewMenu({ state, sharedViews, localViews, activeView, writable, ready, onOpen, onSave, onDelete, onRename, onPromote }: ViewMenuProps) {
  const [creating, setCreating] = useState(false), [name, setName] = useState(""), [share, setShare] = useState(true);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [manage, setManage] = useState<{ scope: ViewScope; view_id: string } | null>(null);
  const [action, setAction] = useState<"menu" | "rename" | "delete">("menu");
  const lists: Record<ViewScope, SavedView[]> = { shared: sharedViews, local: localViews };
  const active = activeView ? lists[activeView.scope].find(view => view.view_id === activeView.view_id) : undefined;
  const candidate = manage ? lists[manage.scope].find(view => view.view_id === manage.view_id) : undefined;
  const modified = active !== undefined && viewStateKey(active.state) !== viewStateKey(state);
  async function submit(operation: () => Promise<void>) {
    setBusy(true); setError("");
    try { await operation(); setCreating(false); setManage(null); setName(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save View."); }
    finally { setBusy(false); }
  }
  function saveActive() {
    if (!active || !activeView) return Promise.resolve();
    if (activeView.scope === "shared" && !writable) {
      const copy = localViews.find(view => view.name.toLowerCase() === active.name.toLowerCase());
      return onSave(active.name, "local", copy?.view_id);
    }
    return onSave(active.name, activeView.scope, active.view_id);
  }
  function group(scope: ViewScope, label: string) {
    const views = lists[scope];
    return <>
      <div className="view-group-heading">{label}</div>
      {!views.length && <p className="menu-note">{scope === "shared" ? "No shared Views yet." : "Views saved in this browser appear here."}</p>}
      {views.map(view => {
        const current = activeView?.scope === scope && activeView.view_id === view.view_id;
        return <div key={view.view_id} className="saved-view-item">
          <button className="menu-item" title={view.name} disabled={busy || !ready} onClick={(event) => { onOpen(view, scope); setError(""); setCreating(false); setManage(null); event.currentTarget.closest("details")?.removeAttribute("open"); }}><span>{view.name}</span>{current && <CheckIcon size={14} />}</button>
          {(scope === "local" || writable) && <button className="view-delete-button" aria-label={`Manage view ${view.name}`} title="View actions" disabled={busy || !ready} onClick={() => { setManage(manage?.scope === scope && manage.view_id === view.view_id ? null : { scope, view_id: view.view_id }); setCreating(false); setAction("menu"); setName(view.name); setError(""); }}><DotsThreeIcon size={17} /></button>}
        </div>;
      })}
    </>;
  }
  return <Popover className="views-menu" label={active ? `Views: ${active.name}${modified ? ", modified" : ""}` : "Views"} trigger={<>
    <BookmarkSimpleIcon size={15} /><span className="view-name">{active?.name ?? "Views"}</span>{modified && <span className="modified-dot" title="Unsaved changes" aria-label="Modified" />}<CaretDownIcon size={11} />
  </>}>
    <div className="menu-heading">Views</div>
    <div className="saved-view-list">
      {group("shared", "Shared Views")}
      {group("local", "My Views")}
    </div>
    {candidate && manage && <div className="view-delete-confirm">
      {action === "menu" ? <><p>{candidate.name}</p>
        <button className="menu-item" onClick={() => setAction("rename")}>Rename</button>
        {manage.scope === "local" && writable && <button className="menu-item" disabled={busy} onClick={() => void submit(() => onPromote(candidate.view_id))}>Promote to Shared</button>}
        <button className="menu-item" onClick={() => setAction("delete")}>Delete</button></> : action === "rename" ? <form onSubmit={(event) => { event.preventDefault(); if (name.trim()) void submit(() => onRename(manage.scope, candidate.view_id, name.trim())); }} onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setManage(null); } }}>
        <label className="field-label" htmlFor="rename-view">View name</label><input id="rename-view" className="text-input" autoFocus maxLength={80} value={name} onChange={(event) => setName(event.target.value)} />
        <div className="form-actions"><button className="button quiet-button" type="button" disabled={busy || !ready} onClick={() => setManage(null)}>Cancel</button><button className="button primary-button" disabled={busy || !name.trim()}>Save name</button></div>
      </form> : <><p>Delete “{candidate.name}”?</p><div className="form-actions"><button className="button quiet-button" disabled={busy || !ready} onClick={() => setManage(null)}>Cancel</button><button className="button danger-button" disabled={busy || !ready} onClick={() => void submit(() => onDelete(manage.scope, candidate.view_id))}>Delete view</button></div></>}
    </div>}
    <div className="view-save-actions">
      {active && <button className="menu-item" disabled={!modified || busy} onClick={() => void submit(saveActive)}>{activeView?.scope === "shared" && !writable ? "Save copy to My Views" : "Update view"}</button>}
      {!creating ? <button className="menu-item" disabled={busy || !ready} onClick={() => { setCreating(true); setManage(null); setName(""); setShare(true); setError(""); }}>Save as View…</button> : <form className="view-save-form" onSubmit={(event) => { event.preventDefault(); if (name.trim()) void submit(() => onSave(name.trim(), writable && share ? "shared" : "local")); }}>
        <label htmlFor="view-name">View name</label><input id="view-name" className="text-input" autoFocus maxLength={80} value={name} onChange={(event) => setName(event.target.value)} />
        {writable && <label className="view-share-choice"><input type="checkbox" checked={share} onChange={(event) => setShare(event.target.checked)} /> Save to workspace (Shared)</label>}
        <div className="form-actions"><button className="button quiet-button" type="button" onClick={() => setCreating(false)}>Cancel</button><button className="button primary-button" disabled={!name.trim() || busy}>Save view</button></div>
      </form>}
    </div>
    {error && <p className="menu-error" role="alert">{error}</p>}
    <ShareLink state={state} activeView={activeView} sharedViews={sharedViews} />
  </Popover>;
}
