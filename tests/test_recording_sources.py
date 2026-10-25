import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import zipfile
import warnings

import httpx
import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient

import cloverboard as cb
from cloverboard.logger.api import _shutdown
from cloverboard.logger.context import get_context
from cloverboard.server.app import create_app
from cloverboard.server.sources import Source, SourceReader, load_sources, qualify
from cloverboard.storage.protocols import RunSummary, ScalarPoint
from cloverboard.storage.sqlite_backend import SqliteStorageReader
from tests.fakes import FakeStorageReader


def test_parameter_collection_preserves_function_behavior_and_groups(tmp_path):
    cb.init(logdir=tmp_path, capture_hardware=False)

    @cb.track_params(prefix="train", exclude=("secret",))
    def train(lr=.01, epochs=3, model=None, secret="x"):
        return epochs + 1

    assert train(epochs=4, model=object()) == 5
    assert get_context().config["train"] == {"lr": .01, "epochs": 4}
    params = [SimpleNamespace(numel=lambda: 12, requires_grad=True), SimpleNamespace(numel=lambda: 4, requires_grad=False)]
    cb.watch(SimpleNamespace(parameters=lambda: iter(params)), SimpleNamespace(param_groups=[{"params": params, "lr": .01, "weight_decay": .1}, {"params": [], "lr": .1}]))
    recorded = get_context().config["watch"]
    assert recorded["model"]["parameters"] == 16
    assert recorded["model"]["trainable_parameters"] == 12
    assert recorded["optimizer"]["param_groups"] == [{"lr": .01, "weight_decay": .1}, {"lr": .1}]
    with pytest.raises(TypeError):
        train(unexpected=True)


