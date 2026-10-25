import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { placeReadout } from "../readout";

export default function FloatingReadout({ anchor, pinned, onDismiss, children }: { anchor: HTMLElement | null; pinned: boolean; onDismiss: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null), dismiss = useRef(onDismiss);
  dismiss.current = onDismiss;
  const [placement, setPlacement] = useState<ReturnType<typeof placeReadout> | null>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!anchor || !element) return;
    let frame = 0;
    function position() {
      if (!anchor || !element) return;
      const next = placeReadout(anchor.getBoundingClientRect(), element.getBoundingClientRect(), { width: window.innerWidth, height: window.innerHeight });
      setPlacement((old) => JSON.stringify(old) === JSON.stringify(next) ? old : next);
    }
    function schedule() { cancelAnimationFrame(frame); frame = requestAnimationFrame(position); }
    function outside(event: PointerEvent) {
      if (pinned && event.target instanceof Node && !anchor?.contains(event.target) && !element?.contains(event.target)) dismiss.current();
    }
    const observer = new ResizeObserver(schedule);
    observer.observe(anchor); observer.observe(element);
    window.addEventListener("resize", schedule);
    window.addEventListener("scroll", schedule, true);
    document.addEventListener("pointerdown", outside);
    position();
    return () => { cancelAnimationFrame(frame); observer.disconnect(); window.removeEventListener("resize", schedule); window.removeEventListener("scroll", schedule, true); document.removeEventListener("pointerdown", outside); };
  }, [anchor, pinned]);
  return createPortal(<div ref={ref} className={`chart-tooltip${pinned ? " tooltip-pinned" : ""}${placement?.docked ? " tooltip-docked" : ""}`} role="region" aria-label="Chart samples"
    style={placement ? { left: placement.left, top: placement.top, maxWidth: placement.maxWidth, maxHeight: Math.min(240, placement.maxHeight) } : { visibility: "hidden" }}
    onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); dismiss.current(); anchor?.querySelector("svg")?.focus(); } }}>{children}</div>, document.body);
}
