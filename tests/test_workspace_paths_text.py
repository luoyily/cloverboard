"""Opt-in path metadata and basic plain-string records through SDK/storage/HTTP."""

import json
from pathlib import Path
import sys
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

import cloverboard as cb
from cloverboard.logger.api import _shutdown
from cloverboard.logger.context import get_context
from cloverboard.logger.paths import metadata_error, metadata_path
from cloverboard.logger.text import MAX_TEXT_BYTES
from cloverboard.server.app import create_app
from cloverboard.server.sources import Source, SourceReader, qualify
from cloverboard.storage.protocols import TextRecord
from cloverboard.storage.sqlite_backend import SqliteStorageReader


def test_workspace_root_is_opt_in_and_outside_paths_are_unchanged(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    inside, outside = workspace / "data" / "sample.png", tmp_path / "project-other" / "sample.png"
    config = {"input": str(inside), "nested": [inside, {"outside": str(outside)}], "relative": "data/sample.png", "root": workspace,
              "message": "A plain message", "url": "https://example.test/file"}
    monkeypatch.setattr(sys, "argv", [str(workspace / "train.py"), f"--input={inside}", str(outside), "--epochs=4"])
    for enabled in (False, True):
        cb.init(logdir=tmp_path / f"runs-{enabled}", config=config, capture_hardware=False,
                workspace_root=workspace if enabled else None)
        run_id = get_context().run_id
        cb.log_config({"later": str(workspace / "checkpoints" / "best.pt")})
        _shutdown()
        details = SqliteStorageReader(tmp_path / f"runs-{enabled}").get_run_details(run_id)
        assert details.config["input"] == ("data/sample.png" if enabled else str(inside))
        assert details.config["nested"] == ["data/sample.png" if enabled else str(inside), {"outside": str(outside)}]
        assert details.config["root"] == ("." if enabled else str(workspace))
        assert details.config["later"] == ("checkpoints/best.pt" if enabled else str(workspace / "checkpoints" / "best.pt"))
        assert details.config["relative"] == "data/sample.png" and details.config["message"] == "A plain message"
        assert details.config["url"] == "https://example.test/file"
        assert details.env["argv"] == (["train.py", "--input=data/sample.png", str(outside), "--epochs=4"] if enabled else sys.argv)
        assert "workspace_root" not in details.env
    assert config["input"] == str(inside) and config["nested"][0] == inside


def test_relative_workspace_is_frozen_at_init_and_config_file_still_loads(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    config = project / "config.json"
    config.write_text(json.dumps({"file": str(project / "data.bin")}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cb.init(logdir=tmp_path / "runs", config=config, workspace_root="project", capture_hardware=False)
    context = get_context()
    assert context.workspace_root == project and context.config["file"] == "data.bin"
    monkeypatch.chdir(project)
    cb.log_config({"new": str(project / "outputs" / "last.bin")})
    assert context.config["new"] == "outputs/last.bin"
    assert metadata_path(str(project / "folder" / ".." / "data.bin"), project) == "data.bin"
    assert metadata_path(str(project.parent / "outside.bin"), project) == str(project.parent / "outside.bin")


def test_snapshot_metadata_changes_without_changing_file_access_or_text(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    body = f"A literal path: {project / 'data.bin'}\n**Markdown** <tag> 中文"
    (project / "train.py").write_bytes(body.encode("utf-8"))
    cb.init(logdir=tmp_path / "runs", workspace_root=project, capture_hardware=False)
    run_id = get_context().run_id
    archive = cb.snapshot_code(root=project, include=["train.py"])
    cb.log_text("debug/output", body, 0)
    _shutdown()
    reader = SqliteStorageReader(tmp_path / "runs")
    assert reader.get_run_details(run_id).snapshot["root"] == "."
    with zipfile.ZipFile(archive) as saved:
        assert saved.read("train.py").decode("utf-8") == body
    assert reader.get_text(run_id, "debug/output").text == body
    error = FileNotFoundError(2, "Missing file", str(project / "missing.py"))
    assert metadata_error(error, project) == "Missing file: missing.py"
    assert metadata_error(error, None) == str(error)


@pytest.fixture
def recorded(tmp_path):
    cb.init(logdir=tmp_path, capture_hardware=False)
    run_id = get_context().run_id
    cb.log_text("debug/output", "first\n中文", 0)
    cb.log_text("debug/output", "middle", 10)
    cb.log_text("debug/output", "last", 20)
    cb.log_text("debug/output", "overwritten", 10)
    cb.log_text("debug/empty", "", 0)
    _shutdown()
    return SqliteStorageReader(tmp_path), run_id


def test_text_sdk_overwrites_one_name_step_without_files(recorded, tmp_path):
    reader, run_id = recorded
    names = {item.name: item for item in reader.text_names(run_id)}
    assert names["debug/output"].count == 3 and names["debug/output"].step_count == 3
    assert reader.text_timeline(run_id, "debug/output").steps == [0, 10, 20]
    assert reader.get_text(run_id, "debug/output").text == "last"
    assert reader.get_text(run_id, "debug/output", 15).text == "overwritten"
    assert reader.get_text(run_id, "debug/output", -1) is None
    assert reader.get_text(run_id, "debug/empty").text == ""
    assert reader.get_text(run_id, "absent") is None
    run = reader.list_runs()[0]
    assert not run.has_scalars and run.last_recorded_at is not None
    assert not (tmp_path / run_id / "media").exists()


def test_text_timeline_endpoints_are_sampled_and_long_body_is_preserved(tmp_path):
    cb.init(logdir=tmp_path, capture_hardware=False)
    run_id = get_context().run_id
    body = "中" * (MAX_TEXT_BYTES // 3)
    for step in range(20):
        cb.log_text("long", body if step == 19 else "short", step)
    _shutdown()
    reader = SqliteStorageReader(tmp_path)
    timeline = reader.text_timeline(run_id, "long", 3)
    assert timeline.steps[0] == 0 and timeline.steps[-1] == 19 and timeline.total_steps == 20
    assert len(timeline.steps) == 3 and reader.get_text(run_id, "long").text == body


@pytest.mark.parametrize("name,text,step", [("", "text", 1), ("output", {"response": "x"}, 1),
    ("output", "中" * (MAX_TEXT_BYTES // 3 + 1), 1), ("output", "text", True), ("output", "text", 2**53)],
    ids=["empty-name", "mapping", "too-large", "bool-step", "overflow-step"])
def test_invalid_text_warns_and_skips(tmp_path, name, text, step):
    cb.init(logdir=tmp_path, capture_hardware=False)
    run_id = get_context().run_id
    with pytest.warns(RuntimeWarning, match="text skipped"):
        cb.log_text(name, text, step)
    _shutdown()
    assert SqliteStorageReader(tmp_path).text_names(run_id) == []


def test_text_http_local_share_and_workspace_view(recorded):
    reader, run_id = recorded
    app = create_app(reader, share=True)
    with TestClient(app) as client:
        query = {"run_id": run_id, "name": "debug/output"}
        assert client.get("/api/text", params=query).status_code == 401
        auth = {"Authorization": f"Bearer {app.state.access_token}"}
        assert client.get("/api/text", params={**query, "step": 15}, headers=auth).json()["text"] == "overwritten"
        assert client.get("/api/text", params={**query, "step": -1}, headers=auth).json() is None
        assert client.get("/api/text-names", params={"run_id": run_id}, headers=auth).json()[0]["name"] == "debug/empty"
        assert client.get("/api/text-timeline", params=query, headers=auth).json()["steps"] == [0, 10, 20]
        assert client.get("/api/text", params={**query, "step": 2**70}, headers=auth).status_code == 422
    with TestClient(create_app(reader)) as client:
        state = {"panel": "images", "media_type": "text", "text_name": "debug/output", "text_step": 10}
        saved = client.post("/api/workspace/views", json={"name": "Text", "state": state}).json()
        assert all(saved["state"][key] == value for key, value in state.items())


def test_text_local_source_and_peer_forwarding_preserve_actual_step(recorded, monkeypatch):
    reader, run_id = recorded
    leaf = TestClient(create_app(reader))
    def request(request):
        assert request.url.params["local_only"] == "true"
        reply = leaf.get(request.url.path, params=dict(request.url.params))
        return httpx.Response(reply.status_code, content=reply.content, headers={"Content-Type": "application/json"})
    with httpx.Client(transport=httpx.MockTransport(request)) as peer:
        monkeypatch.setattr(SourceReader, "open", lambda self: peer)
        app = create_app(sources=[Source("folder", "Folder", reader=reader), Source("peer", "Peer", url="http://peer")])
        with TestClient(app) as client:
            for source in ("folder", "peer"):
                query = {"run_id": qualify(source, run_id), "name": "debug/output"}
                assert len(client.get("/api/text-names", params=query).json()) == 2
                assert client.get("/api/text-timeline", params=query).json()["total_steps"] == 3
                value = client.get("/api/text", params={**query, "step": 15}).json()
                assert value["step"] == 10 and value["text"] == "overwritten"
                assert client.get("/api/text", params={**query, "step": -1}).json() is None
