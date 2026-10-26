"""HTTP contracts against real run storage and one-hop source failures."""

import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from cloverboard.server.app import create_app
from cloverboard.server.sources import Source, SourceReader, qualify
from cloverboard.storage.protocols import RunMeta, ScalarRecord
from cloverboard.storage.sqlite_backend import SqliteStorageReader, SqliteStorageWriter


@pytest.fixture
def client(tmp_path):
    for name in ("empty", "recorded"):
        folder = tmp_path / name
        folder.mkdir()
        writer = SqliteStorageWriter(folder / "run.sqlite")
        writer.init_run(RunMeta(name, name, 100, config={"lr": .01}))
        if name == "recorded":
            writer.append_scalars([ScalarRecord("loss", 1, 200, 3), ScalarRecord("loss", 5, 150, 1)])
        writer.flush_and_close()
    with TestClient(create_app(SqliteStorageReader(tmp_path)), base_url="http://127.0.0.1") as value:
        yield value


@pytest.mark.parametrize("route,query,expected", [
    ("metrics", {}, {"run_id": "empty", "metrics": [], "fields": []}),
    ("scalars", {"metric": "loss"}, {"run_id": "empty", "metric": "loss", "points": []}),
    ("scalars-batch", {"metrics": ["loss"]}, [{"run_id": "empty", "metric": "loss", "points": []}]),
    ("scalar-summary", {"metric": "loss"}, {"latest": None, "minimum": None, "maximum": None, "first_hit": None}),
    ("scalar-bounds", {"metric": "loss"}, {"minimum": None, "maximum": None, "positive_minimum": None}),
    ("scalar-count", {"metric": "loss"}, {"count": 0}),
    ("scalar-at-step", {"metric": "loss", "step": 3}, None),
    ("run-details", {}, {"config": {"lr": .01}, "env": {}, "snapshot": None}),
    ("run-notes", {}, {"body": "", "updated_at": None, "revision": 0, "writable": True}),
    ("image-names", {}, []),
    ("audio-names", {}, []),
    ("text-names", {}, []),
    ("text-timeline", {"name": "output"}, {"steps": [], "total_steps": 0}),
    ("text", {"name": "output"}, None),
    ("image-timeline", {"name": "prediction"}, {"steps": [], "total_steps": 0}),
    ("audio-timeline", {"name": "speech"}, {"steps": [], "total_steps": 0}),
    ("images", {"name": "prediction"}, {"images": [], "step": None, "total": 0, "next_offset": None}),
    ("audio", {"name": "speech"}, {"audio": [], "step": None, "total": 0, "next_offset": None}),
])
def test_absent_run_differs_from_existing_run_without_records(client, route, query, expected):
    missing = client.get(f"/api/{route}", params={"run_id": "missing", **query})
    assert missing.status_code == 404 and missing.json() == {"detail": "Run not found"}
    empty = client.get(f"/api/{route}", params={"run_id": "empty", **query})
    assert empty.status_code == 200 and empty.json() == expected


def test_point_selection_and_summary_use_steps_not_recording_time(client):
    query = {"run_id": "recorded", "metric": "loss"}
    summary = client.get("/api/scalar-summary", params={**query, "target": 2, "direction": "lte"}).json()
    assert summary["latest"] == {"step": 5, "value": 1, "smoothed": None, "wall_time": 150}
    assert summary["first_hit"]["step"] == 5
    at_step = client.get("/api/scalar-at-step", params={**query, "step": 4}).json()
    assert at_step == {"step": 1, "value": 3, "wall_time": 200}
    assert client.get("/api/scalar-count", params={**query, "step_min": 1, "step_max": 1}).json() == {"count": 1}
    assert client.get("/api/scalar-count", params={**query, "step_min": 2**80}).status_code == 422


@pytest.mark.parametrize("broken_schema", [False, True])
def test_storage_failures_are_not_empty_results(tmp_path, broken_schema):
    folder = tmp_path / "broken"
    folder.mkdir()
    database = folder / "run.sqlite"
    if broken_schema:
        sqlite3.connect(database).close()
    else:
        database.write_bytes(b"not a SQLite database")
    client = TestClient(create_app(SqliteStorageReader(tmp_path)), base_url="http://127.0.0.1")
    for route, query in [("runs", {}), ("metrics", {}), ("scalars", {"metric": "loss"}), ("run-details", {})]:
        response = client.get(f"/api/{route}", params={"run_id": "broken", **query})
        assert response.status_code == 503
        assert response.json() == {"detail": "Experiment storage is unavailable"}


@pytest.mark.parametrize("status,detail", [
    (404, "Run not found"),
    (422, [{"loc": ["query", "metric"], "msg": "Invalid metric", "type": "value_error"}]),
    (503, "Peer database is unavailable"),
])
def test_peer_query_errors_preserve_meaning(monkeypatch, status, detail):
    def reply(request):
        assert request.url.params["local_only"] == "true"
        return httpx.Response(status, json={"detail": detail})

    with httpx.Client(transport=httpx.MockTransport(reply)) as peer:
        monkeypatch.setattr(SourceReader, "open", lambda self: peer)
        app = create_app(sources=[Source("peer", "Peer", url="http://peer")])
        response = TestClient(app, base_url="http://127.0.0.1").get("/api/metrics", params={"run_id": qualify("peer", "missing")})
    assert response.status_code == status
    assert response.json() == {"detail": detail if status != 503 else "Source Peer is unavailable"}


def test_openapi_describes_json_binary_and_error_contracts(client):
    schema = client.get("/openapi.json").json()
    for path, operations in schema["paths"].items():
        for operation in operations.values():
            assert operation["tags"], path
            response = operation["responses"].get("200", operation["responses"].get("201"))
            assert response["content"], path
            if path == "/api/asset":
                assert "application/json" not in response["content"]
                assert response["content"]["image/png"]["schema"]["format"] == "binary"
            else:
                assert response["content"]["application/json"]["schema"], path
            assert operation["responses"]["404"]["content"]["application/json"]["schema"]
    assert "/api/share-token" in schema["paths"] and "/api/share-link" not in schema["paths"]
