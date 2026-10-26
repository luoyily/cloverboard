import json
import math

import pytest
from fastapi.testclient import TestClient

from cloverboard.server.app import create_app
from cloverboard.storage.protocols import RunMeta, ScalarRecord
from cloverboard.storage.sqlite_backend import SqliteStorageReader, SqliteStorageWriter


def _history(tmp_path):
    directory = tmp_path / "r1"
    directory.mkdir()
    writer = SqliteStorageWriter(directory / "run.sqlite")
    writer.init_run(RunMeta("r1", "Baseline", 1, {"optimizer": {"lr": 0.001}}))
    values = [1.0] * 2400
    values[1243] = 0.19
    values[1260] = 0.1
    values[1900] = float("inf")
    values[-1] = float("nan")
    writer.append_scalars([ScalarRecord("loss", i, i, value) for i, value in enumerate(values)])
    writer.flush_and_close()
    return SqliteStorageReader(tmp_path)


def test_zoom_reads_raw_range_before_downsampling_and_keeps_ema_prefix(tmp_path):
    reader = _history(tmp_path)
    sparse = reader.get_scalar_series("r1", "loss", 2)
    assert all(p.step != 1244 for p in sparse)
    assert all(p.step != 1243 for p in sparse)
    zoom = reader.get_scalar_series("r1", "loss", 800, 1240, 1250, 0.6)
    assert [p.step for p in zoom] == list(range(1240, 1251))
    assert zoom[3].value == 0.19
    later = reader.get_scalar_series("r1", "loss", 800, 1244, 1250, 0.6)
    assert later[0].smoothed == pytest.approx(zoom[4].smoothed)
    assert later[0].smoothed != later[0].value
    gap = reader.get_scalar_series("r1", "loss", 800, 1900, 1902, 0.6)
    assert math.isnan(gap[0].smoothed)
    assert gap[1].smoothed == 1


def test_summary_uses_full_raw_history_and_preserves_invalid_latest(tmp_path):
    reader = _history(tmp_path)
    client = TestClient(create_app(reader, logdir=tmp_path), base_url="http://127.0.0.1")
    summary = client.get("/api/scalar-summary", params={"run_id": "r1", "metric": "loss", "target": 0.2, "direction": "lte"}).json()
    assert summary["latest"]["step"] == 2399
    assert summary["latest"]["value"] is None
    assert summary["first_hit"]["step"] == 1243
    assert summary["minimum"] == 0.1
    assert summary["maximum"] == 1
    points = client.get("/api/scalars", params={"run_id": "r1", "metric": "loss", "step_min": 1244, "step_max": 1250, "smoothing": 0.6}).json()["points"]
    assert points[0]["value"] == 1
    assert points[0]["smoothed"] < 1


def test_timestamps_keep_actual_samples_and_global_step_zero_origin(tmp_path):
    directory = tmp_path / "timed"
    directory.mkdir()
    writer = SqliteStorageWriter(directory / "run.sqlite")
    writer.init_run(RunMeta("timed", "Timed", 50, {}))
    writer.append_scalars([
        ScalarRecord("other", 4, 90, 1),
        ScalarRecord("train", 0, 100, 1),
        ScalarRecord("train", 1, 101, .8),
        ScalarRecord("validation", 0, 99, .9),
        ScalarRecord("validation", 10, 260, .7),
    ])
    writer.flush_and_close()
    client = TestClient(create_app(SqliteStorageReader(tmp_path), logdir=tmp_path), base_url="http://127.0.0.1")
    run = client.get("/api/runs").json()[0]
    assert run["time_origin"] == 100
    assert run["time_origin_kind"] == "step_zero"
    points = client.get("/api/scalars", params={"run_id": "timed", "metric": "validation", "step_min": 10, "smoothing": .8}).json()["points"]
    assert points[0]["wall_time"] == 260
    assert points[0]["smoothed"] != points[0]["value"]
    assert client.get("/api/runs").json()[0]["time_origin"] == 100


def test_missing_step_zero_uses_first_recorded_timestamp_not_start_or_minimum(tmp_path):
    directory = tmp_path / "resumed"
    directory.mkdir()
    writer = SqliteStorageWriter(directory / "run.sqlite")
    writer.init_run(RunMeta("resumed", "Resumed", 10, {}))
    writer.append_scalars([ScalarRecord("loss", 20, 300, 1), ScalarRecord("loss", 10, 200, 2)])
    writer.flush_and_close()
    run = SqliteStorageReader(tmp_path).list_runs()[0]
    assert (run.time_origin, run.time_origin_kind) == (300, "first_sample")


