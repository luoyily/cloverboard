import io
import sqlite3
from types import SimpleNamespace
import wave

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

import cloverboard as cb
from cloverboard.logger.api import _shutdown
from cloverboard.logger.context import get_context
from cloverboard.server.app import create_app
from cloverboard.server.models import RunQuery
from cloverboard.server.queries import matches_query
from cloverboard.server.sources import Source, qualify
from cloverboard.storage.sqlite_backend import SqliteStorageReader


@pytest.fixture
def recorded(tmp_path):
    cb.init(logdir=tmp_path, exp_name="baseline", config={"lr": .000001, "model": {"width": 16}}, tags=["baseline", " preview ", "preview"], capture_hardware=False)
    baseline = get_context().run_id
    cb.add_tags(["reviewed"])
    cb.remove_tags(["preview"])
    for step, value in [(0, 1.), (10, .7), (20, .4)]:
        cb.log({"validation/loss": value}, step)
    audio = np.zeros((2400, 2), dtype=np.float32)
    cb.log_audio("validation/audio", audio, 0, sample_rate=24000, caption="first")
    audio[:] = .5
    cb.log_audio("validation/audio", audio, 20, sample_rate=24000, caption="last")
    _shutdown()
    cb.init(logdir=tmp_path, exp_name="partial", tags=["variant", "partial"], capture_hardware=False)
    partial = get_context().run_id
    for step, value in [(0, .9), (7, .6), (14, .3)]:
        cb.log({"validation/loss": value}, step)
    _shutdown()
    return SqliteStorageReader(tmp_path), baseline, partial


def test_sdk_tags_and_audio_roundtrip_copy_and_media_index(recorded, tmp_path):
    reader, baseline, _ = recorded
    run = next(run for run in reader.list_runs() if run.run_id == baseline)
    assert run.tags == ["baseline", "reviewed"]
    assert reader.audio_timeline(baseline, "validation/audio", 2).steps == [0, 20]
    assert not reader.audio_page(baseline, "validation/audio", -1).audio
    first = reader.audio_page(baseline, "validation/audio", 10).audio[0]
    last = reader.audio_page(baseline, "validation/audio").audio[0]
    assert (first.step, last.step, last.sample_rate, last.channels, last.frames) == (0, 20, 24000, 2, 2400)
    with wave.open(str(tmp_path / baseline / first.path)) as source:
        assert np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").max() == 0
    with sqlite3.connect(tmp_path / baseline / "run.sqlite") as connection:
        assert "BLOB" not in [row[2] for row in connection.execute("PRAGMA table_info(audio)")]


def test_tag_edits_are_incremental_bounded_and_persistent(recorded, tmp_path):
    reader, baseline, _ = recorded
    app = create_app(reader, logdir=tmp_path)
    client = TestClient(app)
    response = client.patch("/api/run-tags", params={"run_id": baseline}, json={"add": ["human", "baseline"], "remove": ["reviewed"]})
    assert response.json()["tags"] == ["baseline", "human"]
    reader.update_run_tags(baseline, ["sdk-later"], [])
    assert reader.update_run_tags(baseline, ["human-later"], ["human"]) == ["baseline", "human-later", "sdk-later"]
    response = client.patch("/api/run-tags", params={"run_id": baseline}, json={"add": [f"tag-{i}" for i in range(64)]})
    assert response.status_code == 422
    assert len(next(run for run in SqliteStorageReader(tmp_path).list_runs() if run.run_id == baseline).tags) == 3
    assert client.patch("/api/run-tags", params={"run_id": baseline}, json={"add": ["   "]}).status_code == 422
    local = TestClient(create_app(reader, sources=[Source("folder", "Folder", reader=reader)]))
    assert local.patch("/api/run-tags", params={"run_id": qualify("folder", baseline)}, json={"add": ["local-folder"]}).status_code == 200
    peer = TestClient(create_app(reader, sources=[Source("peer", "Peer", url="http://127.0.0.1:6010")]))
    assert peer.patch("/api/run-tags", params={"run_id": qualify("peer", baseline)}, json={"add": ["blocked"]}).status_code == 403


