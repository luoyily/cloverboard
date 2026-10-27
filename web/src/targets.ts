import type { Target } from "./features";

export interface NamedTarget extends Target {
  id: string;
  name: string;
  chart_label: string;
}

export interface MetricTargets {
  items: NamedTarget[];
  active_id: string | null;
}

export function activeTarget(group: MetricTargets | undefined): NamedTarget | undefined {
  return group?.items.find((item) => item.id === group.active_id);
}

export function saveTarget(group: MetricTargets | undefined, target: NamedTarget): MetricTargets {
  const items = group?.items ?? [];
  const exists = items.some((item) => item.id === target.id);
  return {
    items: exists ? items.map((item) => item.id === target.id ? target : item) : [...items, target],
    active_id: items.length ? group!.active_id : target.id,
  };
}

export function removeTarget(group: MetricTargets, id: string): MetricTargets {
  return { items: group.items.filter((item) => item.id !== id), active_id: group.active_id === id ? null : group.active_id };
}

export function normalizeTargets(groups: Record<string, MetricTargets>): Record<string, MetricTargets> {
  const normalized: Record<string, MetricTargets> = {};
  for (const [metric, group] of Object.entries(groups)) {
    if (!group || !Array.isArray(group.items)) continue;
    const ids = new Set<string>();
    const items = group.items.filter((item) => {
      if (!item || typeof item.id !== "string" || !item.id || ids.has(item.id) ||
          typeof item.name !== "string" || !item.name.trim() || typeof item.chart_label !== "string" ||
          typeof item.value !== "number" || !Number.isFinite(item.value) || !["gte", "lte"].includes(item.direction)) return false;
      ids.add(item.id);
      return true;
    });
    if (items.length) normalized[metric] = { items, active_id: items.some((item) => item.id === group.active_id) ? group.active_id : null };
  }
  return normalized;
}
