import math
import sqlite3

import pytest

from cloverboard.storage.protocols import (
    RunMeta,
    ScalarRecord,
)
from cloverboard.storage.sqlite_backend import SqliteStorageReader, SqliteStorageWriter
from cloverboard.storage.paths import UnsafeLogPathError


def _create_writer(tmp_path, run_id="run_20261022_001"):
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    return SqliteStorageWriter(run_dir / "run.sqlite"), run_dir / "run.sqlite"


def test_writer_reader_roundtrip(tmp_path):
    writer, _ = _create_writer(tmp_path)
    writer.init_run(
        RunMeta(
            run_id="run_20261022_001",
            name="demo",
            started_at=123.0,
            config={"lr": 0.1},
            env={"python_version": "3.11.9"},
        )
    )
    writer.update_config({"bs": 32})
    writer.append_scalars(
        [
            ScalarRecord("train/loss", 1, 10.0, 0.9),
            ScalarRecord("train/loss", 2, 11.0, 0.8),
        ]
    )
    writer.flush_and_close()

    reader = SqliteStorageReader(tmp_path)
    runs = reader.list_runs()

    assert len(runs) == 1
    assert runs[0].run_id == "run_20261022_001"
    assert runs[0].name == "demo"
    assert runs[0].started_at == 123.0
    assert runs[0].config == {"lr": 0.1, "bs": 32}
    assert runs[0].source == "local"

    series = reader.get_scalar_series("run_20261022_001", "train/loss")
    assert [(point.step, point.value) for point in series] == [(1, 0.9), (2, 0.8)]


def test_writer_uses_wal_mode(tmp_path):
    writer, db_path = _create_writer(tmp_path)
    writer.init_run(RunMeta(run_id="r1", name=None, started_at=0.0))
    writer.flush_and_close()

    connection = sqlite3.connect(db_path)
    mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    connection.close()

    assert mode.lower() == "wal"


def test_run_summaries_reuse_unchanged_databases_and_follow_live_wal_updates(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    writer, path = _create_writer(tmp_path)
    run_id = path.parent.name
    writer.init_run(RunMeta(run_id, "live", 100))
    reader = SqliteStorageReader(tmp_path)
    calls = []
    original = reader._read_summary
    def counted(*args):
        calls.append(args[0])
        return original(*args)
    monkeypatch.setattr(reader, "_read_summary", counted)
    with ThreadPoolExecutor(max_workers=2) as pool:
        initial = list(pool.map(lambda _: reader.list_runs(), range(2)))
    assert len(calls) == 1
    assert not initial[0][0].has_scalars
    writer.append_scalars([ScalarRecord("loss", 1, 200, .5)])
    assert reader.list_runs()[0].last_recorded_at == 200
    assert reader.list_runs()[0].has_scalars
    writer.update_config({"batch": 8})
    assert reader.list_runs()[0].config == {"batch": 8}
    writer.update_tags(["fresh"], [])
    assert reader.list_runs()[0].tags == ["fresh"]
    reader.save_run_note(run_id, "new note", 0)
    assert reader.list_runs()[0].note_excerpt == "new note"
    writer.flush_and_close()
    assert reader.list_runs()[0].last_recorded_at == 200
    path.unlink()
    assert reader.list_runs() == []
    assert not reader._summary_cache


def test_batch_series_preserves_prefix_ema_gaps_and_missing_metrics(tmp_path):
    writer, path = _create_writer(tmp_path)
    writer.init_run(RunMeta(path.parent.name, "batch", 100))
    writer.append_scalars([ScalarRecord("loss", step, 100 + step, value) for step, value in enumerate([10, 5, float("nan"), 2, 4, 8])])
    writer.append_scalars([ScalarRecord("val", 0, 100, 1), ScalarRecord("val", 4, 104, 2)])
    writer.flush_and_close()
    result = SqliteStorageReader(tmp_path).get_scalar_series_batch(path.parent.name, ["loss", "val", "missing"], step_min=4, step_max=5, smoothing=.5)
    assert [(p.step, p.value, p.smoothed) for p in result["loss"]] == [(4, 4, 3), (5, 8, 5.5)]
    assert [(p.step, p.smoothed) for p in result["val"]] == [(4, 1.5)]
    assert result["missing"] == []


def test_live_wal_summary_has_a_bounded_age_even_when_file_stamps_repeat(tmp_path, monkeypatch):
    import cloverboard.storage.sqlite_backend as module

    writer, path = _create_writer(tmp_path)
    writer.init_run(RunMeta(path.parent.name, "live", 100))
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module, "_summary_stamp", lambda _: ((1, 1, 1), (1, 100, 2)))
    reader = SqliteStorageReader(tmp_path)
    assert not reader.list_runs()[0].has_scalars
    writer.append_scalars([ScalarRecord("loss", 1, 200, .5)])
    now[0] = 1.1
    assert reader.list_runs()[0].last_recorded_at == 200
    writer.flush_and_close()


