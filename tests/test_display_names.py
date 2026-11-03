import sqlite3

import httpx
from fastapi.testclient import TestClient

from cloverboard.server.app import create_app
from cloverboard.server.sources import Source, SourceReader, qualify
from cloverboard.storage.protocols import RunMeta
from cloverboard.storage.sqlite_backend import SqliteStorageReader, SqliteStorageWriter


def make_run(root, run_id: str = "baseline"):
    folder = root / run_id
    folder.mkdir()
    writer = SqliteStorageWriter(folder / "run.sqlite")
    writer.init_run(RunMeta(run_id, "baseline", 1.0))
    writer.flush_and_close()
    return folder, SqliteStorageReader(root)


def test_display_name_roundtrip_clear_validation_and_etag(tmp_path):
    folder, reader = make_run(tmp_path)
    client = TestClient(create_app(reader, logdir=tmp_path), base_url="http://127.0.0.1")

    initial = client.get("/api/runs")
    assert initial.status_code == 200
    assert initial.json()[0]["display_name"] is None
    assert initial.json()[0]["display_name_writable"] is True
    etag = initial.headers["etag"]

    saved = client.patch("/api/run-display-name", params={"run_id": "baseline"}, json={"display_name": "  Lower LR  "})
    assert saved.status_code == 200
    assert saved.json() == {"display_name": "Lower LR"}

    changed = client.get("/api/runs", headers={"if-none-match": etag})
    assert changed.status_code == 200
    assert changed.headers["etag"] != etag
    assert changed.json()[0]["display_name"] == "Lower LR"
    with sqlite3.connect(folder / "run.sqlite") as connection:
        assert connection.execute("SELECT value FROM meta WHERE key='display_name'").fetchone()[0] == '"Lower LR"'

    reopened = SqliteStorageReader(tmp_path).list_runs()[0]
    assert reopened.name == "baseline"
    assert reopened.display_name == "Lower LR"

    cleared = client.patch("/api/run-display-name", params={"run_id": "baseline"}, json={"display_name": "  "})
    assert cleared.status_code == 200
    assert cleared.json() == {"display_name": None}
    assert client.get("/api/runs").json()[0]["display_name"] is None
    with sqlite3.connect(folder / "run.sqlite") as connection:
        assert connection.execute("SELECT value FROM meta WHERE key='display_name'").fetchone() is None

    assert client.patch("/api/run-display-name", params={"run_id": "baseline"}, json={"display_name": "x" * 201}).status_code == 422
    assert client.patch("/api/run-display-name", params={"run_id": "baseline"}, json={"display_name": "bad\nname"}).status_code == 422
    assert client.patch("/api/run-display-name", params={"run_id": "missing"}, json={"display_name": "x"}).status_code == 404


def test_display_name_peer_summary_and_write_permissions(monkeypatch, tmp_path):
    _, reader = make_run(tmp_path, "example")
    reader.update_run_display_name("example", "Remote label")

    def reply(request):
        assert request.url.params["local_only"] == "true"
        return httpx.Response(200, json=[{
            "run_id": "example", "name": "baseline", "display_name": "Remote label", "started_at": 1.0,
        }])

    with httpx.Client(transport=httpx.MockTransport(reply)) as peer:
        monkeypatch.setattr(SourceReader, "open", lambda self: peer)
        runs, _ = SourceReader(reader, [Source("peer", "Peer", url="http://peer")]).run_catalog()
    peer_run = next(run for run in runs if run.source == "peer")
    assert peer_run.display_name == "Remote label"
    assert peer_run.display_name_writable is False

    folder_client = TestClient(create_app(reader, sources=[Source("folder", "Folder", reader=reader)]), base_url="http://127.0.0.1")
    assert folder_client.patch("/api/run-display-name", params={"run_id": qualify("folder", "example")}, json={"display_name": "Folder label"}).status_code == 200
    peer_client = TestClient(create_app(reader, sources=[Source("peer", "Peer", url="http://peer")]), base_url="http://127.0.0.1")
    assert peer_client.patch("/api/run-display-name", params={"run_id": qualify("peer", "example")}, json={"display_name": "Blocked"}).status_code == 403

    shared = create_app(reader, share=True)
    shared_client = TestClient(shared, base_url="http://127.0.0.1")
    headers = {"Authorization": f"Bearer {shared.state.access_token}"}
    assert shared_client.patch("/api/run-display-name", params={"run_id": "example"}, json={"display_name": "Blocked"}, headers=headers).status_code == 403
    assert all(run["display_name_writable"] is False for run in shared_client.get("/api/runs", headers=headers).json())
