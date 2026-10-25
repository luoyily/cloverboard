import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

interface PopoverProps {
  label: string;
  trigger: ReactNode;
  children: ReactNode;
  className?: string;
}

export default function Popover({ label, trigger, children, className = "" }: PopoverProps) {
  const ref = useRef<HTMLDetailsElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const place = useCallback(() => {
    const details = ref.current, menu = menuRef.current;
    if (!details?.open || !menu) return;
    const trigger = details.querySelector("summary")!.getBoundingClientRect(), bounds = menu.getBoundingClientRect();
    const leftAligned = details.classList.contains("views-menu");
    const left = Math.max(12, Math.min(window.innerWidth - bounds.width - 12, leftAligned ? trigger.left : trigger.right - bounds.width));
    const below = trigger.bottom + 6;
    const top = below + bounds.height <= window.innerHeight - 12 ? below : Math.max(12, trigger.top - bounds.height - 6);
    setPosition((previous) => previous?.left === left && previous.top === top ? previous : { left, top });
  }, []);
  useEffect(() => {
    function close(event: PointerEvent) {
      if (event.target instanceof Node && !ref.current?.contains(event.target)) {
        ref.current?.removeAttribute("open");
      }
    }
    document.addEventListener("pointerdown", close);
    window.addEventListener("resize", place);
    document.addEventListener("scroll", place, true);
    const observer = new ResizeObserver(place);
    if (menuRef.current) observer.observe(menuRef.current);
    return () => { document.removeEventListener("pointerdown", close); window.removeEventListener("resize", place); document.removeEventListener("scroll", place, true); observer.disconnect(); };
  }, []);

  return (
    <details ref={ref} className={`popover-menu ${className}`} onToggle={place} onKeyDown={(event) => {
      if (event.key === "Escape") {
        event.currentTarget.removeAttribute("open");
        event.currentTarget.querySelector("summary")?.focus();
      }
    }}>
      <summary role="button" className="button quiet-button popover-trigger" aria-label={label}>{trigger}</summary>
      <div ref={menuRef} className="menu-popover" style={position ? { position: "fixed", left: position.left, top: position.top, right: "auto", zIndex: 40 } : undefined}>{children}</div>
    </details>
  );
}
