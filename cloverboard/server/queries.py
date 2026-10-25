"""Typed run filtering shared by server-side analysis tools."""

from cloverboard.server.models import RunQuery
from cloverboard.storage.protocols import RunSummary


def flatten_config(config, prefix=""):
    values = {}
    for key, value in config.items():
        segment = key.replace("\\", "\\\\").replace(".", "\\.")
        path = f"{prefix}.{segment}" if prefix else segment
        if isinstance(value, dict) and value:
            values.update(flatten_config(value, path))
        else:
            values[path] = value
    return values


def matches_query(run: RunSummary, query: RunQuery) -> bool:
    if not query.rules:
        return True
    results = []
    for rule in query.rules:
        if rule.field == "tags":
            selected = rule.value if isinstance(rule.value, list) else []
            membership = [tag in run.tags for tag in selected]
            result = {"has_any": any(membership), "has_all": all(membership), "has_none": not any(membership), "empty": not run.tags, "not_empty": bool(run.tags)}[rule.operator]
            results.append(result)
            continue
        values = {"name": run.name or run.run_id, "run_id": run.run_id}
        values.update({"config:" + key: value for key, value in flatten_config(run.config).items()})
        exists = rule.field in values
        value = values.get(rule.field)
        if rule.operator in ("exists", "missing"):
            result = exists if rule.operator == "exists" else not exists
        elif not exists:
            result = False
        elif rule.operator in ("eq", "neq"):
            equal = type(value) is type(rule.value) and value == rule.value
            if isinstance(value, (int, float)) and not isinstance(value, bool) and isinstance(rule.value, (int, float)) and not isinstance(rule.value, bool):
                equal = value == rule.value
            result = equal if rule.operator == "eq" else not equal
        elif rule.operator == "contains":
            result = isinstance(value, str) and rule.value.lower() in value.lower()
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result = {"gt": value > rule.value, "gte": value >= rule.value, "lt": value < rule.value, "lte": value <= rule.value}[rule.operator]
        else:
            result = False
        results.append(result)
    return all(results) if query.match == "all" else any(results)
