"""Image indices stay text-only; selected pixels are bounded, cropped and isolated."""

import base64
from io import BytesIO
import sqlite3
from types import SimpleNamespace

import httpx
from PIL import Image, ImageDraw
import pytest
from fastapi.testclient import TestClient

from cloverboard.server.app import create_app
from cloverboard.server import mcp_images
from cloverboard.server.sources import Source, SourceReader, qualify
from cloverboard.server.thumbnails import thumbnail_cache
from cloverboard.storage.protocols import FileAsset, ImageRecord, RunMeta
from cloverboard.storage.sqlite_backend import SqliteStorageReader, SqliteStorageWriter
from tests.test_mcp_analysis import call, rpc


@pytest.fixture
def recorded(tmp_path):
    folder = tmp_path / "r"
    media = folder / "media"
    media.mkdir(parents=True)
    writer = SqliteStorageWriter(folder / "run.sqlite")
    writer.init_run(RunMeta("r", "Run", 1))
    picture = Image.new("RGB", (1600, 800), "blue")
    ImageDraw.Draw(picture).rectangle((1200, 600, 1327, 727), fill="red")
    for number in range(11):
        image_id = f"{number:032x}"
        picture.save(media / f"{image_id}.png")
        name = "val/prediction" if number < 9 else ("val/input" if number == 9 else "train/preview")
        step = 0 if number == 0 else 20
        writer.append_image(ImageRecord(image_id, name, step, number,
            f"media/{image_id}.png", 1600, 800, "png", "caption " * 50))
    writer.flush_and_close()
    return SqliteStorageReader(tmp_path), media


def get(client, items, max_side=512):
    result = rpc(client, "tools/call", {"name": "get_images", "arguments": {"items": items, "max_side": max_side}})
    assert not result.get("isError"), result
    return result


def ref(number=1, **kwargs):
    return {"run_id": "r", "image_id": f"{number:032x}", **kwargs}


def decode(result, index=0):
    content = [item for item in result["content"] if item["type"] == "image"][index]
    assert content["mimeType"] == "image/png"
    image = Image.open(BytesIO(base64.b64decode(content["data"])))
    image.load()
    return image


def test_discovery_and_paging_never_open_image_files(recorded, monkeypatch):
    reader, _ = recorded
    def forbidden(*args, **kwargs):
        raise AssertionError("An index request tried to read pixels")
    monkeypatch.setattr(Image, "open", forbidden)
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        names = call(client, "list_image_names", {"run_id": "r", "name": "VAL", "limit": 1})
        assert names["total"] == 2 and names["next_offset"] == 1
        assert names["names"][0] == {"name": "val/input", "count": 1, "step_count": 1, "first_step": 20, "last_step": 20}
        following = call(client, "list_image_names", {"run_id": "r", "name": "val", "offset": 1})
        assert following["names"][0]["name"] == "val/prediction" and following["next_offset"] is None
        latest = call(client, "list_images", {"run_id": "r", "name": "val/prediction"})
        assert latest["step"] == 20 and latest["total"] == 8 and latest["next_offset"] == 6
        assert len(latest["images"]) == 6 and set(latest["images"][0]) == {"image_id", "width", "height"}
        page = call(client, "list_images", {"run_id": "r", "name": "val/prediction", "step": 20, "offset": 6})
        assert len(page["images"]) == 2 and page["next_offset"] is None
        older = call(client, "list_images", {"run_id": "r", "name": "val/prediction", "step": 15, "include": ["caption"]})
        assert older["step"] == 0 and len(older["images"][0]["caption"]) == 200
        assert older["images"][0]["caption_truncated"]
        assert call(client, "list_images", {"run_id": "r", "name": "val/prediction", "step": -1})["images"] == []
        assert call(client, "list_image_names", {"run_id": "missing"})["error"]["code"] == "run_not_found"
        assert call(client, "list_images", {"run_id": "missing", "name": "val/input"})["error"]["code"] == "run_not_found"
    assert "path" not in str(latest) and "wall_time" not in str(latest)


