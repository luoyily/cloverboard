import { useEffect, useState } from "react";
import type { TextRecord } from "../api";

export default function TextTile({ record }: { record: TextRecord }) {
  const [expanded, setExpanded] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  useEffect(() => { setExpanded(false); setCopyStatus(""); }, [record.name, record.step, record.text]);
  const long = record.text.length > 1000;
  async function copy() {
    try { await navigator.clipboard.writeText(record.text); setCopyStatus("Copied"); }
    catch { setCopyStatus("Copy failed"); }
  }
  return <div className="media-text-tile">
    {record.text === "" ? <p className="field-help">Empty text</p> : <pre className="media-text-body">{expanded ? record.text : record.text.slice(0, 1000)}</pre>}
    <div className="media-text-actions">
      {long && <button className="button quiet-button" onClick={() => setExpanded(!expanded)}>{expanded ? "Collapse" : "Show full text"}</button>}
      <button className="button quiet-button" onClick={() => void copy()}>Copy text</button>
      <span role="status">{copyStatus}</span>
    </div>
  </div>;
}
