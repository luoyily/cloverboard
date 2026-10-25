"""Pillow rendering and MCP image transport over real, independently sampled curves."""

import base64
import io
import math
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from cloverboard.server.app import create_app
from cloverboard.server import mcp_plot
from cloverboard.server.metric_plot import PlotSeries, format_ticks, nice_ticks, render_metric
from cloverboard.storage.protocols import RunMeta, RunSummary, ScalarPoint, ScalarRecord
from cloverboard.storage.sqlite_backend import SqliteStorageReader, SqliteStorageWriter
from tests.fakes import FakeStorageReader
from tests.test_mcp_analysis import rpc


def plot(client, **arguments):
    result = rpc(client, "tools/call", {"name": "plot_metric", "arguments": arguments})
    assert not result.get("isError"), result
    return result


@pytest.mark.parametrize("lo,hi", [(0, 1000000), (-.003, .002), (32.401, 32.405),
                                     (1e-18, 2e-18), (1e-300, 2e-300), (42, 42), (0, 1)])
def test_ticks_cover_data_without_colliding_labels(lo, hi):
    ticks = nice_ticks(lo, hi)
    labels = format_ticks(ticks)
    assert 4 <= len(ticks) <= 6
    assert ticks[0] <= lo and ticks[-1] >= hi
    assert len(set(labels)) == len(ticks)
    assert all(math.isfinite(value) for value in ticks)


def test_renderer_leaves_a_real_gap_and_keeps_all_eight_legend_entries():
    curves = [PlotSeries("中文实验名称 " * 15, [(0, 1), (1, 1), (2, None), (9, 1), (10, 1)], "#3276b8")]
    curves += [PlotSeries(f"empty {i}", [], mcp_plot.PALETTE[i]) for i in range(1, 8)]
    result = render_metric(curves, title="metrics/BSDS100/psnr")
    assert result.image.size == (1000, 480) and result.image.mode == "RGB"
    assert len(result.series) == 8 and result.series[-1].legend.startswith("8.")
    assert result.series[0].gaps == 1 and result.series[0].plotted_points == 4
    assert result.series[0].legend.endswith("…")
    # The middle of the drawing area has grid/background, never the curve's blue.
    for _, (red, green, blue) in result.image.crop((380, 100, 430, 400)).getcolors(maxcolors=15000):
        assert not (blue > red + 60 and blue > green + 30)
    empty = render_metric([PlotSeries("empty", [(0, float("nan"))], "#3276b8")])
    assert empty.x_range is None and empty.series[0].plotted_points == 0


def test_mcp_returns_inline_png_compact_metadata_and_independent_steps():
    reader = FakeStorageReader([RunSummary("a", "Same name", 0), RunSummary("b", "Same name", 1)],
        {("a", "loss"): [ScalarPoint(0, 1), ScalarPoint(2, .5), ScalarPoint(10, .2)],
         ("b", "loss"): [ScalarPoint(3, .8), ScalarPoint(8, .4)]})
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        result = plot(client, run_ids=["b", "a"], metric="loss")
        reordered = plot(client, run_ids=["a", "b"], metric="loss")["structuredContent"]
    assert [item["type"] for item in result["content"]] == ["text", "image"]
    image = result["content"][1]
    assert image["mimeType"] == "image/png"
    png = Image.open(io.BytesIO(base64.b64decode(image["data"])))
    png.load()
    assert png.size == (1000, 480)
    data = result["structuredContent"]
    assert data["x_range"] == [0, 10] and data["y_range"] == [.2, 1]
    assert [c["plotted_points"] for c in data["curves"]] == [2, 3]
    assert "points" not in data["curves"][0] and "errors" not in data
    assert len(result["content"][0]["text"]) < 2500
    assert {c["run_id"]: c["color"] for c in data["curves"]} == {c["run_id"]: c["color"] for c in reordered["curves"]}