def test_snapshot_is_standalone_bounded_and_captures_untracked_files(tmp_path, directory_link):
    project = tmp_path / "project"
    (project / "src" / "nested").mkdir(parents=True)
    (project / "src" / "model.py").write_text("MODEL = 1")
    (project / "src" / "nested" / "train.py").write_text("TRAIN = 2")
    (project / "train.py").write_text("ENTRY = 3")
    (project / "secret.env").write_text("hidden")
    (project / ".venv").mkdir()
    (project / ".venv" / "hidden.py").write_text("hidden")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden.py").write_text("hidden")
    directory_link(project / "linked", outside)
    cb.init(logdir=project / "runs", capture_hardware=False)
    archive = cb.snapshot_code(root=project, include=["**/*.py"])
    assert archive is not None
    with zipfile.ZipFile(archive) as package:
        assert set(package.namelist()) == {"src/model.py", "src/nested/train.py", "train.py"}
        assert package.read("train.py") == b"ENTRY = 3"
    before = archive.read_bytes()
    (project / "train.py").write_text("CHANGED")
    assert cb.snapshot_code(root=project, include=["train.py"]).read_bytes() == before
    manifest = json.loads((archive.parent / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert len(manifest["files"]) == 3


def test_snapshot_rules_relative_to_toml_and_partial_manifest(tmp_path):
    (tmp_path / "a.py").write_text("a" * 100)
    (tmp_path / "b.py").write_text("b")
    rules = tmp_path / "snapshot.toml"
    rules.write_text('[snapshot]\nroot = "."\ninclude = ["*.py"]\nexclude = []\n')
    cb.init(logdir=tmp_path / "runs", capture_hardware=False)
    archive = cb.snapshot_code(config=rules, max_file_bytes=10)
    manifest = json.loads((archive.parent / "manifest.json").read_text())
    assert manifest["status"] == "partial"
    assert manifest["files"][0]["path"] == "b.py"
    assert manifest["skipped"][0]["path"] == "a.py"
    _shutdown()
    cb.init(logdir=tmp_path / "runs", capture_hardware=False)
    assert cb.snapshot_code(root=tmp_path, include=["../*.py"]) is None


def _record_images(logdir):
    cb.init(logdir=logdir, capture_hardware=False)
    run_id = get_context().run_id
    pixels = np.zeros((12, 18, 3), dtype=np.uint8)
    cb.log_image("validation/prediction", pixels, 10, caption="first")
    pixels[:] = 255
    cb.log_image("validation/prediction", pixels, 10, caption="second", format="jpeg")
    cb.log({"loss": .2}, step=10)
    _shutdown()
    return run_id


def test_images_copy_input_keep_same_step_records_and_survive_restart(tmp_path):
    run_id = _record_images(tmp_path)
    reader = SqliteStorageReader(tmp_path)
    images = reader.image_page(run_id, "validation/prediction").images
    assert len(images) == 2
    assert images[0].image_id != images[1].image_id
    first = next(image for image in images if image.caption == "first")
    with Image.open(tmp_path / run_id / first.path) as picture:
        assert picture.getpixel((0, 0)) == (0, 0, 0)
    assert reader.get_scalar_series(run_id, "loss")[0].value == .2
    with sqlite3.connect(tmp_path / run_id / "run.sqlite") as connection:
        types = [row[2] for row in connection.execute("PRAGMA table_info(images)")]
        assert "BLOB" not in types
    client = TestClient(create_app(reader, logdir=tmp_path, read_only=True))
    records = client.get("/api/images", params={"run_id": run_id, "name": "validation/prediction"}).json()["images"]
    assert len(records) == 2
    response = client.get("/api/asset", params={"run_id": run_id, "asset_id": first.image_id})
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
    assert client.get("/api/asset", params={"run_id": run_id, "asset_id": "../../run.sqlite"}).status_code == 404


def test_assets_share_auth_and_reject_index_paths_outside_run(tmp_path):
    run_id = _record_images(tmp_path)
    reader = SqliteStorageReader(tmp_path)
    image = reader.image_page(run_id, "validation/prediction").images[0]
    app = create_app(reader, logdir=tmp_path, share=True)
    client = TestClient(app)
    query = {"run_id": run_id, "asset_id": image.image_id}
    assert client.get("/api/asset", params=query).status_code == 401
    headers = {"Authorization": f"Bearer {app.state.access_token}"}
    assert client.get("/api/asset", params=query, headers=headers).status_code == 200
    with sqlite3.connect(tmp_path / run_id / "run.sqlite") as connection:
        connection.execute("UPDATE images SET path = '../outside.png'")
    assert client.get("/api/asset", params=query, headers=headers).status_code == 403
    assert not (tmp_path / ".workspace").exists()


def test_one_hop_sources_isolate_same_ids_authenticate_and_keep_offline_identity(monkeypatch, tmp_path):
    run = RunSummary("same", "Example", 1)
    primary = FakeStorageReader([run], {("same", "loss"): [ScalarPoint(0, .2)]})
    leaf = create_app(primary, share=True, sources=[Source("nested", "Nested", url="http://unreachable.test")])
    leaf_client = TestClient(leaf)
    calls = []
    offline = False
    def reply(request):
        calls.append(request)
        if offline:
            raise httpx.ConnectError("offline", request=request)
        assert request.url.params["local_only"] == "true"
        result = leaf_client.get(request.url.path, params=request.url.params.multi_items(), headers=dict(request.headers))
        return httpx.Response(result.status_code, content=result.content, headers=result.headers)
    real_client = httpx.Client
    import cloverboard.server.sources as module
    monkeypatch.setattr(module, "httpx", SimpleNamespace(Client=lambda **kwargs: real_client(transport=httpx.MockTransport(reply), **kwargs), HTTPError=httpx.HTTPError))
    peer = Source("peer", "Other run folder", url="http://127.0.0.1:6022", token=leaf.state.access_token)
    app = create_app(primary, sources=[peer, Source("folder", "Local folder", reader=primary)])
    client = TestClient(app)
    runs = client.get("/api/runs").json()
    assert len(runs) == 3
    assert len({run["run_id"] for run in runs}) == 3
    remote_id = qualify("peer", "same")
    query = {"run_id": remote_id, "metric": "loss"}
    assert client.get("/api/scalars", params=query).json()["points"][0]["value"] == .2
    batch = client.get("/api/scalars-batch", params={"run_id": remote_id, "metrics": ["loss", "missing"]}).json()
    assert [row["metric"] for row in batch] == ["loss", "missing"]
    assert batch[0]["run_id"] == remote_id
    assert batch[0]["points"][0]["value"] == .2
    assert batch[1]["points"] == []
    assert client.get("/api/images", params={"run_id": remote_id, "name": "prediction"}).json()["images"] == []
    assert client.get("/api/run-details", params={"run_id": remote_id}).status_code == 200
    assert client.get("/api/scalars", params={**query, "local_only": True}).status_code == 503
    assert len(client.get("/api/runs", params={"local_only": True}).json()) == 2
    offline = True
    cached = client.get("/api/runs").json()
    assert next(run for run in cached if run["run_id"] == remote_id)["source_online"] is False
    assert len(cached) == 3
    assert client.get("/api/scalars", params={"run_id": "same", "metric": "loss"}).status_code == 200
    assert calls


def test_sources_config_supports_loopback_local_dirs_and_stable_keys(tmp_path, monkeypatch):
    config = tmp_path / "sources.toml"
    config.write_text('[sources.experiment_b]\nlabel="Local B"\nlogdir="runs-b"\n[sources.gpu]\nurl="http://127.0.0.1:6007"\ntoken_env="TEST_CLOVER_PEER_TOKEN"\n')
    monkeypatch.setenv("TEST_CLOVER_PEER_TOKEN", "test-only")
    sources = load_sources(config=config)
    assert [source.key for source in sources] == ["experiment_b", "gpu"]
    assert sources[0].reader is not None
    assert sources[1].token == "test-only"
    assert load_sources("test=127.0.0.1:6008")[0].url == "http://127.0.0.1:6008"
    with pytest.raises(ValueError):
        load_sources("local=http://127.0.0.1:6008")


def test_image_view_state_roundtrip(tmp_path):
    client = TestClient(create_app(logdir=tmp_path))
    payload = {"name": "Predictions", "state": {"panel": "images", "image_name": "prediction", "image_step": 0}}
    assert client.post("/api/workspace/views", json=payload).status_code == 201
    state = TestClient(create_app(logdir=tmp_path)).get("/api/workspace").json()["views"][0]["state"]
    assert (state["panel"], state["image_name"], state["image_step"]) == ("images", "prediction", 0)


def test_invalid_images_do_not_interrupt_training_with_warnings_as_errors(tmp_path):
    cb.init(logdir=tmp_path, capture_hardware=False)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cb.log_image("invalid", np.ones((3, 4)) * 255, 0)
        cb.log_image("invalid", object(), 0)
    cb.log({"loss": 1}, 0)
    _shutdown()
    assert SqliteStorageReader(tmp_path).list_runs()
