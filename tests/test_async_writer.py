import time

from cloverboard.logger.async_writer import AsyncWriteWorker
from cloverboard.storage.protocols import ScalarRecord
from tests.fakes import FakeStorageWriter


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _record(step, value=0.5):
    return ScalarRecord(metric="loss", step=step, wall_time=0.0, value=value)


def test_worker_flushes_on_interval():
    writer = FakeStorageWriter()
    worker = AsyncWriteWorker(writer, flush_interval=0.01)
    worker.start()

    try:
        worker.put_scalars([_record(1)])
        assert _wait_until(lambda: len(writer.scalars) == 1)
    finally:
        worker.close()

    assert writer.closed


def test_worker_flushes_when_batch_is_full():
    writer = FakeStorageWriter()
    worker = AsyncWriteWorker(writer, flush_interval=10.0, batch_size=2)
    worker.start()

    try:
        worker.put_scalars([_record(1), _record(2)])
        assert _wait_until(lambda: len(writer.scalars) == 2, timeout=1.0)
    finally:
        worker.close()


def test_close_flushes_pending_points():
    writer = FakeStorageWriter()
    worker = AsyncWriteWorker(writer, flush_interval=10.0, batch_size=500)
    worker.start()

    worker.put_scalars([_record(1)])
    worker.close()

    assert [record.step for record in writer.scalars] == [1]
    assert writer.closed


def test_config_update_is_applied():
    writer = FakeStorageWriter()
    worker = AsyncWriteWorker(writer, flush_interval=0.01)
    worker.start()

    try:
        worker.put_config({"lr": 0.1})
        assert _wait_until(lambda: writer.config == {"lr": 0.1})
    finally:
        worker.close()


class FlakyWriter(FakeStorageWriter):
    def __init__(self):
        super().__init__()
        self.failures = 1

    def append_scalars(self, batch):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("disk hiccup")
        super().append_scalars(batch)


def test_worker_survives_writer_errors():
    writer = FlakyWriter()
    worker = AsyncWriteWorker(writer, flush_interval=0.01)
    worker.start()

    try:
        worker.put_scalars([_record(1)])
        time.sleep(0.05)
        worker.put_scalars([_record(2)])
        assert _wait_until(lambda: [record.step for record in writer.scalars] == [2])
    finally:
        worker.close()


def test_put_after_close_is_ignored():
    writer = FakeStorageWriter()
    worker = AsyncWriteWorker(writer)

    worker.close()
    worker.put_scalars([_record(1)])
    worker.put_config({"lr": 0.1})

    assert writer.scalars == []
    assert writer.config == {}
