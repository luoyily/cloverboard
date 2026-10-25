"""Compact MCP outputs, exact statistics and bounded progressive reads."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from cloverboard.server.app import create_app
from cloverboard.server.sources import SourceUnavailable
from cloverboard.storage.protocols import RunMeta, RunSummary, ScalarPoint, ScalarRecord
from cloverboard.storage.sqlite_backend import SqliteStorageReader, SqliteStorageWriter
from tests.fakes import FakeStorageReader


def rpc(client, method, params=None):
    return client.post("/mcp", headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).json()["result"]


def call(client, name, arguments=None):
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})
    assert not result.get("isError"), result
    return result["structuredContent"]


@pytest.fixture
def client():
    runs = [RunSummary(f"r{i:02}", f"Model {i:02}", i,
                       config={"optimizer": {"lr": i / 1000}, "large": "x" * 20000},
                       tags=[f"tag{j}" for j in range(8)]) for i in range(15)]
    reader = FakeStorageReader(runs, {("r00", "loss"): [ScalarPoint(0, 1, wall_time=100)],
                                      ("r01", "loss"): [ScalarPoint(0, .5)], ("r01", "metrics/psnr"): [ScalarPoint(0, 30)]})
    reader.save_run_note("r00", "long note " * 200, 0)
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as value:
        yield value


def test_discovery_defaults_and_paging_are_small_and_typed(client):
    tools = rpc(client, "tools/list")["tools"]
    assert all(tool["outputSchema"].get("properties") for tool in tools if tool["name"] not in {"plot_metric", "get_images"})
    first = call(client, "list_runs")
    assert len(first["runs"]) == 10 and first["total"] == 15 and first["next_offset"] == 10
    assert first["runs"][0]["run_id"] == "r14"
    assert first["runs"][0]["tags_total"] == 8 and len(first["runs"][0]["tags"]) == 5
    assert "config" not in first["runs"][0] and "sources" not in first
    second = call(client, "list_runs", {"offset": first["next_offset"]})
    assert second["next_offset"] is None and len(second["runs"]) == 5
    assert call(client, "list_runs", {"name": "r00"})["runs"][0]["name"] == "Model 00"
    assert call(client, "list_runs", {"sort": "name_asc", "limit": 1})["runs"][0]["run_id"] == "r00"


def test_run_details_require_explicit_sections_and_paginate_notes(client):
    default = call(client, "get_runs", {"run_ids": ["r00", "missing"]})["runs"]
    assert default[0]["config_keys"] == ["large", "optimizer"]
    assert "config" not in default[0] and "notes" not in default[0] and "environment" not in default[0]
    assert default[1]["error"]["code"] == "run_not_found"
    selected = call(client, "get_runs", {"run_ids": ["r00"], "config_fields": ["optimizer"]})["runs"][0]
    assert selected["config"] == {"optimizer.lr": 0} and selected["config_format"] == "flat"
    expanded = call(client, "get_runs", {"run_ids": ["r00"], "include": ["config", "notes", "tags"]})["runs"][0]
    assert "large" not in expanded["config"] and expanded["config_omitted_fields"] == ["large"]
    assert len(expanded["tags"]) == 8 and "tags_total" not in expanded
    assert len(expanded["notes"]["text"]) == 1000 and expanded["notes"]["next_offset"] == 1000
    continuation = call(client, "get_runs", {"run_ids": ["r00"], "include": ["notes"], "notes_offset": 1000})["runs"][0]["notes"]
    assert continuation["next_offset"] is None and continuation["total_chars"] == 2000


def test_metric_catalog_merges_runs_and_reports_partial_failure(client):
    result = call(client, "list_metrics", {"run_ids": ["r00", "r01", "missing"], "limit": 1})
    assert result["metrics"] == [{"metric": "loss", "run_ids": ["r00", "r01"]}]
    assert result["next_offset"] == 1 and result["total"] == 2
    assert result["errors"][0]["run_id"] == "missing"
    filtered = call(client, "list_metrics", {"run_ids": ["r00", "r01"], "name": "PSNR"})
    assert filtered["metrics"] == [{"metric": "metrics/psnr", "run_ids": ["r01"]}]


def test_config_differences_preserve_missing_null_types_and_literal_dots():
    runs = [RunSummary("a", "A", 1, {"x": None, "flag": False, "model": {"width": 8}, "model.width": 99}),
            RunSummary("b", "B", 2, {"flag": 0, "model": {"width": 16}, "model.width": 99})]
    with TestClient(create_app(FakeStorageReader(runs)), base_url="http://127.0.0.1") as client:
        result = call(client, "compare_configs", {"run_ids": ["a", "b", "missing"]})
    rows = {row["field"]: row for row in result["rows"]}
    assert set(rows) == {"flag", "model.width", "x"}
    assert rows["x"]["values"] == [None, None, None] and rows["x"]["missing_indices"] == [1, 2]
    assert rows["flag"]["values"][:2] == [False, 0]
    assert result["errors"][0]["run_id"] == "missing"


def test_compare_defaults_omit_unrequested_fields_and_keep_good_rows(client):
    result = call(client, "compare_metrics", {"run_ids": ["r00", "missing"], "metrics": ["loss"]})
    assert result["rows"][0] == {"run_id": "r00", "metric": "loss", "point": {"step": 0, "value": 1}}
    assert result["rows"][1]["error"]["code"] == "run_not_found"
    assert "baseline_id" not in result and "statistics_scope" not in result
    page = call(client, "compare_metrics", {"run_ids": ["r00", "r01"], "metrics": ["loss"], "limit": 1, "offset": 1})
    assert page["rows"][0]["run_id"] == "r01" and page["next_offset"] is None


def test_opt_in_statistics_are_exact_and_independent_of_selected_step(tmp_path):
    path = tmp_path / "r"
    path.mkdir()
    writer = SqliteStorageWriter(path / "run.sqlite")
    writer.init_run(RunMeta("r", "R", 0))
    values = [1.] * 2400
    values[137], values[997], values[-1] = .001, 10, float("nan")
    writer.append_scalars([ScalarRecord("loss", step, step, value) for step, value in enumerate(values)])
    writer.flush_and_close()
    with TestClient(create_app(SqliteStorageReader(tmp_path)), base_url="http://127.0.0.1") as client:
        result = call(client, "compare_metrics", {"run_ids": ["r"], "metrics": ["loss"], "step": 5,
            "include_stats": ["extrema", "first_hit"], "targets": {"loss": {"value": .01, "direction": "lte"}}})
        row = result["rows"][0]
        assert row["point"] == {"step": 5, "value": 1}
        assert row["minimum"] == {"step": 137, "value": .001}
        assert row["maximum"] == {"step": 997, "value": 10}
        assert row["first_hit"]["step"] == 137 and result["statistics_scope"] == "full_raw_history"
        series = call(client, "get_series", {"run_id": "r", "metric": "loss"})
        assert series["returned_count"] <= 200 and not series["truncated"]
        assert any(p["value"] is None for p in series["points"])
        assert all("wall_time" not in p for p in series["points"])
        timed = call(client, "get_series", {"run_id": "r", "metric": "loss", "step_min": 5, "step_max": 5, "include_wall_time": True})
        assert timed["points"] == [{"step": 5, "value": 1, "wall_time": 5}]


def test_excessive_gaps_are_paginated_without_stitching_across_breaks():
    points = [ScalarPoint(i, float("nan") if i % 2 else 1.) for i in range(40)]
    reader = FakeStorageReader(series={("r", "loss"): points})
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        first = call(client, "get_series", {"run_id": "r", "metric": "loss", "max_points": 12})
        assert first["truncated"] and first["raw_count"] == 40 and len(first["points"]) == 12
        assert [p["value"] for p in first["points"]] == [1, None] * 6
        following = call(client, "get_series", {"run_id": "r", "metric": "loss", "max_points": 12, "step_min": first["next_step"]})
        assert following["points"][0]["step"] > first["points"][-1]["step"]


def test_storage_source_and_baseline_errors_do_not_abort_good_rows():
    class FailingReader(FakeStorageReader):
        def get_scalar_summary(self, run_id, *args):
            if run_id == "offline":
                raise SourceUnavailable("Source remote is unavailable")
            if run_id == "broken":
                raise sqlite3.DatabaseError("private internal database detail")
            return super().get_scalar_summary(run_id, *args)
    reader = FailingReader(series={("good", "loss"): [ScalarPoint(0, 1)]})
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        result = call(client, "compare_metrics", {"run_ids": ["good", "broken"], "metrics": ["loss"], "baseline_id": "offline"})
    assert result["rows"][0]["point"]["value"] == 1 and result["rows"][0]["delta_baseline"] is None
    assert result["rows"][1]["error"] == {"code": "storage_unavailable", "message": "Experiment storage is unavailable"}
    assert result["baseline_errors"][0]["error"]["code"] == "source_unavailable"


def test_optional_large_results_are_rejected_with_a_retry_hint():
    runs = [RunSummary(f"r{i}", "name" * 50, i, tags=["t" * 80] * 5) for i in range(50)]
    with TestClient(create_app(FakeStorageReader(runs)), base_url="http://127.0.0.1") as client:
        assert len(call(client, "list_runs")["runs"]) == 10
        result = rpc(client, "tools/call", {"name": "list_runs", "arguments": {"limit": 50}})
        assert result["isError"] and "reduce limit" in result["content"][0]["text"]


def test_offline_cached_config_is_not_treated_as_a_live_comparison():
    runs = [RunSummary("live", "Live", 1, {"lr": 1}), RunSummary("cached", "Cached", 2, {"lr": 2}, source_online=False)]
    with TestClient(create_app(FakeStorageReader(runs)), base_url="http://127.0.0.1") as client:
        result = call(client, "compare_configs", {"run_ids": ["live", "cached"]})
    assert result["rows"] == [] and result["errors"][0]["error"]["code"] == "source_unavailable"
