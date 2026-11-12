"""Concurrency regressions for the SQLite read path.

The server runs sync endpoints on a fixed anyio worker pool. When every read
opens and closes its own connection, the pool serializes on SQLite's
process-global VFS lock; these tests keep that failure from coming back.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import cloverboard.storage.sqlite_backend as module
from cloverboard.storage.protocols import RunMeta, ScalarRecord
from cloverboard.storage.sqlite_backend import SqliteStorageReader, SqliteStorageWriter


def _create_run(root, run_id, metric="loss", steps=8):
    run_dir = root / run_id
    run_dir.mkdir()
    writer = SqliteStorageWriter(run_dir / "run.sqlite")
    writer.init_run(RunMeta(run_id=run_id, name=run_id, started_at=100.0))
    writer.append_scalars([ScalarRecord(metric, step, 100.0 + step, float(step)) for step in range(steps)])
    writer.flush_and_close()
    return run_id


def test_concurrent_reads_share_one_connection_per_run(tmp_path, monkeypatch):
    run_id = _create_run(tmp_path, "run_20261109_001")
    reader = SqliteStorageReader(tmp_path)
    reader.get_scalar_summary(run_id, "loss")  # Warm the connection cache.

    opened = []
    real_connect = module.sqlite3.connect

    def counting_connect(*args, **kwargs):
        opened.append(args[0] if args else kwargs.get("database"))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(module.sqlite3, "connect", counting_connect)
    with ThreadPoolExecutor(max_workers=16) as pool:
        summaries = list(pool.map(lambda _: reader.get_scalar_summary(run_id, "loss"), range(64)))

    assert opened == [], f"expected no new connections, opened {len(opened)}"
    assert all(summary.latest.step == 7 for summary in summaries)


def test_catalog_scan_does_not_hold_its_lock_across_summary_reads(tmp_path, monkeypatch):
    run_id = _create_run(tmp_path, "run_20261109_002")
    reader = SqliteStorageReader(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original = reader._read_summary

    def slow_summary(*args):
        entered.set()
        assert release.wait(5.0)
        return original(*args)

    monkeypatch.setattr(reader, "_read_summary", slow_summary)
    results = []
    worker = threading.Thread(target=lambda: results.append(reader.list_runs()))
    worker.start()
    try:
        assert entered.wait(5.0)
        assert not reader._runs_lock.locked()
        # An unrelated run stays readable while the scan is blocked on I/O.
        assert reader.get_scalar_summary(run_id, "loss").latest.step == 7
    finally:
        release.set()
        worker.join(5.0)

    assert [run.run_id for run in results[0]] == [run_id]


def test_scalar_sampling_runs_concurrently_across_runs(tmp_path, monkeypatch):
    first = _create_run(tmp_path, "run_20261109_003")
    second = _create_run(tmp_path, "run_20261109_004")
    reader = SqliteStorageReader(tmp_path)
    reader.get_scalar_series_batch(first, ["loss"])
    reader.get_scalar_series_batch(second, ["loss"])

    barrier = threading.Barrier(2, timeout=5.0)
    original = module._sample_scalar_rows

    def synchronized(*args):
        barrier.wait()
        return original(*args)

    monkeypatch.setattr(module, "_sample_scalar_rows", synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reader.get_scalar_series_batch, run_id, ["loss"]) for run_id in (first, second)]
        results = [future.result(timeout=10.0) for future in futures]

    assert all(result["loss"] for result in results)


def test_cached_connection_keeps_no_transaction_and_sees_later_writes(tmp_path):
    run_id = _create_run(tmp_path, "run_20261109_005")
    reader = SqliteStorageReader(tmp_path)
    reader.get_scalar_series_batch(run_id, ["loss"])

    (connection,) = [entry.connection for entry in reader._connections.values()]
    assert connection.in_transaction is False

    writer = SqliteStorageWriter(tmp_path / run_id / "run.sqlite")
    writer.append_scalars([ScalarRecord("loss", 8, 108.0, 8.0)])
    writer.flush_and_close()

    assert reader.get_scalar_summary(run_id, "loss").latest.step == 8
