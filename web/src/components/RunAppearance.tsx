import { useEffect, useRef, useState } from "react";
import { XIcon } from "@phosphor-icons/react";
import type { RunSummary } from "../api";
import { slotColor, visibleColor, colorDistance } from "../colors";
import { runColorKey, type ColorSettings, type ColorRegistry } from "../features";
import type { ResolvedTheme } from "../theme";
import ColorPicker from "./ColorPicker";

export default function RunAppearance({ run, runs, selectedIds, settings, registry, colors, theme, onChange, onClose }: { run: RunSummary; runs: RunSummary[]; selectedIds: Set<string>; settings: ColorSettings; registry: ColorRegistry; colors: Map<string, string>; theme: ResolvedTheme; onChange: (settings: ColorSettings) => void; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), key = runColorKey(run);
  const [custom, setCustom] = useState(settings.overrides[key] ?? colors.get(run.run_id) ?? "#3276b8");
  const [automatic, setAutomatic] = useState(!settings.overrides[key]);
  const [touched, setTouched] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => { element?.close(); }; }, []);
  const occupied = runs.filter((r) => r.run_id !== run.run_id);
  const relevant = occupied.filter((r) => selectedIds.has(r.run_id));
  const choices = Array.from({ length: 24 }, (_, i) => {
    const color = slotColor(i, registry.seed, theme, settings.palette);
    return { color, score: relevant.length ? Math.min(...relevant.map((r) => colorDistance(colors.get(r.run_id)!, color))) : 1 };
  }).sort((a, b) => b.score - a.score).slice(0, 12);
  const valid = /^#[0-9a-f]{6}$/i.test(custom);
  const preview = valid ? visibleColor(custom, theme, settings.palette) : null;
  const conflicts = preview ? occupied.filter((r) => colors.get(r.run_id)?.toLowerCase() === preview.toLowerCase()) : [];
  const similar = preview ? relevant.filter((r) => !conflicts.includes(r) && colors.has(r.run_id) && colorDistance(colors.get(r.run_id)!, preview) < .065) : [];
  function choose(color: string) { setCustom(color); setAutomatic(false); setTouched(true); }
  function apply() {
    if (!valid) return;
    const overrides = { ...settings.overrides }, assignments = { ...settings.assignments };
    if (automatic) { delete overrides[key]; delete assignments[key]; }
    else overrides[key] = custom.toLowerCase();
    onChange({ ...settings, overrides, assignments });
    onClose();
  }
  return <dialog ref={dialog} className="appearance-dialog" aria-labelledby="appearance-title" onCancel={onClose} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <div className="dialog-title"><h2 id="appearance-title">Color · {run.name ?? run.run_id}</h2><button className="button icon-button" aria-label="Close color settings" onClick={onClose}><XIcon size={16} /></button></div>
    <div className="appearance-body settings-form">
      {settings.mode === "random" ? <>
        <span className="field-label">Recommended colors</span>
        <div className="color-choices">{choices.map(({ color }) => <button key={color} aria-label={`Use ${color}`} aria-pressed={!automatic && custom.toLowerCase() === color.toLowerCase()} title={color} className="color-choice" style={{ background: color }} onClick={() => choose(color)} />)}</div>
        <button className="button quiet-button custom-toggle" aria-expanded={customOpen} onClick={() => setCustomOpen(!customOpen)}>Custom color {customOpen ? "−" : "+"}</button>
        {customOpen && <div className="settings-form custom-color-section">
          <ColorPicker value={custom} onChange={choose} />
          <label>HEX<input className="text-input" value={custom} maxLength={7} spellCheck={false} onChange={(e) => choose(e.target.value)} aria-label="Custom hex color" aria-invalid={!valid} /></label>
          {!valid && <p className="field-help" role="status">Use a six-digit hex color, such as #3276b8.</p>}
        </div>}
        {valid && <div className="color-preview"><span className="preview-light"><i style={{ background: visibleColor(custom, "light", settings.palette) }} />Light</span><span className="preview-dark"><i style={{ background: visibleColor(custom, "dark", settings.palette) }} />Dark</span></div>}
        <p className="field-help">{automatic ? "Automatic color" : "Manual color · locked"} · Saved on Apply.</p>
        {conflicts.length > 0 && <p className="field-help" role="status">Also used by {conflicts.map((r) => r.name ?? r.run_id).join(", ")}.</p>}
        {similar.length > 0 && <p className="field-help" role="status">Similar to {similar.map((r) => r.name ?? r.run_id).join(", ")}.</p>}
      </> : <p className="field-help">Color follows {settings.field || "the selected parameter"}. Switch to Random in Colors to edit this run’s identity color.</p>}
    </div>
    <div className="appearance-footer">{settings.mode === "random" && <button className="button quiet-button reset-color-button" onClick={() => { setAutomatic(true); setTouched(true); setCustom(slotColor(registry.slots[key] ?? 0, registry.seed, theme, settings.palette)); }}>Reset to automatic</button>}<button className="button quiet-button" onClick={onClose}>{settings.mode === "random" ? "Cancel" : "Close"}</button>{settings.mode === "random" && <button className="button primary-button" disabled={!touched || !valid} onClick={apply}>Apply</button>}</div>
  </dialog>;
}
