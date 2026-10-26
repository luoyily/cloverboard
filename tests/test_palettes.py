import sqlite3

import pytest
from fastapi.testclient import TestClient

from cloverboard.server.app import create_app


COLORS = ["#7ECEF4", "#EA68A2", "#FF9A52", "#A2DB86"]
PAYLOAD = {"name": "Spring", "colors": COLORS}


def client_for(path, **kwargs):
    return TestClient(create_app(logdir=path, **kwargs), base_url="http://127.0.0.1")


def test_palette_crud_restart_isolation_and_slot_preservation(tmp_path):
    client = client_for(tmp_path)
    assert client.get("/api/workspace/palettes").json() == []
    assert not (tmp_path / ".workspace").exists()
    registry = client.put("/api/workspace/colors", json={"keys": ["run:a"]}).json()
    response = client.post("/api/workspace/palettes", json={**PAYLOAD, "name": " Spring "})
    assert response.status_code == 201
    palette = response.json()
    assert palette == {"id": palette["id"], "name": "Spring", "colors": [color.lower() for color in COLORS]}
    assert palette["id"].startswith("custom:")
    assert len(palette["id"]) == 39
    endpoint = f"/api/workspace/palettes/{palette['id']}"
    allocated = client.put("/api/workspace/colors", json={"keys": ["run:b"]}).json()
    assert allocated["seed"] == registry["seed"]
    assert allocated["slots"]["run:a"] == registry["slots"]["run:a"]
    assert allocated["palettes"] == [palette]
    changed = client.put(endpoint, json={"name": "Ocean", "colors": list(reversed(COLORS))})
    assert changed.status_code == 200
    changed = changed.json()
    assert changed["id"] == palette["id"]
    assert changed["colors"] == list(reversed(palette["colors"]))
    restarted = client_for(tmp_path)
    assert restarted.get("/api/workspace/palettes").json() == [changed]
    after = restarted.get("/api/workspace/colors").json()
    assert after["slots"] == allocated["slots"]
    assert after["seed"] == registry["seed"]
    assert client_for(tmp_path / "other").get("/api/workspace/palettes").json() == []
    assert restarted.delete(endpoint).json() == changed
    assert client_for(tmp_path).get("/api/workspace/palettes").json() == []
    assert restarted.delete(endpoint).status_code == 404
    assert restarted.put(endpoint, json=PAYLOAD).status_code == 404
    assert restarted.get("/api/workspace/colors").json()["slots"] == allocated["slots"]


def test_palette_snapshots_in_views_and_current_state_survive_library_changes(tmp_path):
    client = client_for(tmp_path)
    palette = client.post("/api/workspace/palettes", json=PAYLOAD).json()
    selection = {"kind": "custom", **palette}
    state = {"colors": {"palette": selection}}
    assert client.put("/api/workspace/state", json={"state": state, "saved_at": 10}).status_code == 200
    assert client.post("/api/workspace/views", json={"name": "Original colors", "state": state}).status_code == 201
    endpoint = f"/api/workspace/palettes/{palette['id']}"
    assert client.put(endpoint, json={"name": "Changed", "colors": ["#123456", "#abcdef"]}).status_code == 200
    assert client.delete(endpoint).status_code == 200
    restarted = client_for(tmp_path)
    snapshot = restarted.get("/api/workspace").json()
    assert snapshot["state"]["colors"]["palette"] == selection
    assert snapshot["views"][0]["state"]["colors"]["palette"] == selection
    # Restoring a View must not replace the independently saved palette library.
    newer = restarted.post("/api/workspace/palettes", json={**PAYLOAD, "name": "Newer"}).json()
    assert restarted.put("/api/workspace/state", json={"state": snapshot["views"][0]["state"], "saved_at": 20}).status_code == 200
    assert restarted.get("/api/workspace/palettes").json() == [newer]


