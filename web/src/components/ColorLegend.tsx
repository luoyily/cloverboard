import type { RunSummary } from "../api";
import { displayParameter } from "../comparison";
import { continuousColor } from "../colors";
import { parameterValue, type ColorSettings } from "../features";
import type { ResolvedTheme } from "../theme";

export default function ColorLegend({ runs, settings, colors, theme }: { runs: RunSummary[]; settings: ColorSettings; colors: Map<string, string>; theme: ResolvedTheme }) {
  const missing = theme === "light" ? "#747e8b" : "#a3adba";
  if (settings.mode === "random" && !settings.top_k.enabled) return null;
  const categories = new Map<string, string>();
  if (settings.mode === "parameter") for (const run of runs) {
    const v = parameterValue(run, settings.field), label = v === undefined ? "Missing" : `${typeof v === "string" ? '"' + v + '"' : displayParameter(v)}`;
    if (!categories.has(label)) categories.set(label, colors.get(run.run_id) ?? missing);
  }
  return <div className="color-legend" aria-label="Color legend">
    {settings.mode !== "random" && <span>{settings.field || "Parameter"}</span>}
    {settings.mode === "parameter" && [...categories].map(([label, color]) => <span className="legend-entry" key={label}><i className="swatch" style={{ background: color }} />{label}</span>)}
    {settings.mode === "continuous" && <><span>{settings.domain[0]}</span><span className="continuous-legend" aria-label="Viridis color scale">{Array.from({ length: 48 }, (_, i) => <i key={i} style={{ background: continuousColor(settings.domain[0] + (settings.domain[1] - settings.domain[0]) * i / 47, settings.domain, theme) }} />)}</span><span>{settings.domain[1]}</span><span className="legend-entry"><i className="swatch" style={{ background: missing }} />Missing / non-numeric</span></>}
    {settings.top_k.enabled && <span className="top-k-note">Top {settings.top_k.k} · {settings.top_k.metric} · {settings.top_k.readout} · {settings.top_k.direction === "max" ? "higher" : "lower"}</span>}
  </div>;
}
