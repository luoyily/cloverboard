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

from cloverboard import init, log
from cloverboard.cli import main
from cloverboard.importers.tensorboard.events import EventCursor, scalar_metric
from cloverboard.importers.tensorboard.importer import TensorBoardImporter, discover_runs
from cloverboard.logger.api import _shutdown
from cloverboard.server.app import create_app
from cloverboard.storage.sqlite_backend import SqliteStorageReader, SqliteStorageWriter


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


def append_events(path, events):
    with path.open("ab") as stream:
        writer = RecordWriter(stream)
        for item in events:
            writer.write(item.SerializeToString())
        writer.flush()


def points(output, run="experiment", metric="loss"):
    return [(p.step, p.value, p.wall_time) for p in SqliteStorageReader(output).get_scalar_series(run, metric, 10000)]


def test_standard_dataset_preserves_precision_paths_gaps_and_source(tmp_path):
    source, output = tmp_path / "source", tmp_path / "converted"
    first = write_events(source / "experiment", "001", [event("loss", 2), event("metrics/Set5/psnr", 30)])
    tensor = Summary.Value(tag="exact", tensor=TensorProto(dtype=DT_DOUBLE, double_val=[1.123456789012345]))
    second = write_events(source / "experiment", "002", [
        event("loss", 1, wall_time=130), event("loss", .5, step=20, wall_time=140),
        event("gap", float("nan")), Event(wall_time=150, step=30, summary=Summary(value=[tensor])),
    ])
    original = {path: path.read_bytes() for path in [first, second]}
    with TensorBoardImporter(source, output) as importer:
        result = importer.poll(allow_incomplete=False)
    assert (result.runs, result.files, result.scalars) == (1, 2, 6)
    assert all(path.read_bytes() == data for path, data in original.items())
    reader = SqliteStorageReader(output)
    run = reader.list_runs()[0]
    assert run.run_id == run.name == "experiment" and run.started_at == 100
    assert points(output) == [(10, 1, 130), (20, .5, 140)]
    assert reader.get_scalar_series(run.run_id, "exact")[0].value == 1.123456789012345
    assert math.isnan(reader.get_scalar_series(run.run_id, "gap")[0].value)
    assert not (output / "runs").exists()
    with TestClient(create_app(reader, logdir=output), base_url="http://127.0.0.1") as client:
        fields = client.get("/api/metrics", params={"run_id": run.run_id}).json()["fields"]
        assert any(field["metric"] == "metrics/Set5/psnr" for field in fields)
        assert client.get("/api/scalars", params={"run_id": run.run_id, "metric": "gap"}).json()["points"][0]["value"] is None


def test_poll_reads_only_appends_and_enforces_filename_precedence(tmp_path, monkeypatch):
    source, output = tmp_path / "source", tmp_path / "output"
    older = write_events(source / "experiment", "001", [event("loss", 2)])
    newer = write_events(source / "experiment", "002", [event("loss", 1)])
    reads = []
    original_read = EventCursor.read

    def counted_read(self, stamp):
        reads.append((self.path.name, self.consumed))
        yield from original_read(self, stamp)

    monkeypatch.setattr(EventCursor, "read", counted_read)
    with TensorBoardImporter(source, output) as importer:
        importer.poll()
        reads.clear()
        idle = importer.poll()
        assert (idle.changed_files, idle.events, idle.written) == (0, 0, 0)
        assert not reads
        append_events(older, [event("loss", 9), event("loss", .8, step=20)])
        result = importer.poll()
        assert result.scalars == 2 and result.written == 1
        assert reads[0][0] == older.name and reads[0][1] > 0
        assert points(output) == [(10, 1, 120), (20, pytest.approx(.8), 120)]
        append_events(newer, [event("loss", .7, step=20), event("loss", .6, step=20)])
        write_events(source / "new-experiment", "001", [event("loss", 4)])
        assert importer.poll().runs == 2
        expected = points(output)
    with TensorBoardImporter(source, output) as importer:
        importer.poll()
    assert points(output) == expected
    assert expected[-1][1] == pytest.approx(.6)


