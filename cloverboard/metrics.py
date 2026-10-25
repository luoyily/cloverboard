"""Metric paths: the first segment is the display group, the rest is its name."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MetricField:
    metric: str
    group: str
    name: str


def metric_field(name: str, group: str | None = None) -> MetricField:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Metric names must be nonempty text")
    if group is not None:
        if not isinstance(group, str) or not group.strip() or "/" in group:
            raise ValueError("Group must be a single nonempty path segment")
        name = f"{group.strip()}/{name.strip()}"
    parts = name.split("/")
    if any(not part.strip() for part in parts):
        raise ValueError("Metric path segments must be nonempty")
    if len(parts) >= 2:
        namespace, *rest = (part.strip() for part in parts)
        field = "/".join(rest)
        return MetricField(f"{namespace}/{field}", namespace, field)
    field = name.strip()
    return MetricField(field, "General", field)
