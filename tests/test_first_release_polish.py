import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from io import BytesIO
from types import SimpleNamespace

import httpx
import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient

import cloverboard as cb
from cloverboard.logger.api import _shutdown
from cloverboard.logger.context import get_context
from cloverboard.server.app import create_app
from cloverboard.server.sources import Source, qualify
from cloverboard.server.thumbnails import ThumbnailCache
from cloverboard.storage.protocols import ImageRecord, RunMeta, ScalarRecord
from cloverboard.storage.sqlite_backend import SqliteStorageReader, SqliteStorageWriter


def database(root, run_id="example"):
    directory = root / run_id
    directory.mkdir(parents=True)
    writer = SqliteStorageWriter(directory / "run.sqlite")
    writer.init_run(RunMeta(run_id, run_id, 1))
    return writer, SqliteStorageReader(root)


def test_sdk_metric_paths_share_top_level_groups_and_reject_empty_segments(tmp_path):
    cb.init(logdir=tmp_path, capture_hardware=False)
    run_id = get_context().run_id
    cb.log({"loss": 1, "acc": .5}, 0, group="train")
    cb.log({"train/loss": .8}, 1)
    cb.log({"loss": .9}, 0)
    cb.log({"metrics/Set5/psnr": 30}, 0)
    cb.log({"Set5/psnr": 32}, 0, group="metrics")
    with pytest.warns(RuntimeWarning):
        cb.log({"train//loss": .2}, 0)
    with pytest.warns(RuntimeWarning):
        cb.log({"loss": .2}, 0, group="train/deep")
    _shutdown()
    reader = SqliteStorageReader(tmp_path)
    assert reader.list_metrics(run_id) == ["loss", "metrics/Set5/psnr", "train/acc", "train/loss"]
    assert reader.get_scalar_series(run_id, "metrics/Set5/psnr")[0].value == 32
    fields = TestClient(create_app(reader)).get("/api/metrics", params={"run_id": run_id}).json()["fields"]
    assert {field["metric"]: (field["group"], field["name"]) for field in fields} == {
        "loss": ("General", "loss"), "train/acc": ("train", "acc"), "train/loss": ("train", "loss"),
        "metrics/Set5/psnr": ("metrics", "Set5/psnr"),
    }


def test_notes_are_portable_with_run_and_do_not_modify_training_scalars(tmp_path):
    writer, reader = database(tmp_path)
    client = TestClient(create_app(reader, logdir=tmp_path))
    empty = client.get("/api/run-notes?run_id=example").json()
    assert empty["revision"] == 0 and empty["writable"]
    saved = client.put("/api/run-notes?run_id=example", json={"body": "# Trial\n\n- lower lr", "revision": 0})
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    writer.append_scalars([ScalarRecord("train/loss", 0, 1, .4)])
    writer.flush_and_close()
    assert reader.get_scalar_series("example", "train/loss")[0].value == .4
    assert client.get("/api/runs").json()[0]["has_notes"] is True
    assert not (tmp_path / ".workspace").exists()
    assert client.put("/api/run-notes?run_id=example", json={"body": "stale", "revision": 0}).status_code == 409
    assert TestClient(create_app(SqliteStorageReader(tmp_path))).get("/api/run-notes?run_id=example").json()["body"].startswith("# Trial")
    assert client.put("/api/run-notes?run_id=example", json={"body": "", "revision": 1}).status_code == 200
    assert client.get("/api/runs").json()[0]["has_notes"] is False


def test_notes_local_sources_writable_peers_and_share_readonly(monkeypatch, tmp_path):
    writer, reader = database(tmp_path)
    writer.flush_and_close()
    leaf = create_app(reader, logdir=tmp_path, share=True)
    leaf_client = TestClient(leaf)
    real_client = httpx.Client
    def reply(request):
        assert request.url.params["local_only"] == "true"
        response = leaf_client.get(request.url.path, params=dict(request.url.params), headers=dict(request.headers))
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)
    import cloverboard.server.sources as module
    monkeypatch.setattr(module, "httpx", SimpleNamespace(Client=lambda **kwargs: real_client(transport=httpx.MockTransport(reply), **kwargs), HTTPError=httpx.HTTPError))
    app = create_app(reader, sources=[Source("folder", "Folder", reader=reader), Source("peer", "Peer", url="http://127.0.0.1:6024", token=leaf.state.access_token)])
    client = TestClient(app)
    assert client.put("/api/run-notes", params={"run_id": qualify("folder", "example")}, json={"body": "Local", "revision": 0}).status_code == 200
    peer_query = {"run_id": qualify("peer", "example")}
    assert client.get("/api/run-notes", params=peer_query).json()["writable"] is False
    assert client.put("/api/run-notes", params=peer_query, json={"body": "Remote", "revision": 1}).status_code == 403
    assert leaf_client.get("/api/run-notes?run_id=example").status_code == 401
    headers = {"Authorization": f"Bearer {leaf.state.access_token}"}
    assert leaf_client.get("/api/run-notes?run_id=example", headers=headers).json()["writable"] is False
    assert leaf_client.put("/api/run-notes?run_id=example", headers=headers, json={"body": "bad", "revision": 1}).status_code == 403


