import math
from pathlib import Path

import pytest

pytest.importorskip("tensorboard")
from tensorboard.compat.proto.event_pb2 import Event
from tensorboard.compat.proto.summary_pb2 import Summary, HistogramProto
from tensorboard.compat.proto.tensor_pb2 import TensorProto
from tensorboard.compat.proto.types_pb2 import DT_DOUBLE
from tensorboard.summary.writer.record_writer import RecordWriter
from fastapi.testclient import TestClient

from scripts.import_tensorboard import convert
from cloverboard.server.app import create_app
from cloverboard.storage.sqlite_backend import SqliteStorageReader


def event(tag, number, step=10, wall_time=120):
    return Event(step=step, wall_time=wall_time, summary=Summary(value=[Summary.Value(tag=tag, simple_value=number)]))


def write_events(directory: Path, suffix: str, events):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"events.out.tfevents.{suffix}"
    with path.open("wb") as stream:
        writer = RecordWriter(stream)
        writer.write(Event(file_version="brain.Event:2", wall_time=100).SerializeToString())
        for item in events:
            writer.write(item.SerializeToString())
        writer.flush()
    return path


def test_import_preserves_scalar_history_tensor_precision_and_source_files(tmp_path):
    source, output = tmp_path / "source", tmp_path / "converted"
    first = write_events(source / "experiment", "001", [event("losses/l_pix", 2), event("metrics/Set5/psnr", 30), event("metrics.Set5/psnr", 40)])
    tensor = Summary.Value(tag="exact", tensor=TensorProto(dtype=DT_DOUBLE, double_val=[1.123456789012345]))
    second = write_events(source / "experiment", "002", [event("losses/l_pix", 1, wall_time=130), event("losses/l_pix", .5, step=20, wall_time=140), event("gap", float("nan")), Event(wall_time=150, step=30, summary=Summary(value=[tensor]))])
    original = {path: path.read_bytes() for path in [first, second]}
    report = convert(source, output)
    assert (report["run_count"], report["scalar_count"], report["duplicate_step_count"]) == (1, 6, 1)
    assert report["source_scalar_count"] == 7
    assert report["runs"][0]["tag_counts"]["losses/l_pix"] == 2
    assert report["runs"][0]["source_tag_counts"]["losses/l_pix"] == 3
    assert report["tag_map"]["metrics/Set5/psnr"] == "metrics/Set5/psnr"
    assert len(set(report["tag_map"].values())) == 5
    assert report["tag_map"]["metrics/Set5/psnr"] != report["tag_map"]["metrics.Set5/psnr"]
    assert all(path.read_bytes() == data for path, data in original.items())
    reader = SqliteStorageReader(output / "runs")
    run = reader.list_runs()[0]
    assert run.name == "experiment" and run.started_at == 100
    points = reader.get_scalar_series(run.run_id, "losses/l_pix", 100)
    assert [(p.step, p.value, p.wall_time) for p in points] == [(10, 1, 130), (20, .5, 140)]
    assert reader.get_scalar_series(run.run_id, "exact")[0].value == 1.123456789012345
    assert math.isnan(reader.get_scalar_series(run.run_id, "gap")[0].value)
    client = TestClient(create_app(reader, logdir=output / "runs"))
    response = client.get("/api/metrics", params={"run_id": run.run_id})
    assert response.status_code == 200 and len(response.json()["fields"]) == 5
    assert client.get("/api/scalars", params={"run_id": run.run_id, "metric": "gap"}).json()["points"][0]["value"] is None


def test_empty_and_unsupported_logs_are_visible_in_report(tmp_path):
    source = tmp_path / "source"
    write_events(source / "empty", "001", [])
    histogram = Summary.Value(tag="weights", histo=HistogramProto(min=1, max=2))
    write_events(source / "histograms", "001", [Event(wall_time=110, summary=Summary(value=[histogram]))])
    report = convert(source, tmp_path / "output")
    assert report["run_count"] == report["empty_run_count"] == 2
    assert report["scalar_count"] == 0
    assert report["runs"][1]["unsupported"] == {"histo": 1}
    assert all("empty-log" in run.tags for run in SqliteStorageReader(tmp_path / "output" / "runs").list_runs())


def test_truncated_files_fail_without_publishing_partial_dataset(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    path = write_events(source / "experiment", "001", [event("loss", 1)])
    path.write_bytes(path.read_bytes()[:-2])
    with pytest.raises(ValueError, match="Truncated or corrupt"):
        convert(source, output)
    assert not output.exists()


def test_import_rejects_existing_destination_and_overlapping_paths(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    write_events(source, "001", [event("loss", 1)])
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(ValueError, match="already exists"):
        convert(source, output)
    assert sentinel.read_text() == "keep"
    with pytest.raises(ValueError, match="must not overlap"):
        convert(source, source / "output")
