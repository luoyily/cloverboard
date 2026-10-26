import json

import pytest
from fastapi.testclient import TestClient

from cloverboard.server.app import create_app


def _client(logdir, **kwargs):
    return TestClient(create_app(logdir=logdir, **kwargs), base_url="http://127.0.0.1")


def _save(client, state, saved_at=100):
    return client.put("/api/workspace/state", json={"state": state, "saved_at": saved_at})


def test_state_roundtrip_survives_new_app_and_preserves_empty_selection(tmp_path):
    first = _client(tmp_path)
    response = _save(first, {
        "selected_ids": [], "smoothing": 0, "panel": "table",
        "search": "baseline", "metric_columns": [], "differences_only": False,
        "sort_column": "param:optimizer.lr", "sort_desc": True,
        "refresh_interval": 0, "tooltip_sort": "list", "tooltip_decimals": 5,
        "expanded_charts": ["train/loss", "validation/score"],
    })
    assert response.status_code == 200
    second = _client(tmp_path).get("/api/workspace").json()
    assert second == response.json()
    assert second["state"]["selected_ids"] == []
    assert second["state"]["smoothing"] == 0
    assert second["state"]["metric_columns"] == []
    assert second["state"]["refresh_interval"] == 0
    assert second["state"]["tooltip_sort"] == "list"
    assert second["state"]["tooltip_decimals"] == 5
    assert second["state"]["expanded_charts"] == ["train/loss", "validation/score"]
    assert (tmp_path / ".workspace" / "workspace.sqlite").is_file()


def test_workspace_identity_and_data_are_isolated_by_logdir(tmp_path):
    first, second = _client(tmp_path / "a"), _client(tmp_path / "b")
    _save(first, {"selected_ids": ["r1"]})
    a, b = first.get("/api/workspace").json(), second.get("/api/workspace").json()
    assert a["workspace_id"] != b["workspace_id"]
    assert b["state"] is None
    assert b["views"] == []


def test_older_delayed_state_does_not_replace_newer_edit(tmp_path):
    client = _client(tmp_path)
    _save(client, {"search": "new"}, 200)
    response = _save(client, {"search": "old"}, 100)
    assert response.status_code == 200
    assert response.json()["state"]["search"] == "new"
    assert _client(tmp_path).get("/api/workspace").json()["state"]["search"] == "new"


def test_auto_state_and_named_view_are_independent(tmp_path):
    client = _client(tmp_path)
    created = client.post("/api/workspace/views", json={
        "name": "Learning rates", "state": {"selected_ids": ["r1", "r2"], "smoothing": 0.3},
    })
    assert created.status_code == 201
    view_id = created.json()["view_id"]
    _save(client, {"selected_ids": ["r3"], "smoothing": 0.9, "active_view_id": view_id})
    snapshot = _client(tmp_path).get("/api/workspace").json()
    assert snapshot["state"]["selected_ids"] == ["r3"]
    assert snapshot["views"][0]["state"]["selected_ids"] == ["r1", "r2"]
    assert snapshot["views"][0]["state"]["smoothing"] == 0.3
    assert snapshot["views"][0]["state"]["active_view_id"] is None
    updated = client.put(f"/api/workspace/views/{view_id}", json={
        "name": "Learning rates", "state": {"selected_ids": ["r3"], "active_view_id": view_id},
    })
    assert updated.status_code == 200
    assert updated.json()["state"]["selected_ids"] == ["r3"]
    assert updated.json()["state"]["active_view_id"] is None
    assert len(_client(tmp_path).get("/api/workspace").json()["views"]) == 1


def test_view_names_are_trimmed_and_duplicates_do_not_overwrite(tmp_path):
    client = _client(tmp_path)
    assert client.post("/api/workspace/views", json={"name": "  Baseline  ", "state": {}}).json()["name"] == "Baseline"
    duplicate = client.post("/api/workspace/views", json={"name": "baseline", "state": {"search": "other"}})
    assert duplicate.status_code == 409
    assert len(client.get("/api/workspace").json()["views"]) == 1
    assert client.post("/api/workspace/views", json={"name": "   ", "state": {}}).status_code == 422
    assert client.put("/api/workspace/views/missing", json={"name": "x", "state": {}}).status_code == 404


def test_read_only_serves_saved_state_and_rejects_workspace_mutations(tmp_path):
    writable = _client(tmp_path)
    _save(writable, {"search": "saved"})
    view = writable.post("/api/workspace/views", json={"name": "Baseline", "state": {}}).json()
    before = (tmp_path / ".workspace" / "workspace.sqlite").read_bytes()
    client = _client(tmp_path, read_only=True)
    assert client.get("/api/workspace").json()["writable"] is False
    assert _save(client, {"search": "changed"}).status_code == 403
    assert client.post("/api/workspace/views", json={"name": "Other", "state": {}}).status_code == 403
    assert client.put(f"/api/workspace/views/{view['view_id']}", json={"name": "Other", "state": {}}).status_code == 403
    assert (tmp_path / ".workspace" / "workspace.sqlite").read_bytes() == before


def test_reading_fresh_workspace_does_not_create_database(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/runs").status_code == 200
    snapshot = client.get("/api/workspace").json()
    assert snapshot["state"] is None
    assert snapshot["views"] == []
    assert not (tmp_path / ".workspace").exists()


def test_global_warmup_and_explicit_chart_auto_survive_workspace_and_view_roundtrip(tmp_path):
    client = _client(tmp_path)
    state = {"y_warmup": 1000, "chart_warmup": {"auto": None, "zero": 0, "own": -10}}
    assert _save(client, state).status_code == 200
    view = client.post("/api/workspace/views", json={"name": "Warmup", "state": state})
    assert view.status_code == 201
    restored = _client(tmp_path).get("/api/workspace").json()
    for value in [restored["state"], restored["views"][0]["state"]]:
        assert value["y_warmup"] == 1000
        assert value["chart_warmup"] == state["chart_warmup"]
    for invalid in [{"y_warmup": 2**53}, {"chart_warmup": {"loss": -(2**53)}}]:
        assert _save(client, invalid, 200).status_code == 422


@pytest.mark.parametrize("state", [{"smoothing": 1}, {"smoothing": -1}, {"panel": "other"}, {"version": 2}, {"refresh_interval": -1}, {"refresh_interval": 1}, {"tooltip_sort": "unknown"}])
def test_invalid_state_is_rejected(tmp_path, state):
    client = _client(tmp_path)
    assert _save(client, state).status_code == 422
    assert client.get("/api/workspace").json()["state"] is None