def test_default_preview_and_original_pixel_crop_return_precise_metadata(recorded):
    reader, _ = recorded
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        result = get(client, [ref(), ref(crop=[1200, 600, 128, 128])])
    assert decode(result).size == (512, 256)
    crop = decode(result, 1)
    assert crop.size == (128, 128) and crop.getpixel((64, 64)) == (255, 0, 0)
    items = result["structuredContent"]["items"]
    assert [item["image_index"] for item in items] == [1, 2]
    assert items[0]["original_size"] == [1600, 800] and items[0]["returned_size"] == [512, 256]
    assert items[1]["crop"] == [1200, 600, 128, 128]
    assert "crop" not in items[0] and len(result["content"][0]["text"]) < 1000


def test_web_and_mcp_share_cache_with_separate_size_and_crop_variants(recorded, monkeypatch):
    reader, media = recorded
    calls = 0
    original = thumbnail_cache._encode
    def counted(*args):
        nonlocal calls
        calls += 1
        return original(*args)
    monkeypatch.setattr(thumbnail_cache, "_encode", counted)
    path = media / f"{1:032x}.png"
    before = path.read_bytes()
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        response = client.get("/api/asset", params={"run_id": "r", "asset_id": ref()["image_id"], "thumbnail": True})
        assert response.status_code == 200
        assert decode(get(client, [ref()])).size == (512, 256) and calls == 1
        assert decode(get(client, [ref()], 1024)).size == (1024, 512) and calls == 2
        for _ in range(2):
            assert decode(get(client, [ref(crop=[1200, 600, 128, 128])])).size == (128, 128)
        assert calls == 3
    assert path.read_bytes() == before and len(list(media.iterdir())) == 11


def test_crop_coordinates_follow_exif_orientation(recorded):
    reader, media = recorded
    picture = Image.new("RGB", (300, 100), "blue")
    ImageDraw.Draw(picture).rectangle((200, 0, 299, 99), fill="red")
    exif = picture.getexif()
    exif[274] = 6
    picture.save(media / f"{1:032x}.png", exif=exif)
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        result = get(client, [ref(crop=[0, 200, 100, 50])])
    assert result["structuredContent"]["items"][0]["original_size"] == [100, 300]
    assert decode(result).size == (100, 50) and decode(result).getpixel((50, 25)) == (255, 0, 0)


def test_failures_preserve_successful_image_order_and_hide_paths(recorded):
    reader, media = recorded
    (media / f"{2:032x}.png").write_bytes(b"corrupt image")
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        result = get(client, [ref(100), ref(1), ref(2), ref(3, crop=[1599, 0, 2, 2])])
        items = result["structuredContent"]["items"]
        assert [item.get("image_index") for item in items] == [None, 1, None, None]
        assert [items[i]["error"]["code"] for i in (0, 2, 3)] == ["image_not_found", "image_unavailable", "invalid_crop"]
        assert len([c for c in result["content"] if c["type"] == "image"]) == 1
        failed = rpc(client, "tools/call", {"name": "get_images", "arguments": {"items": [ref(100)]}})
        assert failed["isError"] and all(c["type"] == "text" for c in failed["content"])
    assert str(media) not in str(items) and "path" not in str(items)


def test_qualified_local_source_uses_same_index_and_image_reading(recorded):
    reader, _ = recorded
    run_id = qualify("folder", "r")
    with TestClient(create_app(sources=[Source("folder", "Folder", reader=reader)]), base_url="http://127.0.0.1") as client:
        assert call(client, "list_image_names", {"run_id": run_id})["total"] == 3
        assert call(client, "list_images", {"run_id": run_id, "name": "val/input"})["total"] == 1
        assert decode(get(client, [ref(run_id=run_id)])).size == (512, 256)