def test_partial_tail_waits_and_resumes_without_replaying_completed_records(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    path = write_events(source / "experiment", "001", [event("loss", 2)])
    extra = write_events(tmp_path / "extra", "001", [event("loss", 1, step=20)])
    raw = extra.read_bytes()
    header_length = len(Event(file_version="brain.Event:2", wall_time=100).SerializeToString()) + 16
    record = raw[header_length:]
    with TensorBoardImporter(source, output) as importer:
        importer.poll()
        with path.open("ab") as stream:
            stream.write(record[:11])
        result = importer.poll()
        assert result.scalars == 0 and result.incomplete_files == ["experiment/" + path.name]
        assert importer.poll().changed_files == 0
        with path.open("ab") as stream:
            stream.write(record[11:])
        result = importer.poll()
        assert result.scalars == 1 and result.incomplete_files == []
        assert points(output) == [(10, 2, 120), (20, 1, 120)]


def test_one_shot_rejects_incomplete_file_without_publishing_new_run(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    path = write_events(source / "experiment", "001", [event("loss", 1)])
    path.write_bytes(path.read_bytes()[:-2])
    with TensorBoardImporter(source, output) as importer:
        with pytest.raises(ValueError, match="Incomplete or corrupt"):
            importer.poll(allow_incomplete=False)
    assert not (output / "experiment" / "run.sqlite").exists()
    assert not list((output / "experiment").iterdir())


def test_replacement_and_deletion_replay_one_run_and_keep_historical_points(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    older = write_events(source / "experiment", "001", [event("loss", 2)])
    newer = write_events(source / "experiment", "002", [event("loss", 1), event("loss", .5, step=20)])
    with TensorBoardImporter(source, output) as importer:
        importer.poll()
        replacement = write_events(tmp_path / "replacement", "001", [event("loss", 3)])
        replacement.replace(newer)
        assert importer.poll().replayed_runs == 1
        assert points(output) == [(10, 3, 120), (20, .5, 120)]
        newer.unlink()
        assert importer.poll().replayed_runs == 1
        assert points(output) == [(10, 2, 120), (20, .5, 120)]
        older.unlink()
        assert importer.poll().runs == 0
        assert len(points(output)) == 2
    with TensorBoardImporter(source, output) as importer:
        assert importer.poll().files == 0
    assert len(points(output)) == 2


def test_restart_preserves_metadata_user_edits_and_native_sdk_handoff(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    path = write_events(source / "experiment", "001", [event("loss", 2)])
    with TensorBoardImporter(source, output) as importer:
        importer.poll()
    reader = SqliteStorageReader(output)
    writer = SqliteStorageWriter(output / "experiment" / "run.sqlite")
    writer.update_config({"lr": .01})
    writer.flush_and_close()
    reader.update_run_tags("experiment", ["reviewed"], ["tensorboard-import"])
    reader.save_run_note("experiment", "Keep this note", 0)
    with TestClient(create_app(reader, logdir=output), base_url="http://127.0.0.1") as client:
        assert client.post("/api/workspace/views", json={"name": "Saved", "state": {}}).status_code == 201
    original_details = reader.get_run_details("experiment")
    append_events(path, [event("loss", 1, step=20)])
    with TensorBoardImporter(source, output) as importer:
        importer.poll()
    assert reader.get_run_details("experiment") == original_details
    assert reader.list_runs()[0].tags == ["reviewed"]
    assert reader.get_run_note("experiment").body == "Keep this note"
    init(logdir=output, run_dir="experiment", exp_name="ignored", capture_hardware=False, config={"batch": 8})
    log({"loss": .75}, step=20)
    log({"loss": .5}, step=30)
    _shutdown()
    assert reader.list_runs()[0].name == "experiment"
    assert reader.get_run_details("experiment").config == {"lr": .01, "batch": 8}
    assert reader.get_run_details("experiment").env == original_details.env
    assert [(step, value) for step, value, _ in points(output)] == [(10, 2), (20, .75), (30, .5)]
    with TestClient(create_app(reader, logdir=output), base_url="http://127.0.0.1") as client:
        assert client.get("/api/workspace").json()["views"][0]["name"] == "Saved"


def test_empty_and_non_scalar_runs_and_root_events(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    write_events(source, "001", [])
    histogram = Summary.Value(tag="weights", histo=HistogramProto(min=1, max=2))
    write_events(source / "histograms", "001", [Event(wall_time=110, summary=Summary(value=[histogram]))])
    with TensorBoardImporter(source, output) as importer:
        result = importer.poll()
    assert (result.runs, result.scalars, result.unsupported) == (2, 0, {"histo": 1})
    assert {run.run_id for run in SqliteStorageReader(output).list_runs()} == {"source", "histograms"}


def test_empty_source_discovers_new_run_and_in_place_truncation_replays(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    with TensorBoardImporter(source, output) as importer:
        assert importer.poll().runs == 0
        path = write_events(source / "experiment", "001", [event("loss", 2), event("loss", 1, step=20)])
        assert importer.poll().scalars == 2
        write_events(path.parent, "001", [event("loss", 3)])
        assert importer.poll().replayed_runs == 1
        assert points(output) == [(10, 3, 120), (20, 1, 120)]


def test_output_lock_source_binding_and_native_collision(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    write_events(source / "experiment", "001", [event("loss", 2)])
    with TensorBoardImporter(source, output):
        with pytest.raises(ValueError, match="Another TensorBoard importer"):
            TensorBoardImporter(source, output)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="another TensorBoard source"):
        TensorBoardImporter(other, output)
    init(logdir=output, run_dir="experiment", capture_hardware=False)
    log({"loss": 4}, step=10)
    _shutdown()
    with TensorBoardImporter(source, output) as importer:
        with pytest.raises(ValueError, match="was not imported"):
            importer.poll()
    assert points(output)[0][1] == 4


@pytest.mark.parametrize("destination", ["source", "source/child", "."])
def test_overlapping_paths_are_rejected(tmp_path, destination):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="must not overlap"):
        TensorBoardImporter(source, tmp_path / destination)


@pytest.mark.parametrize("directory, message", [("parent/child", "Nested TensorBoard Run"), (".hidden", "Invalid run path")])
def test_unsupported_run_paths_fail_explicitly(tmp_path, directory, message):
    write_events(tmp_path / directory, "001", [])
    with pytest.raises(ValueError, match=message):
        discover_runs(tmp_path.resolve())


def test_root_and_child_name_collision_and_linked_directories(tmp_path, directory_link):
    source = tmp_path / "experiment"
    write_events(source, "001", [])
    write_events(source / "experiment", "001", [])
    with pytest.raises(ValueError, match="names collide"):
        discover_runs(source)
    elsewhere = tmp_path / "linked-source"
    write_events(elsewhere / "run", "001", [])
    directory_link(elsewhere / "run" / "cycle", elsewhere)
    assert len(discover_runs(elsewhere)) == 1


def test_tag_mapping_is_independent_of_discovery_order():
    tags = ["loss", "metrics/Set5/psnr", " metrics/Set5/psnr ", "a//b", "", "tensorboard/tag_example"]
    mapping = {tag: scalar_metric(tag) for tag in tags}
    assert mapping["loss"] == "loss"
    assert mapping["metrics/Set5/psnr"] == "metrics/Set5/psnr"
    assert len(set(mapping.values())) == len(tags)
    assert {tag: scalar_metric(tag) for tag in reversed(tags)} == mapping


def test_cli_one_shot_follow_shutdown_and_invalid_intervals(tmp_path, monkeypatch, capsys):
    source, output = tmp_path / "source", tmp_path / "output"
    path = write_events(source / "experiment", "001", [event("loss", 2)])
    main(["import-tensorboard", str(source), "--output", str(output)])
    assert "scalar records: 1" in capsys.readouterr().out
    append_events(path, [event("loss", 1, step=20)])

    def stop_polling(interval):
        assert interval == .1
        raise KeyboardInterrupt

    monkeypatch.setattr("cloverboard.importers.tensorboard.cli.time.sleep", stop_polling)
    main(["import-tensorboard", str(source), "--output", str(output), "--follow", "--interval", ".1"])
    assert "import stopped" in capsys.readouterr().out
    assert len(points(output)) == 2
    with TensorBoardImporter(source, output):
        pass  # Ctrl+C released the process lock.
    for interval in ["0", "-1", "nan", "inf"]:
        with pytest.raises(SystemExit) as error:
            main(["import-tensorboard", str(source), "--output", str(output), "--interval", interval])
        assert error.value.code == 2


def test_imported_runs_carry_the_last_recorded_stamp(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    write_events(source / "experiment", "001", [event("loss", 2), event("loss", 1, step=20, wall_time=140)])
    with TensorBoardImporter(source, output) as importer:
        importer.poll()
    summary = SqliteStorageReader(output).list_runs()[0]
    assert summary.last_recorded_at == 140
