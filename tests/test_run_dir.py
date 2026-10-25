from datetime import datetime

from cloverboard.logger.run_dir import create_run_dir


def test_run_ids_increment_within_the_same_day(tmp_path):
    started_at = datetime(2026, 10, 22, 12, 0, 0)

    first_id, first_path = create_run_dir(tmp_path, started_at)
    second_id, second_path = create_run_dir(tmp_path, started_at)

    assert first_id == "run_20261022_001"
    assert second_id == "run_20261022_002"
    assert first_path.is_dir()
    assert second_path.is_dir()


def test_non_numeric_suffixes_are_ignored(tmp_path):
    (tmp_path / "run_20261022_backup").mkdir()

    run_id, _ = create_run_dir(tmp_path, datetime(2026, 10, 22))

    assert run_id == "run_20261022_001"


def test_logdir_is_created_when_missing(tmp_path):
    logdir = tmp_path / "nested" / "runs"

    run_id, run_path = create_run_dir(logdir, datetime(2026, 10, 22))

    assert run_path.is_dir()
    assert run_path.parent == logdir
    assert run_id.startswith("run_20261022_")
