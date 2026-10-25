import { useState } from "react";
import { PaletteIcon } from "@phosphor-icons/react";
import type { RunSummary } from "../api";
import { flattenConfig } from "../comparison";
import { reassignColors } from "../colors";
import { parameterValue, type ColorRegistry, type ColorSettings } from "../features";
import Popover from "./Popover";

export default function ColorMenu({ runs, selectedIds, settings, registry, localOnly, onChange }: { runs: RunSummary[]; selectedIds: Set<string>; settings: ColorSettings; registry: ColorRegistry; localOnly: boolean; onChange: (settings: ColorSettings) => void }) {
  const [undo, setUndo] = useState<ColorSettings | null>(null);
  const [domainText, setDomainText] = useState<[string, string] | null>(null);
  const [error, setError] = useState("");
  const fields = [...new Set(runs.flatMap((r) => [...flattenConfig(r.config).keys()]))].sort();
  const numeric = fields.filter((field) => runs.some((r) => typeof parameterValue(r, field) === "number"));
  function patch(values: Partial<ColorSettings>) { onChange({ ...settings, ...values }); setError(""); setDomainText(null); }
  function domainFor(field: string): [number, number] {
    const values = runs.map((r) => parameterValue(r, field)).filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    const low = values.length ? Math.min(...values) : 0, high = values.length ? Math.max(...values) : 1;
    return low === high ? [low, low + (Math.abs(low) || 1)] : [low, high];
  }
  return <Popover className="colors-menu" label="Colors" trigger={<><PaletteIcon size={15} /><span>Colors</span></>}>
    <div className="menu-heading">Color system</div>
    <div className="settings-form">
      <label>Mode<select className="text-input" value={settings.mode} onChange={(e) => {
        const mode = e.target.value as ColorSettings["mode"];
        const field = mode === "continuous" ? (numeric.includes(settings.field) ? settings.field : numeric[0] ?? "") : fields.includes(settings.field) ? settings.field : fields[0] ?? "";
        patch({ mode, field, ...(mode === "continuous" ? { domain: domainFor(field) } : {}) });
      }}><option value="random">Random · stable identities</option><option value="parameter">By parameter · families</option><option value="continuous">Continuous · Viridis</option></select></label>
      {settings.mode !== "random" && <label>Parameter<select className="text-input" value={settings.field} onChange={(e) => patch({ field: e.target.value, ...(settings.mode === "continuous" ? { domain: domainFor(e.target.value) } : {}) })}>
        {(settings.mode === "continuous" ? numeric : fields).map((field) => <option key={field} value={field}>{field}</option>)}
        {!(settings.mode === "continuous" ? numeric : fields).includes(settings.field) && <option value={settings.field}>{settings.field || "No recorded parameter"}</option>}
      </select></label>}
      {settings.mode === "parameter" && <p className="field-help">Values form color families. Shades may repeat within a family; hover a run to isolate it.</p>}
      {settings.mode === "continuous" && <>
        <div className="inline-fields">{([0, 1] as const).map((i) => <label key={i}>{i === 0 ? "Minimum" : "Maximum"}<input className="text-input" type="number" step="any" value={domainText?.[i] ?? settings.domain[i]} onChange={(e) => { const next: [string, string] = domainText ? [...domainText] : settings.domain.map(String) as [string, string]; next[i] = e.target.value; setDomainText(next); }} /></label>)}</div>
        <div className="inline-fields"><button className="button quiet-button" onClick={() => { const d = domainText?.map(Number) as [number, number] | undefined; if (!d || domainText?.some((s) => !s.trim()) || !d.every(Number.isFinite) || d[0] >= d[1]) setError("Use a finite, increasing range."); else patch({ domain: d }); }}>Apply range</button><button className="button quiet-button" onClick={() => patch({ domain: domainFor(settings.field) })}>Fit current runs</button></div>
        <p className="field-help">Saved range stays fixed as runs arrive. Values outside it use the end colors.</p>
      </>}
      {settings.mode === "random" && <>
        <p className="field-help">Click a run’s swatch to edit its color. Manual colors stay locked.</p>
        <div className="inline-fields"><button className="button quiet-button" disabled={selectedIds.size === 0} onClick={() => { setUndo(settings); onChange(reassignColors(runs, selectedIds, registry, settings)); }}>Reassign selected</button><button className="button quiet-button" disabled={!undo} onClick={() => { if (undo) onChange({ ...settings, assignments: undo.assignments }); setUndo(null); }}>Undo</button></div>
      </>}
      {error && <p className="field-help" role="alert">{error}</p>}
      {localOnly && <p className="field-help" role="status">Color identities could not be saved. Refresh to retry.</p>}
    </div>
  </Popover>;
}
