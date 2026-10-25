import sqlite3

import pytest

from cloverboard import init, log, log_config, log_text
from cloverboard.logger.api import _shutdown
from cloverboard.logger.context import get_context
from cloverboard.storage.sqlite_backend import SqliteStorageReader


def test_named_run_resumes_preserving_identity_metadata_and_records(tmp_path, monkeypatch):
    captures = []
    monkeypatch.setattr("cloverboard.logger.api.collect_environment", lambda **kwargs: captures.append(kwargs) or {"original": True})
    run_path = tmp_path / "experiment"
    run_path.mkdir()
    checkpoint = run_path / "checkpoint.txt"
    checkpoint.write_text("keep")
    init(logdir=tmp_path, run_dir="experiment", exp_name="First name", config={"lr": .1, "batch": 8}, tags=["old"])
    log({"loss": 2}, step=10)
    log_text("message", "first", step=10)
    _shutdown()
    reader = SqliteStorageReader(tmp_path)
    original = reader.list_runs()[0]
    reader.update_run_tags("experiment", ["reviewed"], ["old"])
    reader.save_run_note("experiment", "Keep my note", 0)

    init(logdir=tmp_path, run_dir="experiment", exp_name="Do not rename", config={"lr": .01}, tags=["new"])
    assert get_context().config == {"lr": .01, "batch": 8}
    assert get_context().exp_name == "First name"
    log_config({"epochs": 30})
    log({"loss": 1}, step=10)
    log({"loss": .5}, step=20)
    log_text("message", "resumed", step=20)
    _shutdown()
    assert len(captures) == 1
    current = reader.list_runs()[0]
    assert (current.run_id, current.name, current.started_at) == ("experiment", "First name", original.started_at)
    assert current.config == {"lr": .01, "batch": 8, "epochs": 30}
    assert current.tags == ["new", "reviewed"]
    assert reader.get_run_details("experiment").env == {"original": True}
    assert reader.get_run_note("experiment").body == "Keep my note"
    assert [(p.step, p.value) for p in reader.get_scalar_series("experiment", "loss")] == [(10, 1), (20, .5)]
    assert reader.get_text("experiment", "message", 10).text == "first"
    assert reader.get_text("experiment", "message", 20).text == "resumed"
    assert checkpoint.read_text() == "keep"


def test_named_run_without_explicit_updates_keeps_config_and_tags(tmp_path):
    init(logdir=tmp_path, run_dir="same", config={"lr": .1}, tags=["reviewed"], capture_hardware=False)
    _shutdown()
    init(logdir=tmp_path, run_dir="same", capture_hardware=False)
    assert get_context().config == {"lr": .1}
    _shutdown()
    assert SqliteStorageReader(tmp_path).list_runs()[0].tags == ["reviewed"]


@pytest.mark.parametrize("run_dir", ["", ".hidden", "../escape", "parent/child", "parent\\child", "C:\\outside"])
def test_invalid_run_directory_warns_without_allocating_another_run(tmp_path, run_dir):
    with pytest.warns(RuntimeWarning, match="could not initialize logging"):
        init(logdir=tmp_path, run_dir=run_dir, capture_hardware=False)
    assert get_context().worker is None
    assert list(tmp_path.iterdir()) == []


def test_existing_unrelated_database_is_untouched(tmp_path):
    directory = tmp_path / "existing"
    directory.mkdir()
    db = directory / "run.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES ('keep')")
    before = db.read_bytes()
    with pytest.warns(RuntimeWarning, match="Unsupported Run database schema"):
        init(logdir=tmp_path, run_dir="existing", capture_hardware=False)
    assert get_context().worker is None
    assert db.read_bytes() == before
    assert not list(tmp_path.glob("run_*"))


def test_named_run_directory_cannot_escape_logdir_via_link(tmp_path, directory_link):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    directory_link(root / "experiment", outside)
    with pytest.warns(RuntimeWarning, match="outside the log directory"):
        init(logdir=root, run_dir="experiment", capture_hardware=False)
    assert get_context().worker is None
    assert not list(outside.iterdir())
