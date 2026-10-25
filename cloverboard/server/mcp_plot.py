"""Prepare bounded MCP plot data without depending on Web viewing state."""

import io
import math
from typing import Literal

from mcp.server.fastmcp import Image
from mcp.types import CallToolResult, TextContent

from cloverboard.server import mcp_models as output
from cloverboard.server.mcp_helpers import READ_ERRORS, absent_run, failure
from cloverboard.server.metric_plot import PlotSeries, render_metric
from cloverboard.storage.protocols import StorageReader

# The Web's light-theme base colors; no workspace slots or theme state are read.
PALETTE = ("#3276b8", "#c05a24", "#267f6c", "#9955af",
           "#b74666", "#847113", "#4d5eaf", "#a54938")


def plot_metric(reader: StorageReader | None, run_ids: list[str], metric: str,
                range_mode: Literal["full", "last_50", "last_20"],
                step_min: int | None, step_max: int | None,
                smoothing: float, max_points: int) -> CallToolResult:
    """Read each curve on its own Step grid; return one PNG and compact metadata.

    Tail presets share the union of recorded Step extents. EMA is calculated by
    storage over full history before cropping/sampling, exactly as in the Web.
    Read failures are per run; a wholly failed request returns no image.
    """
    if range_mode != "full" and (step_min is not None or step_max is not None):
        raise ValueError("Choose either range_mode or explicit step_min/step_max")
    if step_min is not None and step_max is not None and step_min > step_max:
        raise ValueError("Step range must be increasing")
    errors = []
    def record_error(run_id, error):
        errors.append(output.RunError(run_id=run_id, error=error))
    try:
        runs = {run.run_id: run for run in reader.list_runs()} if reader else {}
    except READ_ERRORS as error:
        runs = {}
        for run_id in run_ids:
            record_error(run_id, failure(error))
    active = []
    if not errors:
        for run_id in run_ids:
            if run_id not in runs:
                record_error(run_id, absent_run(reader, run_id))
            elif not runs[run_id].source_online:
                record_error(run_id, output.Failure(code="source_unavailable", message="Run source is offline"))
            else:
                active.append(run_id)

    if range_mode != "full":
        extents, available = [], []
        for run_id in active:
            try:
                # Endpoints are retained by storage sampling. No new HTTP or
                # storage extent API is needed for these optional presets.
                points = reader.get_scalar_series(run_id, metric, 3)
                if points:
                    extents.append((points[0].step, points[-1].step))
                available.append(run_id)
            except READ_ERRORS as error:
                record_error(run_id, failure(error))
        active = available
        if extents:
            lo, hi = min(v[0] for v in extents), max(v[1] for v in extents)
            fraction = .5 if range_mode == "last_50" else .2
            step_min = min(hi - 1, math.ceil(lo + (hi - lo) * (1 - fraction))) if hi > lo else lo
            step_max = hi

    # Stable for the same comparison set even when callers reorder run_ids.
    colors = {run_id: PALETTE[index] for index, run_id in enumerate(sorted(run_ids))}
    curves, counts, plotted_ids = [], [], []
    for run_id in active:
        try:
            count = reader.scalar_count(run_id, metric, step_min, step_max)
            points = reader.get_scalar_series(run_id, metric, max_points, step_min, step_max, smoothing)
        except READ_ERRORS as error:
            record_error(run_id, failure(error))
            continue
        # Forced extrema can add a few points; excessive gap markers cannot be
        # dropped safely. Ask for a narrower range instead of hiding a suffix.
        if len(points) > max_points + 4:
            record_error(run_id, output.Failure(code="point_budget_exceeded",
                message="Too many gap markers; narrow the Step range or increase max_points"))
            continue
        label = (runs[run_id].name or run_id)[:200]
        values = [(point.step, point.smoothed if smoothing > 0 else point.value) for point in points]
        curves.append(PlotSeries(label, values, colors[run_id]))
        counts.append(count)
        plotted_ids.append(run_id)

    if not curves:
        data = output.MetricPlot(metric=metric, range_mode=range_mode, step_min=step_min, step_max=step_max,
            smoothing=smoothing, width=1000, height=480, empty=True, x_range=None, y_range=None, curves=[], errors=errors)
        return CallToolResult(content=[TextContent(type="text", text=data.model_dump_json())],
                              structuredContent=data.model_dump(mode="json"), isError=True)
    title = f"EMA {smoothing:g} | {metric}" if smoothing else metric
    result = render_metric(curves, title=title)
    data = output.MetricPlot(metric=metric, range_mode=range_mode, step_min=step_min, step_max=step_max,
        smoothing=smoothing, width=result.image.width, height=result.image.height,
        empty=result.x_range is None, x_range=result.x_range, y_range=result.y_range,
        curves=[output.PlotCurve(run_id=run_id, label=curve.label, legend=facts.legend, color=curve.color,
            raw_count=count, plotted_points=facts.plotted_points, gaps=facts.gaps,
            downsampled=count > len(curve.points),
            **({"label_truncated": True} if len(runs[run_id].name or run_id) > 200 else {}))
            for run_id, curve, count, facts in zip(plotted_ids, curves, counts, result.series)],
        **({"errors": errors} if errors else {}))
    buffer = io.BytesIO()
    result.image.save(buffer, format="PNG")
    return CallToolResult(content=[TextContent(type="text", text=data.model_dump_json()),
        Image(data=buffer.getvalue(), format="png").to_image_content()], structuredContent=data.model_dump(mode="json"))
