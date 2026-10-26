import type { RunSummary, ScalarPoint } from "./api";

export interface ReadoutAnchor { left: number; right: number; top: number; bottom: number }
export type ReadoutPlacement = "vertical" | "horizontal";
export function placeReadout(anchor: ReadoutAnchor, popup: { width: number; height: number }, viewport: { width: number; height: number }, preference: ReadoutPlacement = "vertical") {
  const gap = 12, maxWidth = Math.max(0, viewport.width - gap * 2), maxHeight = Math.max(0, viewport.height - gap * 2);
  const width = Math.min(popup.width, maxWidth), height = Math.min(popup.height, maxHeight);
  const top = Math.max(gap, Math.min(anchor.top, viewport.height - height - gap));
  if (viewport.width <= 600) return { left: gap, top: viewport.height - height - gap, maxWidth, maxHeight, docked: true };
  const left = Math.max(gap, Math.min(anchor.left, viewport.width - width - gap));
  const below = viewport.height - anchor.bottom - gap * 2, above = anchor.top - gap * 2;
  const vertical = [
    below >= height ? { left, top: anchor.bottom + gap, maxWidth, maxHeight: below, docked: false } : null,
    above >= height ? { left, top: anchor.top - gap - height, maxWidth, maxHeight: above, docked: false } : null,
  ];
  const horizontal = [
    anchor.right + gap + width <= viewport.width - gap ? { left: anchor.right + gap, top, maxWidth, maxHeight, docked: false } : null,
    anchor.left - gap - width >= gap ? { left: anchor.left - gap - width, top, maxWidth, maxHeight, docked: false } : null,
  ];
  const fitting = (preference === "vertical" ? [...vertical, ...horizontal] : [...horizontal, ...vertical]).find((candidate) => candidate !== null);
  if (fitting) return fitting;
  if (Math.max(above, below) >= 80) return below >= above
    ? { left, top: anchor.bottom + gap, maxWidth, maxHeight: below, docked: false }
    : { left, top: gap, maxWidth, maxHeight: above, docked: false };
  return { left: gap, top: viewport.height - height - gap, maxWidth, maxHeight, docked: true };
}

export function elapsedTime(point: ScalarPoint | undefined, run: RunSummary): number | null {
  if (point?.wall_time == null || run.time_origin == null || !Number.isFinite(point.wall_time) || !Number.isFinite(run.time_origin)) return null;
  return point.wall_time - run.time_origin;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  const total = Math.floor(Math.abs(seconds));
  const days = Math.floor(total / 86400), hours = Math.floor(total % 86400 / 3600), minutes = Math.floor(total % 3600 / 60);
  const sign = seconds < 0 && total > 0 ? "−" : "";
  return sign + [days ? `${days}d` : "", days || hours ? `${hours}h` : "", days || hours || minutes ? `${minutes}m` : "", `${total % 60}s`].filter(Boolean).join(" ");
}

export function formatWallTime(seconds: number | null | undefined, full = false): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const date = new Date(seconds * 1000);
  if (!Number.isFinite(date.getTime())) return "—";
  return full ? date.toLocaleString("en-GB", { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZoneName: "short" }) : date.toLocaleTimeString("en-GB", { hour12: false });
}

export function formatTableTime(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const date = new Date(seconds * 1000);
  if (!Number.isFinite(date.getTime())) return "—";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${String(date.getFullYear()).padStart(4, "0")}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function timeOriginLabel(run: RunSummary): string {
  return run.time_origin_kind === "step_zero" ? "Since Step 0" : run.time_origin_kind === "first_sample" ? "Since first sample" : "Time origin unavailable";
}
