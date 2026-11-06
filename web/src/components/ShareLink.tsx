import { useEffect, useState } from "react";
import { LinkIcon } from "@phosphor-icons/react";
import { getJson } from "../api";
import type { SavedView, WorkspaceState } from "../workspace";
import type { ActiveView } from "../viewingConfig";
import { shareTarget } from "../share";

export default function ShareLink({ state, activeView, sharedViews }: { state: WorkspaceState; activeView: ActiveView | null; sharedViews: SavedView[] }) {
  const [link, setLink] = useState(""), [message, setMessage] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => { setLink(""); setMessage(""); }, [state, activeView]);
  async function copy() {
    setBusy(true); setMessage("");
    try {
      const target = shareTarget(state, activeView, sharedViews);
      if (target.kind === "too-large") throw new Error("This view is too large for a link. Export your settings as JSON instead.");
      const url = new URL(window.location.href); url.search = ""; url.hash = "";
      if (target.kind === "view") url.searchParams.set("view", target.viewId);
      else url.hash = new URLSearchParams({ state: target.encoded }).toString();
      const { token } = await getJson<{ token: string | null }>("/api/share-token");
      if (token) url.searchParams.set("token", token);
      const address = url.toString(); setLink(address);
      try {
        await navigator.clipboard.writeText(address);
        setMessage(target.kind === "view" ? "Copied. This link follows the shared View and stays current." : "Link copied. It snapshots your current settings.");
      } catch { setMessage("Select and copy the link below."); }
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "Unable to create link."); }
    finally { setBusy(false); }
  }
  return <div className="share-link-section"><button className="menu-item" disabled={busy} onClick={() => void copy()}><span><LinkIcon size={14} /> Copy link to current view</span></button>
    {message && <p className="field-help" role="status">{message}</p>}
    {link && <><input className="text-input share-link-input" aria-label="Share URL" readOnly value={link} onFocus={(event) => event.currentTarget.select()} /><p className="field-help">Opens on this server. Shared access expires when its token changes.</p></>}
  </div>;
}