def test_warmup_bounds_use_all_raw_data_and_do_not_change_summaries(tmp_path):
    reader = _history(tmp_path)
    assert reader.get_scalar_bounds("r1", "loss", 1240, 1250).minimum == .19
    assert reader.get_scalar_bounds("r1", "loss", 1240, 1250).maximum == 1
    assert reader.get_scalar_bounds("r1", "loss", 1900, 1900).minimum is None
    assert reader.get_scalar_bounds("r1", "loss", 3000).maximum is None
    client = TestClient(create_app(reader, logdir=tmp_path, read_only=True), base_url="http://127.0.0.1")
    query = {"run_id": "r1", "metric": "loss"}
    assert client.get("/api/scalar-bounds", params={**query, "step_min": 1240, "step_max": 1250}).json() == {"minimum": .19, "maximum": 1, "positive_minimum": .19}
    assert client.get("/api/scalar-bounds", params={**query, "step_min": 20, "step_max": 10}).status_code == 422
    assert client.get("/api/scalar-bounds", params={**query, "step_min": 1 << 80}).status_code == 422
    assert client.get("/api/scalar-summary", params=query).json()["minimum"] == .1
    assert not (tmp_path / ".workspace").exists()


def test_range_and_summary_validation_and_readonly(tmp_path):
    client = TestClient(create_app(_history(tmp_path), logdir=tmp_path, read_only=True), base_url="http://127.0.0.1")
    query = {"run_id": "r1", "metric": "loss"}
    assert client.get("/api/scalars", params={**query, "step_min": 20, "step_max": 10}).status_code == 422
    assert client.get("/api/scalars", params={**query, "step_min": 1 << 80}).status_code == 422
    assert client.get("/api/scalars", params={**query, "smoothing": "nan"}).status_code == 422
    assert client.get("/api/scalar-summary", params={**query, "target": "inf"}).status_code == 422
    assert client.get("/api/scalars", params={**query, "step_min": 1240, "step_max": 1250}).status_code == 200
    assert not (tmp_path / ".workspace").exists()


def test_shared_exploration_reads_require_auth_and_cannot_persist_colors(tmp_path):
    app = create_app(_history(tmp_path), logdir=tmp_path, share=True)
    client = TestClient(app, base_url="http://127.0.0.1")
    query = {"run_id": "r1", "metric": "loss", "step_min": 1240, "step_max": 1250}
    assert client.get("/api/scalars", params=query).status_code == 401
    assert client.get("/api/scalar-bounds", params=query).status_code == 401
    headers = {"Authorization": f"Bearer {app.state.access_token}"}
    assert client.get("/api/scalars", params=query, headers=headers).status_code == 200
    assert client.get("/api/scalar-bounds", params=query, headers=headers).status_code == 200
    assert client.get("/api/scalar-summary", params={"run_id": "r1", "metric": "loss", "target": .2, "direction": "lte"}, headers=headers).json()["first_hit"]["step"] == 1243
    assert client.get("/api/workspace/colors", headers=headers).status_code == 200
    assert client.put("/api/workspace/colors", json={"keys": ["r1"]}, headers=headers).status_code == 403
    assert not (tmp_path / ".workspace").exists()


def test_colors_append_only_restart_copy_and_readonly(tmp_path):
    client = TestClient(create_app(logdir=tmp_path), base_url="http://127.0.0.1")
    assert client.get("/api/workspace/colors").status_code == 200
    assert not (tmp_path / ".workspace").exists()
    first = client.put("/api/workspace/colors", json={"keys": ['run:["local","r2"]', 'run:["peer","r2"]']}).json()
    next_colors = client.put("/api/workspace/colors", json={"keys": ['run:["local","older"]']}).json()
    assert all(next_colors["slots"][key] == slot for key, slot in first["slots"].items())
    assert len(set(next_colors["slots"].values())) == 3
    path = tmp_path / ".workspace" / "workspace.sqlite"
    before = path.read_bytes()
    readonly = TestClient(create_app(logdir=tmp_path, read_only=True), base_url="http://127.0.0.1")
    assert readonly.get("/api/workspace/colors").json() == next_colors
    assert readonly.put("/api/workspace/colors", json={"keys": ["new"]}).status_code == 403
    assert path.read_bytes() == before
    copied = tmp_path / "copied" / ".workspace"
    copied.mkdir(parents=True)
    (copied / "workspace.sqlite").write_bytes(before)
    assert TestClient(create_app(logdir=copied.parent), base_url="http://127.0.0.1").get("/api/workspace/colors").json() == next_colors


def test_empty_color_registry_is_available_without_writes(tmp_path):
    client = TestClient(create_app(logdir=tmp_path), base_url="http://127.0.0.1")
    assert client.get("/api/workspace/colors").json()["slots"] == {}
    assert client.get("/api/runs").status_code == 200
    assert not (tmp_path / ".workspace").exists()