@pytest.mark.parametrize("patch", [
    {"name": ""}, {"name": "   "}, {"name": "x" * 61},
    {"colors": []}, {"colors": ["#abcdef"]},
    {"colors": [f"#{i:06x}" for i in range(25)]},
    {"colors": ["#abcdef", "#ABCDEF"]},
    {"colors": ["#123", "#abcdef"]}, {"colors": ["123456", "#abcdef"]},
    {"colors": ["#xxxxxx", "#abcdef"]}, {"colors": [None, "#abcdef"]},
    {"id": "bright"}, {"unknown": True},
])
def test_invalid_palette_input_rejected_for_create_and_update(tmp_path, patch):
    client = client_for(tmp_path)
    palette = client.post("/api/workspace/palettes", json=PAYLOAD).json()
    payload = {**PAYLOAD, **patch}
    assert client.post("/api/workspace/palettes", json=payload).status_code == 422
    assert client.put(f"/api/workspace/palettes/{palette['id']}", json=payload).status_code == 422
    assert client.get("/api/workspace/palettes").json() == [palette]


def test_library_name_conflicts_limits_and_builtin_protection(tmp_path):
    client = client_for(tmp_path)
    first = client.post("/api/workspace/palettes", json=PAYLOAD).json()
    assert client.post("/api/workspace/palettes", json={**PAYLOAD, "name": " spring "}).status_code == 409
    second = client.post("/api/workspace/palettes", json={**PAYLOAD, "name": "Second"}).json()
    assert client.put(f"/api/workspace/palettes/{second['id']}", json={**PAYLOAD, "name": "SPRING"}).status_code == 409
    assert client.put(f"/api/workspace/palettes/{first['id']}", json={**PAYLOAD, "name": "SPRING"}).status_code == 200
    for i in range(30):
        assert client.post("/api/workspace/palettes", json={**PAYLOAD, "name": f"Palette {i}"}).status_code == 201
    assert client.post("/api/workspace/palettes", json={**PAYLOAD, "name": "Overflow"}).status_code == 409
    assert client.put(f"/api/workspace/palettes/{first['id']}", json=PAYLOAD).status_code == 200
    for builtin in ("default", "bright"):
        assert client.put(f"/api/workspace/palettes/{builtin}", json=PAYLOAD).status_code == 404
        assert client.delete(f"/api/workspace/palettes/{builtin}").status_code == 404


def test_readonly_and_shared_palettes_can_be_read_but_never_written(tmp_path):
    palette = client_for(tmp_path).post("/api/workspace/palettes", json=PAYLOAD).json()
    endpoint = f"/api/workspace/palettes/{palette['id']}"
    database = tmp_path / ".workspace" / "workspace.sqlite"
    before = database.read_bytes()
    for mode in ({"read_only": True}, {"share": True}):
        client = client_for(tmp_path, **mode)
        headers = {}
        if mode.get("share"):
            assert client.get("/api/workspace/palettes").status_code == 401
            assert client.post("/api/workspace/palettes", json=PAYLOAD).status_code == 401
            headers = {"Authorization": f"Bearer {client.app.state.access_token}"}
        assert client.get("/api/workspace/palettes", headers=headers).json() == [palette]
        assert client.post("/api/workspace/palettes", json={**PAYLOAD, "name": "New"}, headers=headers).status_code == 403
        assert client.put(endpoint, json=PAYLOAD, headers=headers).status_code == 403
        assert client.delete(endpoint, headers=headers).status_code == 403
    assert database.read_bytes() == before


@pytest.mark.parametrize("selection", [
    "bright", {"kind": "builtin", "id": "unknown"},
    {"kind": "custom", "id": "bright", **PAYLOAD},
    {"kind": "custom", "id": "custom:" + "a" * 32, "name": "Test", "colors": ["red", "blue"]},
])
def test_palette_selection_uses_strict_discriminated_schema(tmp_path, selection):
    client = client_for(tmp_path)
    state = {"colors": {"palette": selection}}
    assert client.put("/api/workspace/state", json={"state": state, "saved_at": 10}).status_code == 422
    assert client.post("/api/workspace/views", json={"name": "Invalid", "state": state}).status_code == 422


def test_corrupt_registry_returns_503_without_resetting_data(tmp_path):
    client = client_for(tmp_path)
    client.post("/api/workspace/palettes", json=PAYLOAD)
    database = tmp_path / ".workspace" / "workspace.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE color_registry SET payload='invalid JSON'")
    assert client.get("/api/workspace/palettes").status_code == 503
    assert client.post("/api/workspace/palettes", json=PAYLOAD).status_code == 503
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT payload FROM color_registry").fetchone()[0] == "invalid JSON"