def test_audio_http_range_and_path_boundary(recorded, tmp_path):
    reader, baseline, _ = recorded
    client = TestClient(create_app(reader))
    page = client.get("/api/audio", params={"run_id": baseline, "name": "validation/audio"}).json()
    query = {"run_id": baseline, "asset_id": page["audio"][0]["audio_id"]}
    full = client.get("/api/asset", params=query)
    assert full.status_code == 200 and full.content[:4] == b"RIFF"
    with wave.open(io.BytesIO(full.content)) as audio:
        assert audio.getnframes() == 2400
    part = client.get("/api/asset", params=query, headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == full.content[100:200]
    assert part.headers["content-range"] == f"bytes 100-199/{len(full.content)}"
    assert client.get("/api/asset", params=query, headers={"Range": "bytes=9999999-"}).status_code == 416
    with sqlite3.connect(tmp_path / baseline / "run.sqlite") as connection:
        connection.execute("UPDATE audio SET path='../outside.wav'")
    assert client.get("/api/asset", params=query).status_code == 403


def test_audio_peer_proxy_preserves_range_and_auth(recorded, monkeypatch):
    reader, baseline, _ = recorded
    leaf = create_app(reader, share=True)
    asset = reader.audio_page(baseline, "validation/audio").audio[0]
    real_client = httpx.AsyncClient
    import cloverboard.server.assets as assets_module
    monkeypatch.setattr(assets_module, "httpx", SimpleNamespace(AsyncClient=lambda **kwargs: real_client(transport=httpx.ASGITransport(leaf), **kwargs)))
    main = TestClient(create_app(None, sources=[Source("peer", "Peer", url="http://peer", token=leaf.state.access_token)]))
    response = main.get("/api/asset", params={"run_id": qualify("peer", baseline), "asset_id": asset.audio_id}, headers={"Range": "bytes=0-43"})
    assert response.status_code == 206 and len(response.content) == 44
    assert response.content[:4] == b"RIFF" and response.headers["content-type"].startswith("audio/")


def test_tag_query_and_view_preferences(recorded):
    reader, baseline, _ = recorded
    run = next(run for run in reader.list_runs() if run.run_id == baseline)
    query = RunQuery.model_validate({"rules": [{"field": "tags", "operator": "has_all", "value": ["baseline", "reviewed"]}, {"field": "config:model.width", "operator": "eq", "value": 16}]})
    assert matches_query(run, query)
    assert not matches_query(run, RunQuery.model_validate({"rules": [{"field": "tags", "operator": "has_any", "value": ["BASELINE"]}]}))
    with pytest.raises(ValueError):
        RunQuery.model_validate({"rules": [{"field": "config:lr", "operator": "has_all", "value": ["baseline"]}]})
    client = TestClient(create_app(reader))
    state = {"panel": "images", "media_type": "audio", "audio_step": 0, "table_decimals": 7, "show_tags_column": True, "tag_filter": ["reviewed"], "selected_ids": [baseline]}
    saved = client.post("/api/workspace/views", json={"name": "Audio review", "state": state}).json()["state"]
    assert all(saved[key] == value for key, value in state.items())


def _rpc(client, method, params=None, headers=None):
    request_headers = {"Accept": "application/json, text/event-stream", **(headers or {})}
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, headers=request_headers)


def test_mcp_streamable_http_initialization_discovery_and_all_tools(recorded):
    reader, baseline, partial = recorded
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        initialized = _rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "http-test", "version": "1"}})
        assert initialized.status_code == 200
        assert initialized.json()["result"]["serverInfo"]["name"] == "CloverBoard"
        notification = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers={"Accept": "application/json, text/event-stream"})
        assert notification.status_code == 202
        tools = _rpc(client, "tools/list").json()["result"]["tools"]
        assert {tool["name"] for tool in tools} == {"list_runs", "get_runs", "list_metrics", "compare_configs", "compare_metrics", "get_series", "plot_metric", "list_image_names", "list_images", "get_images"}
        assert all(tool["annotations"]["readOnlyHint"] for tool in tools)
        def call(name, arguments):
            result = _rpc(client, "tools/call", {"name": name, "arguments": arguments}).json()["result"]
            assert not result.get("isError"), result
            return result["structuredContent"]
        query = {"rules": [{"field": "tags", "operator": "has_any", "value": ["partial"]}]}
        assert call("list_runs", {"query": query})["runs"][0]["run_id"] == partial
        assert call("get_runs", {"run_ids": [baseline], "include": ["config"]})["runs"][0]["config"]["lr"] == .000001
        assert call("list_metrics", {"run_ids": [baseline]})["metrics"][0]["metric"] == "validation/loss"
        series = call("get_series", {"run_id": baseline, "metric": "validation/loss", "max_points": 2})
        assert series["downsampled"] and series["raw_count"] == 3 and series["returned_count"] == 2
        compared = call("compare_metrics", {"run_ids": [partial], "metrics": ["validation/loss"], "baseline_id": baseline, "step": 15, "targets": {"validation/loss": {"value": .5, "direction": "lte"}}, "include_stats": ["first_hit"]})
        row = compared["rows"][0]
        assert row["point"]["step"] == 14 and row["delta_baseline"] == pytest.approx(-.4)
        assert row["delta_target"] == pytest.approx(-.2) and row["target_met"]
        assert compared["rows"][1]["point"]["step"] == 10
        invalid = _rpc(client, "tools/call", {"name": "get_series", "arguments": {"run_id": baseline, "metric": "validation/loss", "max_points": 100000}}).json()["result"]
        assert invalid["isError"]


def test_mcp_share_auth_readonly_and_origin(recorded):
    reader, baseline, _ = recorded
    app = create_app(reader, share=True)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert _rpc(client, "tools/list").status_code == 401
        auth = {"Authorization": f"Bearer {app.state.access_token}"}
        assert _rpc(client, "tools/list", headers=auth).status_code == 200
        assert _rpc(client, "tools/list", headers={**auth, "Origin": "https://other.example"}).status_code == 403
        plotted = _rpc(client, "tools/call", {"name": "plot_metric", "arguments": {
            "run_ids": [baseline], "metric": "validation/loss"}}, auth).json()["result"]
        assert not plotted.get("isError") and plotted["content"][1]["mimeType"] == "image/png"
        assert client.patch("/api/run-tags", params={"run_id": baseline}, json={"add": ["blocked"]}, headers=auth).status_code == 403
        assert all(not run["tags_writable"] for run in client.get("/api/runs", headers=auth).json())
        blocked = _rpc(client, "tools/call", {"name": "execute_sql", "arguments": {"sql": "SELECT 1"}}, auth).json()["result"]
        assert blocked["isError"]