def test_tail_presets_use_common_union_span_and_do_not_extend_short_runs():
    reader = FakeStorageReader([RunSummary("a", "A", 0), RunSummary("b", "B", 1)],
        {("a", "psnr"): [ScalarPoint(100, 30), ScalarPoint(600, 32), ScalarPoint(900, 32.01), ScalarPoint(1100, 32.02)],
         ("b", "psnr"): [ScalarPoint(100, 29), ScalarPoint(500, 31)]})
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        half = plot(client, run_ids=["a", "b"], metric="psnr", range_mode="last_50")["structuredContent"]
        tail = plot(client, run_ids=["a", "b"], metric="psnr", range_mode="last_20")["structuredContent"]
        exact = plot(client, run_ids=["a"], metric="psnr", step_min=900, step_max=900)["structuredContent"]
    assert (half["step_min"], half["step_max"]) == (600, 1100)
    assert [c["raw_count"] for c in half["curves"]] == [3, 0]
    assert tail["step_min"] == 900 and tail["y_range"] == [32.01, 32.02]
    assert exact["x_range"] == [900, 900] and exact["curves"][0]["plotted_points"] == 1


def test_sqlite_smooths_full_history_before_range_and_sampling(tmp_path, monkeypatch):
    folder = tmp_path / "r"
    folder.mkdir()
    writer = SqliteStorageWriter(folder / "run.sqlite")
    writer.init_run(RunMeta("r", "R", 0))
    writer.append_scalars([ScalarRecord("loss", step, step, 0. if step < 1980 else 1.) for step in range(2000)])
    writer.append_scalars([ScalarRecord("gaps", step, step, float("nan") if step == 1500 else step) for step in range(2000)])
    writer.flush_and_close()
    captured = []
    original = mcp_plot.render_metric
    def capture(curves, **kwargs):
        captured.append(curves)
        return original(curves, **kwargs)
    monkeypatch.setattr(mcp_plot, "render_metric", capture)
    with TestClient(create_app(SqliteStorageReader(tmp_path)), base_url="http://127.0.0.1") as client:
        data = plot(client, run_ids=["r"], metric="loss", step_min=1990, smoothing=.5, max_points=5)["structuredContent"]
        gap_data = plot(client, run_ids=["r"], metric="gaps", max_points=20)["structuredContent"]
    assert captured[0][0].points[0] == pytest.approx((1990, 1 - .5 ** 11))
    assert captured[0][0].points[-1] == pytest.approx((1999, 1 - .5 ** 20))
    assert data["curves"][0]["downsampled"] and data["curves"][0]["raw_count"] == 10
    assert any(math.isnan(value) for _, value in captured[1][0].points)
    assert gap_data["curves"][0]["gaps"] == 1


def test_partial_errors_empty_metrics_and_excessive_gaps_are_explicit():
    class Broken(FakeStorageReader):
        def get_scalar_series(self, run_id, *args, **kwargs):
            if run_id == "broken":
                raise sqlite3.DatabaseError("private DB path")
            return super().get_scalar_series(run_id, *args, **kwargs)
    reader = Broken([RunSummary(rid, rid, 0) for rid in ("good", "broken", "gaps")],
        {("good", "loss"): [ScalarPoint(0, 1)],
         ("gaps", "loss"): [ScalarPoint(i, float("nan")) for i in range(30)]})
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        data = plot(client, run_ids=["good", "missing", "broken", "gaps"], metric="loss", max_points=5)["structuredContent"]
        assert len(data["curves"]) == 1 and len(data["errors"]) == 3
        assert "private" not in str(data)
        assert data["errors"][-1]["error"]["code"] == "point_budget_exceeded"
        empty = plot(client, run_ids=["good"], metric="unrecorded")["structuredContent"]
        assert empty["empty"] and empty["curves"][0]["raw_count"] == 0
        failed = rpc(client, "tools/call", {"name": "plot_metric", "arguments": {"run_ids": ["missing"], "metric": "loss"}})
        assert failed["isError"] and not any(c["type"] == "image" for c in failed["content"])


@pytest.mark.parametrize("extra", [{"range_mode": "last_50", "step_min": 0},
    {"step_min": 5, "step_max": 2}, {"smoothing": 1}, {"max_points": 2001}, {"run_ids": [str(i) for i in range(9)]}])
def test_plot_rejects_ambiguous_or_unbounded_requests(extra):
    with TestClient(create_app(FakeStorageReader()), base_url="http://127.0.0.1") as client:
        result = rpc(client, "tools/call", {"name": "plot_metric", "arguments": {"run_ids": ["a"], "metric": "loss", **extra}})
    assert result["isError"]