def test_peer_reads_authenticated_original_and_crops_on_requested_pixels(recorded, monkeypatch):
    reader, _ = recorded
    leaf = create_app(reader, share=True)
    real_client = httpx.AsyncClient
    requests = []
    async def capture(request):
        requests.append(request)
    def peer_client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return real_client(transport=httpx.ASGITransport(leaf), event_hooks={"request": [capture]}, **kwargs)
    monkeypatch.setattr(mcp_images, "httpx", SimpleNamespace(AsyncClient=peer_client, HTTPError=httpx.HTTPError))
    with TestClient(leaf, base_url="http://127.0.0.1") as leaf_client:
        denied = leaf_client.post("/mcp", headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_images", "arguments": {"items": [ref()]}}})
        assert denied.status_code == 401
        def metadata(self, source, route, params=None):
            query = {key: value for key, value in (params or {}).items() if value is not None}
            response = leaf_client.get(route, params={**query, "local_only": True},
                headers={"Authorization": f"Bearer {source.token}"})
            response.raise_for_status()
            return response.json()
        monkeypatch.setattr(SourceReader, "_json", metadata)
        app = create_app(sources=[Source("peer", "Peer", url="http://peer", token=leaf.state.access_token)])
        with TestClient(app, base_url="http://127.0.0.1") as client:
            run_id = qualify("peer", "r")
            assert call(client, "list_images", {"run_id": run_id, "name": "val/prediction"})["total"] == 8
            result = get(client, [ref(run_id=run_id, crop=[1200, 600, 128, 128])])
        assert decode(result).getpixel((64, 64)) == (255, 0, 0)
        assert requests[0].headers["Authorization"] == f"Bearer {leaf.state.access_token}"
        assert requests[0].url.params["thumbnail"] == "false" and requests[0].url.params["local_only"] == "true"
        assert requests[0].url.params["run_id"] == "r"
        assert leaf.state.access_token not in str(result) and "http://peer" not in str(result)
        authorized = leaf_client.post("/mcp", headers={"Accept": "application/json, text/event-stream", "Authorization": f"Bearer {leaf.state.access_token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_images", "arguments": {"items": [ref()]}}})
        assert not authorized.json()["result"].get("isError")


@pytest.mark.parametrize("status,content_type,body,limit", [(302, "image/png", b"", 64), (200, "text/html", b"secret", 64), (200, "image/png", b"x" * 65, 64)])
def test_peer_rejections_do_not_follow_redirects_or_leak_response_bodies(recorded, monkeypatch, status, content_type, body, limit):
    reader, _ = recorded
    original = reader.get_asset
    def asset(run_id, image_id, thumbnail=False):
        return FileAsset("application/octet-stream", image_id, url="http://peer/private", token="private-token") if image_id == ref(100)["image_id"] else original(run_id, image_id, thumbnail)
    monkeypatch.setattr(reader, "get_asset", asset)
    real_client = httpx.AsyncClient
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status, headers={"Content-Type": content_type, "Location": "http://other/private"}, stream=httpx.ByteStream(body))
    monkeypatch.setattr(mcp_images, "httpx", SimpleNamespace(AsyncClient=lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs), HTTPError=httpx.HTTPError))
    monkeypatch.setattr(mcp_images, "_MAX_SOURCE_BYTES", limit)
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        result = get(client, [ref(100), ref()])
    assert len(requests) == 1 and "error" in result["structuredContent"]["items"][0]
    assert "private-token" not in str(result) and "secret" not in str(result)


@pytest.mark.parametrize("arguments", [{"items": []}, {"items": [ref()] * 5}, {"items": [ref(crop=[0, 0, 0, 5])]},
    {"items": [ref(crop=[-1, 0, 2, 2])]}, {"items": [ref(crop=[.5, 0, 2, 2])]}, {"items": [ref(image_id="snapshot")]},
    {"items": [ref()], "max_side": 1025}])
def test_invalid_or_unbounded_pixel_requests_fail_validation(recorded, arguments):
    reader, _ = recorded
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        result = rpc(client, "tools/call", {"name": "get_images", "arguments": arguments})
    assert result["isError"]


def test_unsafe_or_oversized_images_fail_only_their_item(recorded, monkeypatch):
    reader, media = recorded
    with sqlite3.connect(media.parent / "run.sqlite") as connection:
        connection.execute("UPDATE images SET path='../outside.png' WHERE image_id=?", (ref(2)["image_id"],))
    original = Image.open
    def oversized(source, *args, **kwargs):
        if str(source).endswith(f"{3:032x}.png"):
            raise Image.DecompressionBombError("private original path")
        return original(source, *args, **kwargs)
    monkeypatch.setattr(Image, "open", oversized)
    with TestClient(create_app(reader), base_url="http://127.0.0.1") as client:
        result = get(client, [ref(2), ref(3), ref(1)])
    assert [item.get("error", {}).get("code") for item in result["structuredContent"]["items"]] == ["path_not_allowed", "invalid_image", None]
    assert "private original path" not in str(result)
