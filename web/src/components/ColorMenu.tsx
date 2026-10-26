import Select from "./Select";
import { useState } from "react";
import { PaletteIcon } from "@phosphor-icons/react";
import type { RunSummary } from "../api";
import { flattenConfig } from "../comparison";
import { reassignColors, slotColor } from "../colors";
import { parameterValue, type ColorRegistry, type ColorSettings } from "../features";
import type { ResolvedTheme } from "../theme";
import { DEFAULT_PALETTE, paletteBaseColors, paletteKey, paletteName, type CustomPalette, type PaletteInput } from "../palettes";
import Popover from "./Popover";
import PaletteEditor from "./PaletteEditor";

export default function ColorMenu({ runs, selectedIds, settings, registry, theme, localOnly, writable, ready, onSavePalette, onDeletePalette, onChange }: { runs: RunSummary[]; selectedIds: Set<string>; settings: ColorSettings; registry: ColorRegistry; theme: ResolvedTheme; localOnly: boolean; writable: boolean; ready: boolean; onSavePalette: (input: PaletteInput, id?: string) => Promise<CustomPalette>; onDeletePalette: (id: string) => Promise<void>; onChange: (settings: ColorSettings) => void }) {
  const [undo, setUndo] = useState<ColorSettings | null>(null);
  const [domainText, setDomainText] = useState<[string, string] | null>(null);
  const [error, setError] = useState("");
  const [editor, setEditor] = useState<{ initial: PaletteInput; id?: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const savedPalette = registry.palettes.find((item) => item.id === settings.palette.id);
  const snapshotOnly = settings.palette.kind === "custom" && (!savedPalette || paletteKey({ kind: "custom", ...savedPalette }) !== paletteKey(settings.palette) || savedPalette.name !== settings.palette.name);
  const baseColors = paletteBaseColors(settings.palette);
  const canCreate = writable && ready && registry.palettes.length < 32;
  const fields = [...new Set(runs.flatMap((r) => [...flattenConfig(r.config).keys()]))].sort();
  const numeric = fields.filter((field) => runs.some((r) => typeof parameterValue(r, field) === "number"));
  function patch(values: Partial<ColorSettings>) { onChange({ ...settings, ...values }); setError(""); setDomainText(null); setConfirmDelete(false); }
  function openEditor(button: HTMLButtonElement, editing: boolean) {
    button.closest("details")?.removeAttribute("open");
    setEditor({ ...(editing && savedPalette ? { id: savedPalette.id } : {}), initial: editing && savedPalette ? { name: savedPalette.name, colors: [...savedPalette.colors] } : { name: `${paletteName(settings.palette)} copy`.slice(0, 60), colors: [...baseColors] } });
    setError(""); setConfirmDelete(false);
  }
  function domainFor(field: string): [number, number] {
    const values = runs.map((r) => parameterValue(r, field)).filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    const low = values.length ? Math.min(...values) : 0, high = values.length ? Math.max(...values) : 1;
    return low === high ? [low, low + (Math.abs(low) || 1)] : [low, high];
  }
  return <><Popover className="colors-menu" label="Colors" trigger={<><PaletteIcon size={15} /><span>Colors</span></>}>
    <div className="menu-heading">Color system</div>
    <div className="settings-form">
      <label>Mode<Select value={settings.mode} onChange={(e) => {
        const mode = e.target.value as ColorSettings["mode"];
        const field = mode === "continuous" ? (numeric.includes(settings.field) ? settings.field : numeric[0] ?? "") : fields.includes(settings.field) ? settings.field : fields[0] ?? "";
        patch({ mode, field, ...(mode === "continuous" ? { domain: domainFor(field) } : {}) });
      }}><option value="random">Random · stable identities</option><option value="parameter">By parameter · families</option><option value="continuous">Continuous · Viridis</option></Select></label>
      {settings.mode !== "random" && <label>Parameter<Select value={settings.field} onChange={(e) => patch({ field: e.target.value, ...(settings.mode === "continuous" ? { domain: domainFor(e.target.value) } : {}) })}>
        {(settings.mode === "continuous" ? numeric : fields).map((field) => <option key={field} value={field}>{field}</option>)}
        {!(settings.mode === "continuous" ? numeric : fields).includes(settings.field) && <option value={settings.field}>{settings.field || "No recorded parameter"}</option>}
      </Select></label>}
      {settings.mode === "parameter" && <p className="field-help">Each parameter value uses a color from the selected palette. Runs within a family use subtle shades; hover a run to isolate it.</p>}
      {settings.mode === "continuous" && <>
        <div className="inline-fields">{([0, 1] as const).map((i) => <label key={i}>{i === 0 ? "Minimum" : "Maximum"}<input className="text-input" type="number" step="any" value={domainText?.[i] ?? settings.domain[i]} onChange={(e) => { const next: [string, string] = domainText ? [...domainText] : settings.domain.map(String) as [string, string]; next[i] = e.target.value; setDomainText(next); }} /></label>)}</div>
        <div className="inline-fields"><button className="button quiet-button" onClick={() => { const d = domainText?.map(Number) as [number, number] | undefined; if (!d || domainText?.some((s) => !s.trim()) || !d.every(Number.isFinite) || d[0] >= d[1]) setError("Use a finite, increasing range."); else patch({ domain: d }); }}>Apply range</button><button className="button quiet-button" onClick={() => patch({ domain: domainFor(settings.field) })}>Fit current runs</button></div>
        <p className="field-help">Saved range stays fixed as runs arrive. Values outside it use the end colors.</p>
      </>}
      {settings.mode !== "continuous" && <>
        <label>Palette<Select disabled={deleting} value={snapshotOnly ? "view-snapshot" : settings.palette.id} onChange={(e) => {
          const id = e.target.value;
          if (id === "view-snapshot") return;
          const custom = registry.palettes.find((item) => item.id === id);
          patch({ palette: custom ? { kind: "custom", ...custom, colors: [...custom.colors] } : { kind: "builtin", id: id === "bright" ? "bright" : "default" } }); setUndo(null);
        }}>
          <optgroup label="Built-in"><option value="default">Default</option><option value="bright">Bright</option></optgroup>
          {registry.palettes.length > 0 && <optgroup label="Workspace">{registry.palettes.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</optgroup>}
          {snapshotOnly && <option value="view-snapshot">{paletteName(settings.palette)} · View snapshot</option>}
        </Select></label>
        <div className="palette-preview" role="img" aria-label={`${paletteName(settings.palette)} palette, ${baseColors.length} colors`}>
          {Array.from({ length: baseColors.length }, (_, i) => {
            const color = slotColor(i, registry.seed, theme, settings.palette);
            return <span key={i} style={{ background: color }} title={color} />;
          })}
        </div>
        <p className="field-help">{baseColors.length} base colors, with additional shades for more {settings.mode === "parameter" ? "families" : "runs"}. Colors adapt to light and dark backgrounds.</p>
        <div className="palette-library-actions"><button className="button quiet-button" disabled={!canCreate || deleting} onClick={(event) => openEditor(event.currentTarget, false)}>{snapshotOnly && !savedPalette ? "Save a copy" : "New palette"}</button>{savedPalette && <><button className="button quiet-button" disabled={!writable || !ready || deleting} onClick={(event) => openEditor(event.currentTarget, true)}>Edit</button><button className="button quiet-button" disabled={!writable || !ready || deleting} onClick={() => setConfirmDelete(!confirmDelete)}>Delete</button></>}</div>
        {confirmDelete && savedPalette && <div className="settings-form palette-delete-confirm"><p className="field-help">Delete “{savedPalette.name}” from this workspace? Saved Views keep their colors.</p><div className="inline-fields"><button className="button quiet-button" disabled={deleting} onClick={() => setConfirmDelete(false)}>Cancel</button><button className="button quiet-button" disabled={deleting} onClick={async () => {
          setDeleting(true); setError("");
          try { await onDeletePalette(savedPalette.id); patch({ palette: { ...DEFAULT_PALETTE } }); setUndo(null); }
          catch (cause) { setError(cause instanceof Error ? cause.message : "Could not delete palette."); }
          finally { setDeleting(false); }
        }}>{deleting ? "Deleting…" : "Confirm delete"}</button></div></div>}
        {snapshotOnly && <p className="field-help">This View uses a saved palette snapshot. Select a workspace palette to use its latest colors.</p>}
        {!writable ? <p className="field-help">Read-only workspace · palettes can be selected; editing is disabled.</p> : !ready ? <p className="field-help">Waiting for workspace palette library…</p> : registry.palettes.length >= 32 ? <p className="field-help">Workspace limit reached: 32 custom palettes.</p> : null}
      </>}
      {settings.mode === "random" && <>
        <p className="field-help">Click a run’s swatch to edit its color. Manual colors stay locked.</p>
        <div className="inline-fields"><button className="button quiet-button" disabled={selectedIds.size === 0} onClick={() => { setUndo(settings); onChange(reassignColors(runs, selectedIds, registry, settings)); }}>Reassign selected</button><button className="button quiet-button" disabled={!undo} onClick={() => { if (undo) onChange({ ...settings, assignments: undo.assignments }); setUndo(null); }}>Undo</button></div>
      </>}
      {error && <p className="field-help" role="alert">{error}</p>}
      {localOnly && <p className="field-help" role="status">Color identities could not be saved. Refresh to retry.</p>}
    </div>
  </Popover>{editor && <PaletteEditor initial={editor.initial} editing={!!editor.id} onClose={() => setEditor(null)} onSave={async (input) => {
    const palette = await onSavePalette(input, editor.id);
    patch({ palette: { kind: "custom", ...palette } }); setUndo(null);
  }} />}</>;
}