def test_workspace_sqlite_rename_and_new_settings_are_independent(tmp_path):
    client = TestClient(create_app(logdir=tmp_path))
    state = {"selected_ids": ["example"], "y_scale": "log", "chart_scales": {"validation/score": "linear"}, "collapsed_groups": ["optimizer"], "image_timeline_samples": 50, "images_per_step": 4}
    view = client.post("/api/workspace/views", json={"name": "Trial", "state": state}).json()
    client.put("/api/workspace/state", json={"state": {**state, "smoothing": .9}, "saved_at": 20})
    renamed = client.patch(f"/api/workspace/views/{view['view_id']}", json={"name": "  Renamed  "}).json()
    assert renamed["name"] == "Renamed"
    assert renamed["state"]["smoothing"] == .6
    client.post("/api/workspace/views", json={"name": "Other", "state": {}})
    assert client.patch(f"/api/workspace/views/{view['view_id']}", json={"name": "other"}).status_code == 409
    restarted = TestClient(create_app(logdir=tmp_path)).get("/api/workspace").json()
    assert restarted["state"]["smoothing"] == .9
    assert restarted["views"][0]["state"]["collapsed_groups"] == ["optimizer"]
    assert restarted["views"][0]["state"]["image_timeline_samples"] == 50
    files = list((tmp_path / ".workspace").iterdir())
    assert any(path.name == "workspace.sqlite" for path in files)
    assert not any(path.suffix == ".json" for path in files)
    readonly = TestClient(create_app(logdir=tmp_path, read_only=True))
    assert readonly.patch(f"/api/workspace/views/{view['view_id']}", json={"name": "no"}).status_code == 403


@pytest.mark.parametrize("content", ["bad json", '{"version": 1, "seed": "x", "slots": {"a": -1}}', '{"version": 1, "seed": "x", "slots": {"a": 1, "b": 1}}'])
def test_corrupt_color_rows_do_not_break_runs(tmp_path, content):
    client = TestClient(create_app(logdir=tmp_path))
    client.put("/api/workspace/colors", json={"keys": ["run"]})
    with sqlite3.connect(tmp_path / ".workspace" / "workspace.sqlite") as connection:
        connection.execute("UPDATE color_registry SET payload=?", (content,))
    assert client.get("/api/workspace/colors").json()["slots"] == {}
    assert client.get("/api/runs").status_code == 200


def test_corrupt_workspace_state_can_be_replaced_and_bad_database_reports_unavailable(tmp_path):
    client = TestClient(create_app(logdir=tmp_path))
    client.put("/api/workspace/state", json={"state": {}, "saved_at": 20})
    path = tmp_path / ".workspace" / "workspace.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE workspace_state SET payload='invalid json'")
    assert client.get("/api/workspace").json()["state"] is None
    assert client.put("/api/workspace/state", json={"state": {"y_scale": "log"}, "saved_at": 30}).status_code == 200
    assert client.get("/api/workspace").json()["state"]["y_scale"] == "log"
    other = tmp_path / "damaged"
    (other / ".workspace").mkdir(parents=True)
    (other / ".workspace" / "workspace.sqlite").write_bytes(b"invalid database")
    damaged = TestClient(create_app(logdir=other))
    assert damaged.get("/api/workspace").status_code == 503
    assert damaged.put("/api/workspace/state", json={"state": {}, "saved_at": 40}).status_code == 503


def test_image_sampling_and_pages_bound_large_histories_without_losing_steps(tmp_path):
    writer, reader = database(tmp_path)
    with sqlite3.connect(tmp_path / "example" / "run.sqlite") as connection:
        connection.executemany("INSERT INTO images VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            (f"{index:032x}", "prediction", index // 20, index, f"media/{index}.png", 10, 10, "png", "") for index in range(20_000)
        ])
    writer.flush_and_close()
    client = TestClient(create_app(reader, logdir=tmp_path))
    query = {"run_id": "example", "name": "prediction"}
    names = client.get("/api/image-names", params={"run_id": "example"}).json()
    assert names[0]["count"] == 20_000 and names[0]["step_count"] == 1000
    sampled = client.get("/api/image-timeline", params={**query, "max_steps": 50}).json()
    assert len(sampled["steps"]) == 50 and sampled["total_steps"] == 1000
    assert sampled["steps"][0] == 0 and sampled["steps"][-1] == 999
    omitted = next(step for step in range(1000) if step not in sampled["steps"])
    page = client.get("/api/images", params={**query, "step": omitted, "limit": 12}).json()
    assert page["step"] == omitted and len(page["images"]) == 12 and page["total"] == 20
    assert len(json.dumps(page)) < 6000
    second = client.get("/api/images", params={**query, "step": omitted, "limit": 12, "offset": page["next_offset"]}).json()
    assert len(second["images"]) == 8 and second["next_offset"] is None
    assert not ({item["image_id"] for item in page["images"]} & {item["image_id"] for item in second["images"]})
    assert client.get("/api/images", params={**query, "step": -1}).json()["images"] == []
    assert client.get("/api/images", params=query).json()["step"] == 999
    assert client.get("/api/images", params={**query, "limit": 10000}).status_code == 422
    assert client.get("/api/image-timeline", params={**query, "max_steps": 10000}).status_code == 422


