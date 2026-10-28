"""HTTP routes backed by local and one-hop storage readers."""

import hashlib
import json
import math
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from cloverboard.server.models import (
    MetricListResponse,
    RunCatalogResponse,
    RunSummaryResponse,
    ScalarPointResponse,
    ScalarSeriesResponse,
    ScalarSummaryResponse,
    ScalarBoundsResponse,
    ScalarCountResponse,
)
from cloverboard.storage.protocols import RunNotFound, ScalarPoint, StorageReader
from cloverboard.server.sources import SourceReader, split_run_id
from cloverboard.metrics import metric_field

router = APIRouter()


def get_reader(request: Request, local_only: bool = False) -> StorageReader | None:
    return getattr(request.app.state, "local_reader" if local_only else "reader", None)


def get_run_reader(reader: StorageReader | None = Depends(get_reader)) -> StorageReader:
    if reader is None:
        raise RunNotFound("Run not found")
    return reader


def get_run_id(run_id: str) -> str:
    split_run_id(run_id)
    return run_id


def _run_rows(runs, writable):
    rows = [
        RunSummaryResponse(
            run_id=run.run_id,
            name=run.name,
            started_at=run.started_at,
            config=run.config,
            source=run.source,
            has_notes=run.has_notes, note_excerpt=run.note_excerpt,
            tags=run.tags, tags_writable=run.tags_writable and writable,
            has_scalars=run.has_scalars, last_recorded_at=run.last_recorded_at,
            **({"source_label": run.source_label, "source_online": run.source_online} if run.source_label is not None else {}),
            **({"time_origin": run.time_origin, "time_origin_kind": run.time_origin_kind} if run.time_origin is not None and math.isfinite(run.time_origin) else {}),
        )
        for run in runs
    ]
    return [row.model_dump(mode="json", exclude_unset=True) for row in rows]


def _etag_response(request, payload):
    etag = '"' + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:32] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(payload, headers={"ETag": etag})


@router.get("/api/runs", tags=["Runs"], response_model=list[RunSummaryResponse], response_model_exclude_unset=True)
def list_runs(
    request: Request,
    reader: StorageReader | None = Depends(get_reader),
) -> Response:
    """List currently available runs, with an ETag for unchanged results.

    local_only selects the one-hop peer transport and local discovery path.
    """
    runs = reader.list_runs() if reader is not None else []
    return _etag_response(request, _run_rows(runs, request.app.state.workspace.writable))


@router.get("/api/run-catalog", tags=["Runs"], response_model=RunCatalogResponse)
def run_catalog(request: Request) -> Response:
    """Return a complete run scan and its source status in the same snapshot.

    An online source's missing IDs can be removed from the current selection;
    failed sources must retain their saved IDs. Both results participate in ETag
    revalidation, including source status changes while the run list is empty.
    """
    reader = request.app.state.reader
    if isinstance(reader, SourceReader):
        runs, sources = reader.run_catalog()
    else:
        runs = reader.list_runs() if reader is not None else []
        sources = [{"source_id": "local", "label": "Local", "kind": "local", "online": True}] if reader is not None else []
    return _etag_response(request, {
        "runs": _run_rows(runs, request.app.state.workspace.writable), "sources": sources,
    })


@router.get("/api/metrics", tags=["Scalars"], response_model=MetricListResponse)
def list_metrics(
    run_id: str = Depends(get_run_id),
    reader: StorageReader = Depends(get_run_reader),
) -> MetricListResponse:
    """List recorded metric paths and their group/name fields; the first path segment is the group."""
    metrics = reader.list_metrics(run_id)
    return MetricListResponse(run_id=run_id, metrics=metrics, fields=[asdict(metric_field(name)) for name in metrics])


def _series_response(run_id: str, metric: str, points: list[ScalarPoint], smoothing: float) -> ScalarSeriesResponse:
    return ScalarSeriesResponse(
        run_id=run_id,
        metric=metric,
        points=[ScalarPointResponse(
            step=point.step,
            value=point.value if math.isfinite(point.value) else None,
            **({"wall_time": point.wall_time} if point.wall_time is not None and math.isfinite(point.wall_time) else {}),
            **({"smoothed": point.smoothed if point.smoothed is not None and math.isfinite(point.smoothed) else None} if smoothing > 0 else {}),
        ) for point in points],
    )


@router.get("/api/scalars-batch", tags=["Scalars"], response_model=list[ScalarSeriesResponse], response_model_exclude_unset=True)
def get_scalars_batch(
    metrics: list[str] = Query(min_length=1, max_length=16),
    run_id: str = Depends(get_run_id),
    max_points: int = Query(800, ge=2, le=10000),
    step_min: int | None = Query(None, ge=-(1 << 63), le=(1 << 63) - 1),
    step_max: int | None = Query(None, ge=-(1 << 63), le=(1 << 63) - 1),
    smoothing: float = Query(0, ge=0, le=0.95, allow_inf_nan=False),
    reader: StorageReader = Depends(get_run_reader),
) -> list[ScalarSeriesResponse]:
    """Read up to 16 metrics from one run with common range/EMA settings.

    Repeat the metrics query parameter. Duplicate names are read once and results
    preserve request order. max_points is approximate because gaps/extrema are retained.
    """
    metrics = list(dict.fromkeys(metrics))
    if len(metrics) * max_points > 32000:
        raise HTTPException(422, "Batch point budget exceeded")
    if step_min is not None and step_max is not None and step_min > step_max:
        raise HTTPException(422, "Step range must be increasing")
    series = reader.get_scalar_series_batch(run_id, metrics, max_points, step_min, step_max, smoothing)
    return [_series_response(run_id, metric, series.get(metric, []), smoothing) for metric in metrics]