def test_view_list_and_comparison_preferences_roundtrip(tmp_path):
    client = TestClient(create_app(logdir=tmp_path), base_url="http://127.0.0.1")
    state = {
        "selected_ids": ["r1"], "baseline_id": "r1", "search": "SRx[24]", "search_regex": True, "run_sort": "name_desc",
        "targets": {"loss": {"value": 0.2, "direction": "lte"}},
        "chart_zoom": {"loss": {"x_min": 10, "x_max": 40, "y_min": 0, "y_max": 1}},
        "tooltip_time": "elapsed", "tooltip_delta": "both", "chart_warmup": {"loss": 10},
        "colors": {"mode": "continuous", "field": "optimizer.lr", "domain": [0.0001, 0.1], "overrides": {"r1": "#3276b8"}},
    }
    saved = client.post("/api/workspace/views", json={"name": "Low LR", "state": state})
    assert saved.status_code == 201
    restored = TestClient(create_app(logdir=tmp_path), base_url="http://127.0.0.1").get("/api/workspace").json()["views"][0]["state"]
    for key in ("selected_ids", "search", "search_regex", "run_sort"):
        assert restored[key] == state[key]
    assert restored["colors"]["domain"] == [0.0001, 0.1]
    assert restored["chart_zoom"] == state["chart_zoom"]
    assert restored["chart_warmup"] == {"loss": 10}
    assert restored["tooltip_time"] == "elapsed"
    assert restored["tooltip_delta"] == "both"
    defaults = client.post("/api/workspace/views", json={"name": "Defaults", "state": {"selected_ids": []}}).json()["state"]
    assert defaults["search_regex"] is False
    assert defaults["targets"] == {}
    assert defaults["colors"]["mode"] == "random"
    assert defaults["colors"]["palette"] == {"kind": "builtin", "id": "default"}
    assert defaults["tooltip_time"] == "hidden"
    assert defaults["tooltip_delta"] == "both"
    assert defaults["chart_warmup"] == {}


def test_curve_palette_survives_workspace_views_and_restart(tmp_path):
    client = TestClient(create_app(logdir=tmp_path), base_url="http://127.0.0.1")
    registry = client.put("/api/workspace/colors", json={"keys": ['run:["local","r1"]']}).json()
    colors = {"palette": {"kind": "builtin", "id": "bright"}, "assignments": {"r1": 17}, "overrides": {"r2": "#EA68A2"}}
    state = {"colors": colors}
    assert client.put("/api/workspace/state", json={"state": state, "saved_at": 10}).status_code == 200
    assert client.post("/api/workspace/views", json={"name": "Bright curves", "state": state}).status_code == 201
    restored = TestClient(create_app(logdir=tmp_path), base_url="http://127.0.0.1")
    snapshot = restored.get("/api/workspace").json()
    for saved in (snapshot["state"], snapshot["views"][0]["state"]):
        for key, value in colors.items():
            assert saved["colors"][key] == value
    assert restored.get("/api/workspace/colors").json() == registry


@pytest.mark.parametrize("mode", ["baseline", "target", "both", "hidden"])
def test_explicit_delta_preferences_survive_workspace_and_view_restore(tmp_path, mode):
    client = TestClient(create_app(logdir=tmp_path), base_url="http://127.0.0.1")
    state = {
        "tooltip_delta": mode, "refresh_interval": 30, "table_delta": mode,
        "table_show_steps": False,
        "baseline_id": "r1", "targets": {"loss": {"value": .2, "direction": "lte"}},
    }
    assert client.put("/api/workspace/state", json={"state": state, "saved_at": 10}).status_code == 200
    assert client.post("/api/workspace/views", json={"name": "Delta", "state": state}).status_code == 201
    restored = TestClient(create_app(logdir=tmp_path), base_url="http://127.0.0.1").get("/api/workspace").json()
    expected = mode
    assert restored["state"]["tooltip_delta"] == expected
    assert restored["views"][0]["state"]["tooltip_delta"] == expected
    assert restored["state"]["refresh_interval"] == 30
    for settings in (restored["state"], restored["views"][0]["state"]):
        for key in ("table_delta", "table_show_steps", "baseline_id", "targets"):
            assert settings[key] == state[key]


@pytest.mark.parametrize("patch", [
    {"run_sort": "wrong"},
    {"targets": {"loss": {"value": "nan"}}},
    {"chart_zoom": {"loss": {"x_min": 30, "x_max": 10}}},
    {"colors": {"domain": [1, 0]}}, {"colors": {"overrides": {"r1": "red"}}},
    {"colors": {"palette": "unknown"}},
    {"tooltip_time": "wrong"}, {"tooltip_delta": "wrong"}, {"chart_warmup": {"loss": 1 << 60}},
])
def test_invalid_exploration_state_is_rejected(tmp_path, patch):
    response = TestClient(create_app(logdir=tmp_path), base_url="http://127.0.0.1").put("/api/workspace/state", json={"state": patch, "saved_at": 10})
    assert response.status_code == 422