def test_thumbnail_is_small_memory_bounded_and_readonly(tmp_path):
    writer, reader = database(tmp_path)
    media = tmp_path / "example" / "media"
    media.mkdir()
    path = media / "large.png"
    Image.fromarray(np.zeros((2000, 3000, 3), dtype=np.uint8)).save(path)
    image_id = "a" * 32
    writer.append_image(ImageRecord(image_id, "large", 0, 1, "media/large.png", 3000, 2000, "png"))
    writer.flush_and_close()
    before = path.read_bytes()
    client = TestClient(create_app(reader, logdir=tmp_path, read_only=True))
    preview = client.get("/api/asset", params={"run_id": "example", "asset_id": image_id, "thumbnail": True})
    assert preview.status_code == 200
    with Image.open(BytesIO(preview.content)) as image:
        assert max(image.size) == 512
    assert path.read_bytes() == before
    assert list(media.iterdir()) == [path]
    cache = ThumbnailCache(max_bytes=1)
    cache.get(path)
    assert cache._bytes <= 1 and len(cache._items) == 0


def test_thumbnail_concurrent_misses_share_encoding_and_invalidate_changed_files(tmp_path, monkeypatch):
    path = tmp_path / "image.png"
    Image.new("RGB", (800, 600), "red").save(path)
    cache = ThumbnailCache()
    encode = cache._encode
    start, encoding, release, lock = Barrier(4), Event(), Event(), Lock()
    calls = 0

    def counted(value, max_side, crop):
        nonlocal calls
        with lock:
            calls += 1
        encoding.set()
        assert release.wait(5)
        return encode(value, max_side, crop)

    def read():
        start.wait(timeout=5)
        return cache.get(path)

    monkeypatch.setattr(cache, "_encode", counted)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(read) for _ in range(4)]
        try:
            assert encoding.wait(5)
        finally:
            release.set()
        results = [future.result(timeout=5) for future in futures]
    assert calls == 1 and all(value == results[0] for value in results)
    Image.new("RGB", (900, 700), "blue").save(path)
    assert cache.get(path) != results[0]
    assert calls == 2


def test_thumbnail_http_cache_validation_and_share_auth(tmp_path, monkeypatch):
    writer, reader = database(tmp_path)
    media = tmp_path / "example" / "media"
    media.mkdir()
    Image.new("RGB", (800, 600), "red").save(media / "image.png")
    image_id = "a" * 32
    writer.append_image(ImageRecord(image_id, "image", 0, 1, "media/image.png", 800, 600, "png", ""))
    writer.flush_and_close()
    query = {"run_id": "example", "asset_id": image_id, "thumbnail": True}
    client = TestClient(create_app(reader))
    first = client.get("/api/asset", params=query)
    assert first.status_code == 200 and first.headers["cache-control"] == "private, max-age=3600"
    etag = first.headers["etag"]
    unchanged = client.get("/api/asset", params=query, headers={"If-None-Match": f'"other", W/{etag}'})
    assert unchanged.status_code == 304 and unchanged.content == b""

    leaf = create_app(reader, share=True)
    share = TestClient(leaf)
    assert share.get("/api/asset", params=query, headers={"If-None-Match": etag}).status_code == 401
    headers = {"Authorization": f"Bearer {leaf.state.access_token}", "If-None-Match": etag}
    assert share.get("/api/asset", params=query, headers=headers).headers["cache-control"] == "no-store"

    import cloverboard.server.assets as assets
    real_client = httpx.AsyncClient
    monkeypatch.setattr(assets, "httpx", SimpleNamespace(AsyncClient=lambda **kwargs: real_client(transport=httpx.ASGITransport(leaf), **kwargs)))
    peer = TestClient(create_app(sources=[Source("peer", "Peer", url="http://peer", token=leaf.state.access_token)]))
    response = peer.get("/api/asset", params={**query, "run_id": qualify("peer", "example")}, headers={"If-None-Match": etag})
    assert response.status_code == 304 and response.headers["etag"] == etag
    assert response.headers["cache-control"] == "no-store"


def test_positive_bounds_and_share_tokens(tmp_path):
    writer, reader = database(tmp_path)
    writer.append_scalars([ScalarRecord("loss", i, i, value) for i, value in enumerate([-4, 0, .001, 10])])
    writer.flush_and_close()
    bounds = reader.get_scalar_bounds("example", "loss")
    assert (bounds.minimum, bounds.positive_minimum, bounds.maximum) == (-4, .001, 10)
    app = create_app(reader, logdir=tmp_path, share=True)
    client = TestClient(app)
    assert client.get("/api/share-token").status_code == 401
    response = client.get("/api/share-token", headers={"Authorization": f"Bearer {app.state.access_token}"})
    assert response.json()["token"] == app.state.access_token
    assert response.headers["cache-control"] == "no-store"
    assert TestClient(create_app(reader)).get("/api/share-token").json()["token"] is None
