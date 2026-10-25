import time

from cloverboard import init, log, log_config
from cloverboard.logger.api import _shutdown
from cloverboard.logger.context import get_context
from cloverboard.storage.sqlite_backend import SqliteStorageReader


def test_sdk_writes_run_end_to_end(tmp_path):
    init(exp_name="exp", logdir=tmp_path, config={"lr": 0.1}, capture_hardware=False)

    context = get_context()
    assert context is not None
    assert context.run_id.startswith("run_")
    assert (tmp_path / context.run_id / "run.sqlite").is_file()

    log_config({"bs": 32})
    log({"train/loss": 0.5}, step=1)
    _shutdown()

    reader = SqliteStorageReader(tmp_path)
    runs = reader.list_runs()

    assert len(runs) == 1
    assert runs[0].run_id == context.run_id
    assert runs[0].name == "exp"
    assert runs[0].config == {"lr": 0.1, "bs": 32}
    series = reader.get_scalar_series(runs[0].run_id, "train/loss")
    assert [(point.step, point.value) for point in series] == [(1, 0.5)]


def test_second_init_creates_a_new_run(tmp_path):
    init(logdir=tmp_path, capture_hardware=False)
    first_run_id = get_context().run_id
    init(logdir=tmp_path, capture_hardware=False)
    second_run_id = get_context().run_id

    assert first_run_id != second_run_id
    assert (tmp_path / first_run_id).is_dir()
    assert (tmp_path / second_run_id).is_dir()


def test_log_is_fast(tmp_path):
    init(logdir=tmp_path, capture_hardware=False)

    started = time.perf_counter()
    for step in range(1000):
        log({"train/loss": 1.0}, step=step)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5


def test_log_ignores_non_numeric_values(tmp_path):
    init(logdir=tmp_path, capture_hardware=False)

    log({"loss": object(), "acc": 0.9}, step=1)
    _shutdown()

    runs = SqliteStorageReader(tmp_path).list_runs()
    series = SqliteStorageReader(tmp_path).get_scalar_series(runs[0].run_id, "acc")

    assert [point.value for point in series] == [0.9]


def test_init_failure_does_not_raise(tmp_path):
    blocked = tmp_path / "runs"
    blocked.write_text("not a directory", encoding="utf-8")

    init(logdir=blocked, capture_hardware=False)

    context = get_context()
    assert context is not None
    assert context.worker is None

    log({"loss": 0.5}, step=1)
    log_config({"lr": 0.1})