def test_duplicate_steps_overwrite_before_ema_and_full_history_statistics(tmp_path):
    writer, path = _create_writer(tmp_path)
    writer.init_run(RunMeta("run_20261022_001", "duplicates", 100))
    writer.append_scalars([ScalarRecord("loss", 0, 100, 10), ScalarRecord("loss", 1, 101, 1)])
    writer.append_scalars([ScalarRecord("loss", 0, 120, 2), ScalarRecord("loss", 1, 121, 3), ScalarRecord("val/loss", 1, 122, .5)])
    writer.flush_and_close()
    reader = SqliteStorageReader(tmp_path)
    assert reader.scalar_count("run_20261022_001", "loss") == 2
    points = reader.get_scalar_series("run_20261022_001", "loss", smoothing=.5)
    assert [(p.value, p.smoothed, p.wall_time) for p in points] == [(2, 2, 120), (3, 2.5, 121)]
    summary = reader.get_scalar_summary("run_20261022_001", "loss", target=1, direction="lte")
    assert summary.minimum == 2 and summary.maximum == 3 and summary.first_hit is None
    assert summary.latest.wall_time == 121
    run = reader.list_runs()[0]
    assert run.has_scalars and run.last_recorded_at == 122
    assert run.time_origin == 100  # Correcting Step 0 does not move the elapsed clock.
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM meta WHERE key='status'").fetchone() is None
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO scalars VALUES ('loss', 0, 130, 4)")


def test_duplicate_overwrite_in_one_batch_survives_reopening(tmp_path):
    writer, path = _create_writer(tmp_path)
    writer.init_run(RunMeta("run_20261022_001", "duplicate batch", 100))
    writer.append_scalars([ScalarRecord("loss", 5, 110, 1), ScalarRecord("loss", 5, 111, 2)])
    writer.flush_and_close()
    reopened = SqliteStorageWriter(path)
    reopened.append_scalars([ScalarRecord("loss", 5, 112, 3)])
    reopened.flush_and_close()
    point = SqliteStorageReader(tmp_path).get_scalar_series("run_20261022_001", "loss")[0]
    assert (point.step, point.value, point.wall_time) == (5, 3, 112)


def test_reader_applies_downsampling(tmp_path):
    writer, _ = _create_writer(tmp_path)
    writer.init_run(RunMeta(run_id="r1", name="demo", started_at=0.0))
    writer.append_scalars(
        [ScalarRecord("loss", step, float(step), 1.0 / (step + 1)) for step in range(5000)]
    )
    writer.flush_and_close()

    reader = SqliteStorageReader(tmp_path)
    series = reader.get_scalar_series("run_20261022_001", "loss", max_points=100)

    assert len(series) <= 102
    assert series[0].step == 0
    assert series[-1].step == 4999


def test_reader_rejects_path_traversal(tmp_path):
    reader = SqliteStorageReader(tmp_path)

    for run_id in ("../outside", "", "."):
        with pytest.raises(UnsafeLogPathError):
            reader.get_scalar_series(run_id, "loss")


def test_reader_ignores_non_run_entries(tmp_path):
    (tmp_path / ".workspace").mkdir()
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "run_without_db").mkdir()

    assert SqliteStorageReader(tmp_path).list_runs() == []


def test_reader_handles_missing_logdir(tmp_path):
    assert SqliteStorageReader(tmp_path / "missing").list_runs() == []


def test_reader_lists_metrics(tmp_path):
    writer, _ = _create_writer(tmp_path)
    writer.init_run(RunMeta(run_id="r1", name="demo", started_at=0.0))
    writer.append_scalars(
        [
            ScalarRecord("val/acc", 1, 0.0, 0.9),
            ScalarRecord("train/loss", 1, 0.0, 0.5),
            ScalarRecord("train/loss", 2, 0.0, 0.4),
        ]
    )
    writer.flush_and_close()

    reader = SqliteStorageReader(tmp_path)

    assert reader.list_metrics("run_20261022_001") == ["train/loss", "val/acc"]
    with pytest.raises(FileNotFoundError, match="Run not found"):
        reader.list_metrics("missing")


def test_nan_values_do_not_drop_the_batch(tmp_path):
    writer, _ = _create_writer(tmp_path)
    writer.init_run(RunMeta(run_id="r1", name="demo", started_at=0.0))
    batch = [
        ScalarRecord("loss", step, 0.0, float("nan") if step == 2 else 0.5)
        for step in range(5)
    ]
    writer.append_scalars(batch)
    writer.flush_and_close()

    series = SqliteStorageReader(tmp_path).get_scalar_series(
        "run_20261022_001", "loss", max_points=10
    )

    assert [point.step for point in series] == [0, 1, 2, 3, 4]
    assert math.isnan(series[2].value)
    assert series[0].value == 0.5


def test_flush_and_close_is_idempotent(tmp_path):
    writer, _ = _create_writer(tmp_path)
    writer.init_run(RunMeta(run_id="r1", name=None, started_at=0.0))

    writer.flush_and_close()
    writer.flush_and_close()


def test_summary_stamp_tracks_newest_record_across_append_types(tmp_path):
    from cloverboard.storage.protocols import AudioRecord, ImageRecord, TextRecord

    writer, _ = _create_writer(tmp_path)
    writer.init_run(RunMeta(run_id="run_20261022_001", name="demo", started_at=1.0))
    writer.append_scalars([ScalarRecord("train/loss", 1, 10.0, 0.9)])
    writer.append_image(ImageRecord("i1", "validation/prediction", 1, 20.0, "imgs/i1.png", 4, 4, "png"))
    writer.append_audio(AudioRecord("a1", "validation/audio", 1, 25.0, "audio/a1.wav", 24000, 1, 24000))
    writer.append_text(TextRecord("debug/output", 1, 30.0, "hello"))
    # An older record after newer ones must not move the stamp backwards.
    writer.append_scalars([ScalarRecord("train/loss", 2, 5.0, 0.8)])
    writer.flush_and_close()

    summary = SqliteStorageReader(tmp_path).list_runs()[0]

    assert summary.last_recorded_at == 30.0
