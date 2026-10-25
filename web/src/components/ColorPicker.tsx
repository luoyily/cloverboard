import { useEffect, useRef, useState, type PointerEvent } from "react";
import { hexToHsv, hsvToHex, type Hsv } from "../colors";

export default function ColorPicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [hsv, setHsv] = useState(() => hexToHsv(/^#[0-9a-f]{6}$/i.test(value) ? value : "#3276b8"));
  const emitted = useRef(value);
  useEffect(() => { if (value !== emitted.current && /^#[0-9a-f]{6}$/i.test(value)) setHsv(hexToHsv(value)); }, [value]);
  function update(next: Hsv) { setHsv(next); emitted.current = hsvToHex(next); onChange(emitted.current); }
  function pick(event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    update({ ...hsv, s: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), v: Math.max(0, Math.min(1, 1 - (event.clientY - bounds.top) / bounds.height)) });
  }
  return <div className="custom-picker">
    <div className="color-square" role="group" aria-label="Saturation and brightness picker" style={{ backgroundColor: hsvToHex({ h: hsv.h, s: 1, v: 1 }) }}
      onPointerDown={(event) => { if (event.button !== 0) return; event.currentTarget.setPointerCapture(event.pointerId); pick(event); }}
      onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) pick(event); }}
      onPointerUp={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}>
      <i style={{ left: `${hsv.s * 100}%`, top: `${(1 - hsv.v) * 100}%` }} />
    </div>
    <label>Hue<input className="color-hue" type="range" min={0} max={359} value={Math.round(hsv.h)} onChange={(event) => update({ ...hsv, h: Number(event.target.value) })} /></label>
    <details className="advanced-color-controls"><summary>Advanced values</summary><div className="inline-fields">{(["s", "v"] as const).map((channel) => <label key={channel}>{channel === "s" ? "Saturation %" : "Brightness %"}<input className="text-input" type="number" min={0} max={100} step={1} value={Math.round(hsv[channel] * 100)} onChange={(event) => { if (event.target.value !== "") update({ ...hsv, [channel]: Math.max(0, Math.min(100, Number(event.target.value))) / 100 }); }} /></label>)}</div></details>
  </div>;
}
