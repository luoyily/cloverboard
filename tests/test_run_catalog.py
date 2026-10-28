"""Run discovery distinguishes a successful empty source from a failed source."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import httpx
from fastapi.testclient import TestClient
import pytest

from cloverboard.server.app import create_app
from cloverboard.server.sources import Source, SourceReader, SourceUnavailable, qualify
from cloverboard.storage.protocols import RunSummary
from tests.fakes import FakeStorageReader


PEER_RUN = {"run_id": "same", "name": "Peer run", "started_at": 1}


def test_catalog_tracks_current_runs_and_source_failure_and_recovery(monkeypatch):
    primary = FakeStorageReader([RunSummary("same", "Local run", 1)])
    folder = FakeStorageReader([RunSummary("same", "Folder run", 1)])
    app = create_app(primary, sources=[Source("peer", "Peer", url="http://peer"), Source("folder", "Folder", reader=folder)])
    available = True

    def fetch(source, route, params=None):
        assert route == "/api/runs"
        if not available:
            raise SourceUnavailable("Peer is offline")
        return [PEER_RUN]

    monkeypatch.setattr(app.state.reader, "_json", fetch)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.get("/api/run-catalog")
        assert first.status_code == 200
        payload = first.json()
        assert {run["run_id"] for run in payload["runs"]} == {"same", qualify("peer", "same"), qualify("folder", "same")}
        assert payload["sources"] == [
            {"source_id": "local", "label": "Local", "kind": "local", "online": True},
            {"source_id": "peer", "label": "Peer", "kind": "peer", "online": True},
            {"source_id": "folder", "label": "Folder", "kind": "local", "online": True},
        ]
        assert client.get("/api/run-catalog?local_only=true").json() == payload
        available = False
        offline = client.get("/api/run-catalog").json()
        assert {run["run_id"] for run in offline["runs"]} == {"same", qualify("folder", "same")}
        assert next(source for source in offline["sources"] if source["source_id"] == "peer")["online"] is False
        assert qualify("peer", "same") not in {run["run_id"] for run in client.get("/api/runs").json()}
        available = True
        recovered = client.get("/api/run-catalog").json()
        assert recovered == payload
        # The one-hop endpoint and local prefetch keep their list response shape.
        assert {run["run_id"] for run in client.get("/api/runs?local_only=true").json()} == {"same", qualify("folder", "same")}


def test_catalog_etag_changes_when_empty_peer_becomes_offline(monkeypatch):
    app = create_app(sources=[Source("peer", "Peer", url="http://peer")])
    available = True

    def fetch(source, route, params=None):
        if not available:
            raise SourceUnavailable("Peer is offline")
        return []

    monkeypatch.setattr(app.state.reader, "_json", fetch)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.get("/api/run-catalog")
        assert first.json() == {"runs": [], "sources": [{"source_id": "peer", "label": "Peer", "kind": "peer", "online": True}]}
        etag = first.headers["etag"]
        assert client.get("/api/run-catalog", headers={"if-none-match": etag}).status_code == 304
        available = False
        changed = client.get("/api/run-catalog", headers={"if-none-match": etag})
        assert changed.status_code == 200
        assert changed.json()["runs"] == []
        assert changed.json()["sources"][0]["online"] is False
        assert changed.headers["etag"] != etag
        assert client.get("/api/run-catalog", headers={"if-none-match": changed.headers["etag"]}).status_code == 304
        available = True
        restored = client.get("/api/run-catalog", headers={"if-none-match": changed.headers["etag"]})
        assert restored.status_code == 200 and restored.headers["etag"] == etag


@pytest.mark.parametrize("failure", ["unauthorized", "invalid-json", "invalid-run", "object", "empty-string", "null"])
def test_rejected_or_invalid_peer_response_is_failed_not_authoritative_empty(failure):
    reader = SourceReader(None, [Source("peer", "Peer", url="http://peer", token="secret")])
    healthy = True

    def respond(request):
        assert request.url.params["local_only"] == "true"
        assert request.headers["authorization"] == "Bearer secret"
        if healthy:
            return httpx.Response(200, json=[PEER_RUN])
        if failure == "unauthorized":
            return httpx.Response(401, json={"detail": "Invalid access token"})
        if failure == "invalid-json":
            return httpx.Response(200, content=b"not json")
        if failure == "object":
            return httpx.Response(200, json={})
        if failure == "empty-string":
            return httpx.Response(200, json="")
        if failure == "null":
            return httpx.Response(200, content=b"null")
        return httpx.Response(200, json=[PEER_RUN, {"run_id": "broken", "started_at": "not a timestamp"}])

    reader._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        assert [run.run_id for run in reader.list_runs()] == [qualify("peer", "same")]
        healthy = False
        runs, sources = reader.run_catalog()
        assert runs == []
        assert sources == [{"source_id": "peer", "label": "Peer", "kind": "peer", "online": False}]
    finally:
        reader.close()


def test_concurrent_catalogs_keep_status_from_their_own_scan(monkeypatch):
    reader = SourceReader(None, [Source("a", "A", url="http://a"), Source("b", "B", url="http://b")])
    calls, lock = {"a": 0, "b": 0}, Lock()
    first_failed, first_waiting, release_first = Event(), Event(), Event()

    def fetch(source, route, params=None):
        with lock:
            calls[source.key] += 1
            call = calls[source.key]
        if source.key == "a":
            if call == 1:
                raise SourceUnavailable("Only the first scan fails")
            return [PEER_RUN]
        if call == 1:
            first_waiting.set()
            assert release_first.wait(5)
        return []

    original = reader._list_source

    def capture(source):
        result = original(source)
        if source.key == "a" and not result[1]["online"]:
            first_failed.set()
        return result

    monkeypatch.setattr(reader, "_json", fetch)
    monkeypatch.setattr(reader, "_list_source", capture)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(reader.run_catalog)
        try:
            assert first_failed.wait(5) and first_waiting.wait(5)
            second_runs, second_status = reader.run_catalog()
            assert [run.run_id for run in second_runs] == [qualify("a", "same")]
            assert all(source["online"] for source in second_status)
            assert all(source["online"] for source in reader.status())
        finally:
            release_first.set()
        first_runs, first_status = first.result(timeout=5)
    assert first_runs == []
    assert first_status == [
        {"source_id": "a", "label": "A", "kind": "peer", "online": False},
        {"source_id": "b", "label": "B", "kind": "peer", "online": True},
    ]


@pytest.mark.parametrize("reader", [None, FakeStorageReader()])
def test_catalog_without_peers_is_a_complete_local_snapshot(reader):
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        payload = client.get("/api/run-catalog").json()
    assert payload["runs"] == []
    assert payload["sources"] == ([{"source_id": "local", "label": "Local", "kind": "local", "online": True}] if reader is not None else [])


def test_primary_scan_failure_is_not_a_successful_empty_catalog(monkeypatch):
    primary = FakeStorageReader()

    def fail():
        raise OSError("Cannot read the primary log directory")

    monkeypatch.setattr(primary, "list_runs", fail)
    with TestClient(create_app(primary), base_url="http://127.0.0.1") as client:
        assert client.get("/api/run-catalog").status_code == 503