@router.get("/api/scalars", tags=["Scalars"], response_model=ScalarSeriesResponse, response_model_exclude_unset=True)
def get_scalars(
    metric: str,
    run_id: str = Depends(get_run_id),
    max_points: int = Query(800, ge=2, le=10000),
    step_min: int | None = Query(None, ge=-(1 << 63), le=(1 << 63) - 1),
    step_max: int | None = Query(None, ge=-(1 << 63), le=(1 << 63) - 1),
    smoothing: float = Query(0, ge=0, le=0.95, allow_inf_nan=False),
    reader: StorageReader = Depends(get_run_reader),
) -> ScalarSeriesResponse:
    """Read samples over an inclusive Step range; max_points is an approximate sampling budget.

    EMA is computed over the raw prefix before filtering the requested range.
    Sampling retains gaps and finite extrema. NaN/Inf values become null.
    """
    if step_min is not None and step_max is not None and step_min > step_max:
        raise HTTPException(422, "Step range must be increasing")
    points = reader.get_scalar_series(run_id, metric, max_points, step_min, step_max, smoothing)
    return _series_response(run_id, metric, points, smoothing)


@router.get("/api/scalar-bounds", tags=["Scalars"], response_model=ScalarBoundsResponse)
def get_scalar_bounds(
    metric: str,
    run_id: str = Depends(get_run_id),
    step_min: int | None = Query(None, ge=-(1 << 63), le=(1 << 63) - 1),
    step_max: int | None = Query(None, ge=-(1 << 63), le=(1 << 63) - 1),
    reader: StorageReader = Depends(get_run_reader),
) -> ScalarBoundsResponse:
    """Return finite raw bounds before sampling, within the inclusive Step range."""
    if step_min is not None and step_max is not None and step_min > step_max:
        raise HTTPException(422, "Step range must be increasing")
    bounds = reader.get_scalar_bounds(run_id, metric, step_min, step_max)
    return ScalarBoundsResponse(minimum=bounds.minimum, maximum=bounds.maximum, positive_minimum=bounds.positive_minimum)


@router.get("/api/scalar-summary", tags=["Scalars"], response_model=ScalarSummaryResponse)
def get_scalar_summary(
    metric: str,
    run_id: str = Depends(get_run_id),
    target: float | None = Query(None, allow_inf_nan=False),
    direction: Literal["gte", "lte"] = "gte",
    reader: StorageReader = Depends(get_run_reader),
) -> ScalarSummaryResponse:
    """Return greatest-Step Raw plus full-history finite min/max and earliest Step meeting a target."""
    summary = reader.get_scalar_summary(run_id, metric, target, direction)
    def point(value):
        if value is None:
            return None
        return ScalarPointResponse(step=value.step, value=value.value if math.isfinite(value.value) else None,
                                   wall_time=value.wall_time if value.wall_time is not None and math.isfinite(value.wall_time) else None)
    return ScalarSummaryResponse(
        latest=point(summary.latest), minimum=summary.minimum, maximum=summary.maximum,
        first_hit=point(summary.first_hit),
    )


@router.get("/api/scalar-count", tags=["Scalars"], response_model=ScalarCountResponse)
def scalar_count(
    metric: str, run_id: str = Depends(get_run_id),
    step_min: int | None = Query(None, ge=-(1 << 63), le=(1 << 63) - 1),
    step_max: int | None = Query(None, ge=-(1 << 63), le=(1 << 63) - 1),
    reader: StorageReader = Depends(get_run_reader),
) -> ScalarCountResponse:
    """Count raw records, including non-finite points, within the inclusive Step range."""
    if step_min is not None and step_max is not None and step_min > step_max:
        raise HTTPException(422, "Step range must be increasing")
    return ScalarCountResponse(count=reader.scalar_count(run_id, metric, step_min, step_max))


@router.get("/api/scalar-at-step", tags=["Scalars"], response_model=ScalarPointResponse | None, response_model_exclude_unset=True)
def scalar_at_step(metric: str, step: int = Query(ge=-(2**53 - 1), le=2**53 - 1), run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> ScalarPointResponse | None:
    """Return the raw record with greatest Step <= step, or null if none. Includes the actual Step."""
    point = reader.scalar_at_step(run_id, metric, step)
    if point is None:
        return None
    return ScalarPointResponse(step=point.step, value=point.value if math.isfinite(point.value) else None,
                               wall_time=point.wall_time if point.wall_time is not None and math.isfinite(point.wall_time) else None)
