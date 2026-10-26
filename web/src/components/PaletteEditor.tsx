import { useEffect, useRef, useState } from "react";
import { XIcon } from "@phosphor-icons/react";
import { slotColor, visibleColor } from "../colors";
import { paletteError, type PaletteInput, type PaletteSelection } from "../palettes";
import ColorPicker from "./ColorPicker";

export default function PaletteEditor({ initial, editing, onSave, onClose }: { initial: PaletteInput; editing: boolean; onSave: (input: PaletteInput) => Promise<void>; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(initial.name);
  const [colors, setColors] = useState([...initial.colors]);
  const [active, setActive] = useState(0);
  const [bulk, setBulk] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);
  const validation = paletteError(name, colors);
  const selection: PaletteSelection = { kind: "custom", id: "draft", name, colors };
  function changeColor(value: string) { setColors(colors.map((color, i) => i === active ? value : color)); setError(""); }
  function move(delta: number) {
    const target = active + delta, next = [...colors];
    [next[active], next[target]] = [next[target], next[active]];
    setColors(next); setActive(target);
  }
  async function save() {
    if (validation || busy) return;
    setBusy(true); setError("");
    try { await onSave({ name: name.trim(), colors: colors.map((color) => color.toLowerCase()) }); onClose(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not save palette. Try again."); setBusy(false); }
  }
  return <dialog ref={dialog} className="appearance-dialog palette-dialog" aria-labelledby="palette-title" onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="dialog-title"><h2 id="palette-title">{editing ? "Edit palette" : "New palette"}</h2><button className="button icon-button" disabled={busy} aria-label="Close palette editor" onClick={onClose}><XIcon size={16} /></button></div>
    <fieldset className="appearance-body settings-form palette-editor-fields" disabled={busy}>
      <label>Name<input className="text-input" value={name} maxLength={60} onChange={(event) => { setName(event.target.value); setError(""); }} autoFocus /></label>
      <div className="palette-colors-heading"><span className="field-label">Ordered colors · {colors.length}/24</span><button className="button quiet-button" disabled={colors.length >= 24} onClick={() => {
        let slot = colors.length, color: string;
        do { color = slotColor(slot++, "palette-editor", "dark"); } while (colors.some((item) => item.toLowerCase() === color));
        setColors([...colors, color]); setActive(colors.length);
      }}>Add color</button></div>
      <div className="color-choices palette-color-grid">{colors.map((color, i) => <button key={i} className="color-choice" aria-label={`Color ${i + 1}: ${color}`} aria-pressed={active === i} title={`${i + 1} · ${color}`} style={{ background: /^#[0-9a-f]{6}$/i.test(color) ? color : "transparent" }} onClick={() => setActive(i)} />)}</div>
      <div className="palette-color-actions"><span className="field-help">Color {active + 1}</span><button className="button quiet-button" aria-label="Move color left" disabled={active === 0} onClick={() => move(-1)}>←</button><button className="button quiet-button" aria-label="Move color right" disabled={active === colors.length - 1} onClick={() => move(1)}>→</button><button className="button quiet-button" disabled={colors.length <= 2} onClick={() => { setColors(colors.filter((_, i) => i !== active)); setActive(Math.min(active, colors.length - 2)); }}>Remove color</button></div>
      <div className="palette-picker-row"><ColorPicker value={colors[active]} onChange={changeColor} /><label>HEX<input className="text-input" aria-label="Palette color HEX" value={colors[active]} maxLength={7} spellCheck={false} aria-invalid={!/^#[0-9a-f]{6}$/i.test(colors[active])} onChange={(event) => changeColor(event.target.value)} /></label></div>
      <details className="palette-bulk"><summary>Paste HEX colors</summary><div className="settings-form"><label>Color list<textarea className="text-input" rows={2} placeholder="#7ECEF4 #EA68A2 #FF9A52 #A2DB86" value={bulk} onChange={(event) => setBulk(event.target.value)} /></label><button className="button quiet-button" onClick={() => {
        const next = bulk.trim().split(/[\s,;]+/), issue = paletteError("Palette", next);
        if (issue) { setError(issue); return; }
        setColors(next); setActive(0); setBulk(""); setError("");
      }}>Replace colors</button></div></details>
      <div className="palette-theme-previews">{(["light", "dark"] as const).map((theme) => <div key={theme} className={`palette-theme-preview preview-${theme}`}><span>{theme === "light" ? "Light" : "Dark"}</span><div className="palette-preview" role="img" aria-label={`${theme} palette preview`}>{colors.map((color, i) => <span key={i} style={{ background: /^#[0-9a-f]{6}$/i.test(color) ? visibleColor(color, theme, selection) : "transparent" }} />)}</div></div>)}</div>
      <p className="field-help">2–24 unique colors. Curves follow this order, then use additional shades. Light and dark previews show the displayed colors.</p>
      {(error || validation) && <p className="field-help" role="alert">{error || validation}</p>}
    </fieldset>
    <div className="appearance-footer"><button className="button quiet-button" disabled={busy} onClick={onClose}>Cancel</button><button className="button primary-button" disabled={!!validation || busy} onClick={() => void save()}>{busy ? "Saving…" : "Save palette"}</button></div>
  </dialog>;
}
