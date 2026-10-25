"""Append-aware TensorBoard decoding; imported only by the optional importer."""

from dataclasses import dataclass
from collections.abc import Iterator
import hashlib
from pathlib import Path
import stat

from google.protobuf.message import DecodeError
from tensorboard.backend.event_processing.event_file_loader import RawEventFileLoader
from tensorboard.compat.proto.event_pb2 import Event
from tensorboard.util.tensor_util import make_ndarray

from cloverboard.metrics import metric_field


@dataclass(frozen=True, slots=True)
class FileStamp:
    device: int
    inode: int
    size: int
    modified_ns: int

    @classmethod
    def read(cls, path: Path) -> "FileStamp":
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"TensorBoard event path is not a regular file: {path}")
        return cls(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    def replaces(self, previous: "FileStamp") -> bool:
        return (
            (self.device, self.inode) != (previous.device, previous.inode)
            or self.size < previous.size
            or (self.size == previous.size and self.modified_ns != previous.modified_ns)
        )


class EventCursor:
    def __init__(self, path: Path):
        self.path = path
        self.stamp: FileStamp | None = None
        self.consumed = 0
        self.incomplete = False
        self._loader = RawEventFileLoader(str(path))

    def read(self, stamp: FileStamp) -> Iterator[Event]:
        for raw in self._loader.Load():
            try:
                event = Event.FromString(raw)
            except DecodeError as error:
                raise ValueError(f"Invalid TensorBoard event protobuf: {self.path}") from error
            self.consumed += len(raw) + 16  # TFRecord length and CRC framing.
            yield event
        # Keep the pre-read stamp: appends racing with EOF must be read next poll.
        self.stamp = stamp
        self.incomplete = self.consumed < stamp.size


def scalar_value(value) -> float | None:
    kind = value.WhichOneof("value")
    if kind == "simple_value":
        return float(value.simple_value)
    if kind == "tensor" and value.metadata.plugin_data.plugin_name in {"", "scalars"}:
        array = make_ndarray(value.tensor)
        if array.size == 1 and array.dtype.kind in "biuf":
            return float(array.reshape(-1)[0])
    return None


def scalar_metric(tag: str) -> str:
    """Preserve canonical paths; reserve one namespace for noncanonical tags.

    The mapping depends only on the original tag, so discovering another tag
    later cannot rename a metric or change collision handling after restart.
    """
    prefix = "tensorboard/tag_"
    try:
        canonical = metric_field(tag).metric
    except ValueError:
        canonical = None
    if canonical == tag and not tag.startswith(prefix):
        return tag
    return prefix + hashlib.sha256(tag.encode("utf-8")).hexdigest()
