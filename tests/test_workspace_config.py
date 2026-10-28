import pytest
from fastapi.testclient import TestClient

from cloverboard.server.app import create_app


def client_for(path, **kwargs):
    return TestClient(create_app(logdir=path, **kwargs), base_url="http://127.0.0.1")


def configuration():
    colors = {"runs": {"run:a": {"color": "#abcdef", "fixed": True}, "run:b": {"color": "#3276b8", "fixed": False}}}
    return {
        "version": 1, "saved_at": 10,
        "state": {"selected_ids": ["a"], "colors": colors, "tooltip_decimals": 6, "y_scale": "log", "refresh_interval": 30},
        "views": [{"view_id": "a" * 32, "name": "Imported", "state": {"colors": colors, "table_decimals": 2}}],
        "palettes": [{"id": "custom:" + "b" * 32, "name": "Ocean", "colors": ["#abcdef", "#3276b8"]}],
    }


def test_configuration_import_commits_current_state_and_independent_libraries(tmp_path):
    client = client_for(tmp_path)
    payload = configuration()
    response = client.put("/api/workspace/config", json=payload)
    assert response.status_code == 200
    restarted = client_for(tmp_path)
    snapshot = restarted.get("/api/workspace").json()
    for key, value in payload["state"].items():
        if key == "colors":
            assert snapshot["state"][key]["runs"] == value["runs"]
        else:
            assert snapshot["state"][key] == value
    assert snapshot["views"][0]["state"]["colors"]["runs"] == payload["state"]["colors"]["runs"]
    assert snapshot["views"][0]["state"]["table_decimals"] == 2
    assert restarted.get("/api/workspace/palettes").json() == payload["palettes"]
    # A View restore changes watching settings without replacing the library.
    assert restarted.put("/api/workspace/state", json={"state": snapshot["views"][0]["state"], "saved_at": 20}).status_code == 200
    assert restarted.get("/api/workspace/palettes").json() == payload["palettes"]


@pytest.mark.parametrize("invalid", [
    {"color": "red", "fixed": True}, {"color": "#abcdef", "fixed": "false"},
    {"color": "#abcdef"}, {"color": "#abcdef", "fixed": False, "slot": 1},
])
def test_invalid_color_entries_are_rejected_without_partial_import(tmp_path, invalid):
    client = client_for(tmp_path)
    valid = configuration()
    assert client.put("/api/workspace/config", json=valid).status_code == 200
    before = client.get("/api/workspace").json()
    changed = configuration()
    changed["state"]["colors"]["runs"]["run:a"] = invalid
    assert client.put("/api/workspace/config", json=changed).status_code == 422
    assert client.put("/api/workspace/state", json={"state": changed["state"], "saved_at": 11}).status_code == 422
    assert client.get("/api/workspace").json() == before
    assert client.get("/api/workspace/palettes").json() == valid["palettes"]


def test_readonly_and_share_configuration_imports_do_not_write(tmp_path):
    payload = configuration()
    assert client_for(tmp_path).put("/api/workspace/config", json=payload).status_code == 200
    path = tmp_path / ".workspace" / "workspace.sqlite"
    before = path.read_bytes()
    for options in ({"read_only": True}, {"share": True}):
        client = client_for(tmp_path, **options)
        headers = {"Authorization": f"Bearer {client.app.state.access_token}"} if options.get("share") else {}
        assert client.put("/api/workspace/config", json=payload, headers=headers).status_code == 403
        assert client.get("/api/workspace", headers=headers).json()["state"]["colors"]["runs"] == payload["state"]["colors"]["runs"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("temporary", [
    {"chart_zoom": {"loss": {"x_min": 10, "x_max": 20, "y_min": 0, "y_max": 1}}},
    {"hover_step": 10}, {"pinned": True}, {"theme": "dark"}, {"sidebar_width": 350},
])
def test_temporary_interactions_and_browser_preferences_are_not_workspace_settings(tmp_path, temporary):
    client = client_for(tmp_path)
    assert client.put("/api/workspace/state", json={"state": temporary, "saved_at": 1}).status_code == 422
    assert client.post("/api/workspace/views", json={"name": "Temporary", "state": temporary}).status_code == 422
    payload = configuration()
    payload["state"].update(temporary)
    assert client.put("/api/workspace/config", json=payload).status_code == 422
    assert client.get("/api/workspace").json()["state"] is None


def test_toolbar_watching_settings_restore_together_in_state_and_named_view(tmp_path):
    client = client_for(tmp_path)
    state = {
        "selected_ids": ["baseline", "trial"], "baseline_id": "baseline", "search": "trial", "search_regex": True,
        "colors": {"runs": {"trial": {"color": "#abcdef", "fixed": True}}, "top_k": {"enabled": True, "metric": "score", "direction": "max", "readout": "best", "k": 2}},
        "targets": {"score": {"items": [{"id": "goal", "name": "Goal", "chart_label": "G", "value": .9, "direction": "gte"}], "active_id": "goal"}},
        "metric_filter": "loss", "chart_density": "compact", "show_chart_legend": False, "smoothing": .2,
        "y_scale": "log", "y_warmup": 100, "chart_scales": {"score": "linear"}, "chart_warmup": {"score": None},
        "tooltip_decimals": 6, "tooltip_sort": "list", "tooltip_placement": "horizontal", "tooltip_time": "wall", "tooltip_delta": "hidden",
        "metric_columns": ["score"], "hidden_params": ["seed"], "differences_only": False, "sort_column": "metric:score", "sort_desc": True,
        "table_decimals": 2, "table_time": "hidden", "table_show_steps": False, "table_delta": "target", "show_tags_column": True,
        "panel": "images", "media_type": "audio", "audio_name": "sample", "audio_step": 0,
        "image_timeline_samples": 50, "images_per_step": 4, "refresh_interval": 30,
    }
    assert client.put("/api/workspace/state", json={"state": state, "saved_at": 1}).status_code == 200
    assert client.post("/api/workspace/views", json={"name": "Watching settings", "state": state}).status_code == 201
    snapshot = client_for(tmp_path).get("/api/workspace").json()
    for restored in (snapshot["state"], snapshot["views"][0]["state"]):
        for key, expected in state.items():
            assert restored[key] == ({**restored[key], **expected} if key == "colors" else expected)
