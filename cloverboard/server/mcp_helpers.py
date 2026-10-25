"""Small data and output-budget helpers shared by MCP tools."""

import json
import math
import sqlite3

from cloverboard.server.mcp_models import Failure, Point, RunBrief
from cloverboard.server.sources import SourceRequestError, SourceUnavailable, split_run_id
from cloverboard.storage.protocols import RunNotFound, RunSummary, ScalarPoint, StorageReader

READ_ERRORS = (OSError, sqlite3.Error, SourceUnavailable, SourceRequestError)


def failure(error: Exception) -> Failure:
    if isinstance(error, RunNotFound) or isinstance(error, SourceRequestError) and error.status_code == 404:
        return Failure(code="run_not_found", message="Run not found")
    if isinstance(error, SourceUnavailable):
        return Failure(code="source_unavailable", message=str(error)[:300])
    if isinstance(error, SourceRequestError):
        return Failure(code="source_query_failed", message=str(error)[:300])
    return Failure(code="storage_unavailable", message="Experiment storage is unavailable")


def absent_run(reader: StorageReader | None, run_id: str) -> Failure:
    source, _ = split_run_id(run_id)
    if source != "local" and hasattr(reader, "status"):
        status = next((item for item in reader.status() if item["source_id"] == source), None)
        if status is None or not status["online"]:
            return Failure(code="source_unavailable", message="Run source is offline or not configured")
    return failure(RunNotFound())


def finite(value):
    return value if value is not None and math.isfinite(value) else None


def point(value: ScalarPoint | None, include_wall_time: bool = False) -> Point | None:
    if value is None:
        return None
    return Point(step=value.step, value=finite(value.value),
                 **({"wall_time": finite(value.wall_time)} if include_wall_time else {}))


def brief(run: RunSummary) -> RunBrief:
    return RunBrief(
        run_id=run.run_id, name=run.name[:200] if run.name else run.name,
        source=run.source, source_online=run.source_online,
        started_at=run.started_at, last_recorded_at=finite(run.last_recorded_at),
        has_scalars=run.has_scalars, tags=run.tags[:5],
        **({"tags_total": len(run.tags)} if len(run.tags) > 5 else {}),
        **({"name_truncated": True} if run.name and len(run.name) > 200 else {}),
    )


class ValueBudget:
    """Omit entire values that do not fit; preserve their names for focused retries."""

    def __init__(self, remaining: int = 12000):
        self.remaining = remaining

    def take(self, value, key: str = "") -> bool:
        size = len(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)) + len(key) + 4
        if size > self.remaining:
            return False
        self.remaining -= size
        return True

    def fields(self, values: dict) -> tuple[dict, list[str]]:
        result, omitted = {}, []
        for key, value in values.items():
            if self.take(value, key):
                result[key] = value
            else:
                omitted.append(key)
        return result, omitted


def selected_fields(values: dict, fields: list[str] | None) -> dict:
    if fields is None:
        return values
    return {key: value for key, value in values.items() if any(key == prefix or key.startswith(prefix + ".") for prefix in fields)}


def same_value(left, right) -> bool:
    """Keep false/0, missing/null and nested value types distinct."""
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(same_value(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_value(left[key], right[key]) for key in left)
    return left == right
