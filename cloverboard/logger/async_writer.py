"""Background batch writer that keeps the training thread off the disk path.

All storage writes (scalars and config updates) are serialized on one daemon
thread, so a single SQLite connection is used by exactly one thread at a time.
"""

import queue
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cloverboard.storage.protocols import AudioRecord, ImageRecord, ScalarRecord, StorageWriter, TextRecord


@dataclass(frozen=True)
class _ScalarBatch:
    records: Sequence[ScalarRecord]


@dataclass(frozen=True)
class _ConfigUpdate:
    delta: Mapping[str, Any]


@dataclass(frozen=True)
class _Stop:
    pass


@dataclass(frozen=True)
class _TagUpdate:
    add: Sequence[str]
    remove: Sequence[str]


_STOP = _Stop()


class AsyncWriteWorker:
    """Commits a scalar batch every flush_interval seconds or batch_size points."""

    def __init__(
        self,
        writer: StorageWriter,
        flush_interval: float = 0.5,
        batch_size: int = 500,
    ) -> None:
        self._writer = writer
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._thread = threading.Thread(
            target=self._run,
            name="cloverboard-writer",
            daemon=True,
        )
        self._pending: list[ScalarRecord] = []
        self._closed = False
        self._closed_lock = threading.Lock()

    def start(self) -> None:
        self._thread.start()

    def put_scalars(self, records: Sequence[ScalarRecord]) -> None:
        if self._closed:
            return
        self._queue.put(_ScalarBatch(records))

    def put_config(self, delta: Mapping[str, Any]) -> None:
        if self._closed:
            return
        self._queue.put(_ConfigUpdate(delta))

    def put_image(self, image: ImageRecord) -> None:
        if not self._closed:
            self._queue.put(image)

    def close(self) -> None:
        """Flush everything queued so far, stop the thread, close the writer."""
        with self._closed_lock:
            if self._closed:
                return
            self._closed = True

        self._queue.put(_STOP)
        if self._thread.is_alive():
            self._thread.join(timeout=10)
        self._writer.flush_and_close()

    def put_audio(self, audio: AudioRecord) -> None:
        if not self._closed:
            self._queue.put(audio)

    def put_tags(self, add: Sequence[str], remove: Sequence[str]) -> None:
        if not self._closed:
            self._queue.put(_TagUpdate(add, remove))

    def put_text(self, text: TextRecord) -> None:
        if not self._closed:
            self._queue.put(text)

    def _run(self) -> None:
        next_flush = time.monotonic() + self._flush_interval

        while True:
            remaining = next_flush - time.monotonic()
            if remaining <= 0:
                self._flush_scalars()
                next_flush = time.monotonic() + self._flush_interval
                remaining = self._flush_interval

            try:
                command = self._queue.get(timeout=remaining)
            except queue.Empty:
                continue

            if isinstance(command, _Stop):
                self._flush_scalars()
                return

            if isinstance(command, _ConfigUpdate):
                self._flush_scalars()
                self._apply_config(command.delta)
                next_flush = time.monotonic() + self._flush_interval
                continue

            if isinstance(command, ImageRecord):
                try:
                    self._writer.append_image(command)
                except Exception:
                    pass
                continue

            if isinstance(command, AudioRecord):
                try:
                    self._writer.append_audio(command)
                except Exception:
                    pass
                continue

            if isinstance(command, _TagUpdate):
                try:
                    self._writer.update_tags(command.add, command.remove)
                except Exception:
                    pass
                continue

            if isinstance(command, TextRecord):
                try:
                    self._writer.append_text(command)
                except Exception:
                    pass
                continue

            self._pending.extend(command.records)
            if len(self._pending) >= self._batch_size:
                self._flush_scalars()
                next_flush = time.monotonic() + self._flush_interval

    def _flush_scalars(self) -> None:
        if not self._pending:
            return
        batch = self._pending
        self._pending = []
        try:
            self._writer.append_scalars(batch)
        except Exception:
            # A failed write must never crash the training loop.
            pass

    def _apply_config(self, delta: Mapping[str, Any]) -> None:
        try:
            self._writer.update_config(delta)
        except Exception:
            pass
