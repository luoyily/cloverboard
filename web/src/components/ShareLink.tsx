import { useEffect, useState } from "react";
import { LinkIcon } from "@phosphor-icons/react";
import { getJson } from "../api";
import { encodeSharedState, viewStateKey, type SavedView, type WorkspaceState } from "../workspace";

export default function ShareLink({ state, views }: { state: WorkspaceState; views: SavedView[] }) {
  const [link, setLink] = useState(""), [message, setMessage] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => { setLink(""); setMessage(""); }, [state]);
  async function copy() {
    setBusy(true); setMessage("");
    try {
      const url = new URL(window.location.href); url.search = ""; url.hash = "";
      try { url.hash = new URLSearchParams({ state: encodeSharedState(state) }).toString(); }
      catch (reason) {
        const view = views.find((item) => item.view_id === state.active_view_id);
        if (!view || viewStateKey(view.state) !== viewStateKey(state)) throw reason;
        url.searchParams.set("view", view.view_id);
      }
      const { token } = await getJson<{ token: string | null }>("/api/share-token");
      if (token) url.searchParams.set("token", token);
      const address = url.toString(); setLink(address);
      try { await navigator.clipboard.writeText(address); setMessage(url.searchParams.has("view") ? "Copied. This link follows saved View updates." : "Link copied."); }
      catch { setMessage("Select and copy the link below."); }
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "Unable to create link."); }
    finally { setBusy(false); }
  }
  return <div className="share-link-section"><button className="menu-item" disabled={busy} onClick={() => void copy()}><span><LinkIcon size={14} /> Copy link to current view</span></button>
    {message && <p className="field-help" role="status">{message}</p>}
    {link && <><input className="text-input share-link-input" aria-label="Share URL" readOnly value={link} onFocus={(event) => event.currentTarget.select()} /><p className="field-help">Opens on this server. Shared access expires when its token changes.</p></>}
  </div>;
}
