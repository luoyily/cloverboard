import json
import socket
import sqlite3

import numpy as np
import pytest

from scripts.preview import preview_lock, reserve_ports, seed_workspace
from scripts.preview_data import RUNS, metric_rng, prepare_data, scalar_values
from cloverboard.server.workspace import WorkspaceStore
from cloverboard.storage.sqlite_backend import SqliteStorageReader


def test_seeded_metrics_are_reproducible_noisy_and_bounded():
    first = scalar_values(42, RUNS[0], 1000)
    repeated = scalar_values(42, RUNS[0], 1000)
    other = scalar_values(73, RUNS[0], 1000)
    assert len(first) == 9
    for metric, values in first.items():
        np.testing.assert_array_equal(values, repeated[metric])
    assert not np.array_equal(first["train/loss"], other["train/loss"])
    assert np.count_nonzero(np.diff(first["train/loss"]) > 0) > 100
    assert first["train/loss"].min() > 0
    for metric in ("train/accuracy", "validation/score", "validation/precision"):
        assert 0 <= first[metric].min() <= first[metric].max() <= 1
    assert np.isnan(scalar_values(42, RUNS[3], 1000)["diagnostics/residual"]).sum() == 1
    # Metric streams are derived from identity rather than consumption order.
    before = metric_rng(42, RUNS[0], "validation/score").normal(size=10)
    metric_rng(42, RUNS[0], "unrelated/new_metric").normal(size=500)
    np.testing.assert_array_equal(before, metric_rng(42, RUNS[0], "validation/score").normal(size=10))


def test_preview_named_targets_and_views_are_seeded_once(tmp_path):
    seed_workspace(tmp_path)
    store = WorkspaceStore(tmp_path / "data" / "main")
    snapshot = store.snapshot()
    targets = snapshot.state.targets["validation/score"]
    assert snapshot.state.metric_columns == ["train/loss", "validation/score"]
    assert targets.active_id == "small"
    assert [(target.name, target.chart_label, target.value) for target in targets.items] == [
        ("Small model", "S", .9), ("Large model", "L", .95),
    ]
    assert all(view.state.targets == snapshot.state.targets for view in snapshot.views)
    assert len(RUNS[1].name) > 40
    seed_workspace(tmp_path)
    assert store.snapshot() == snapshot


def test_shared_fixture_uses_sdk_and_reuses_notes_and_views(tmp_path):
    directory = tmp_path / "fixture"
    manifest = prepare_data(directory, "default", 42)
    assert manifest["complete"]
    main = directory / "data" / "main"
    reader = SqliteStorageReader(main)
    assert len(reader.list_runs()) == 3
    assert len(reader.list_metrics("baseline")) == 9
    assert len(reader.get_scalar_series("baseline", "train/loss", max_points=2000)) == 1000
    assert len(reader.get_scalar_series("baseline", "validation/score", max_points=2000)) == 101
    assert reader.image_names("baseline")[0].count == 8
    assert reader.image_timeline("baseline", "validation/prediction").steps == [0, 333, 666, 999]
    assert reader.get_run_details("baseline").snapshot["status"] == "complete"
    partial = reader.get_scalar_series("partial-75", "train/loss", max_points=2000)
    assert len(partial) == 750
    assert partial[0].step == 0 and partial[-1].step == 749
    partial_run = next(run for run in reader.list_runs() if run.run_id == "partial-75")
    assert partial_run.config["planned_steps"] == 1000
    assert partial_run.config["recorded_steps"] == 750
    assert reader.image_timeline("partial-75", "validation/prediction").steps == [0, 250, 500, 749]
    assert "750 / 1000 planned steps (75%)" in reader.get_run_note("partial-75").body
    with sqlite3.connect(main / "partial-75" / "run.sqlite") as connection:
        bounds = connection.execute("SELECT metric, MAX(step) FROM scalars GROUP BY metric").fetchall()
    assert len(bounds) == 9 and all(last_step == 749 for _, last_step in bounds)
    partial_spec = next(run for run in RUNS if run.run_id == "partial-75")
    np.testing.assert_allclose([point.value for point in partial], scalar_values(42, partial_spec, 1000)["train/loss"][:750])
    note = reader.get_run_note("baseline")
    reader.save_run_note("baseline", "Keep my preview observations", note.revision)
    seed_workspace(directory)
    store = WorkspaceStore(main)
    assert len(store.snapshot().views) == 2
    assert "partial-75" in store.snapshot().state.selected_ids
    detail = next(view for view in store.snapshot().views if view.name == "Validation detail")
    assert detail.state.expanded_charts == ["validation/score"]
    assert prepare_data(directory, "default", 42) == manifest
    seed_workspace(directory)
    assert reader.get_run_note("baseline").body == "Keep my preview observations"
    assert len(store.snapshot().views) == 2
    with pytest.raises(RuntimeError, match="--reset"):
        prepare_data(directory, "default", 73)
    assert reader.get_run_note("baseline").body == "Keep my preview observations"
    with sqlite3.connect(main / "baseline" / "run.sqlite") as connection:
        values = connection.execute("SELECT step, value FROM scalars WHERE metric='train/loss' ORDER BY step").fetchall()
    np.testing.assert_allclose([value for _, value in values], scalar_values(42, RUNS[0], 1000)["train/loss"])


def test_preview_reset_rejects_unowned_directories_and_links(tmp_path, directory_link):
    unknown = tmp_path / "unknown"
    unknown.mkdir()
    keep = unknown / "keep.txt"
    keep.write_text("real data", encoding="utf8")
    with pytest.raises(RuntimeError, match="manifest"):
        prepare_data(unknown, "default", 42, reset=True)
    assert keep.read_text(encoding="utf8") == "real data"
    link = tmp_path / "linked"
    directory_link(link, unknown)
    with pytest.raises(RuntimeError, match="link or junction"):
        prepare_data(link, "default", 42, reset=True)
    assert keep.is_file()


def test_preview_lock_and_port_conflicts_are_explicit(tmp_path):
    with preview_lock(tmp_path / "preview"):
        with pytest.raises(RuntimeError, match="Another preview"):
            with preview_lock(tmp_path / "preview"):
                pass
    with preview_lock(tmp_path / "preview"):
        pass
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        if port > 65533:
            pytest.skip("Ephemeral port is outside the supported base-port range")
        with pytest.raises(RuntimeError, match="unavailable"):
            reserve_ports(port)
