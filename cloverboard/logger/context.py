"""Global run context shared by all SDK calls."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from cloverboard.logger.images import ImageWorker
    from cloverboard.logger.audio import AudioWorker

from cloverboard.logger.async_writer import AsyncWriteWorker
from cloverboard.storage.protocols import StorageWriter


@dataclass
class RunContext:
    """State of the active run; one process tracks at most one experiment."""

    run_id: str
    logdir: Path
    exp_name: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    writer: StorageWriter | None = None
    worker: AsyncWriteWorker | None = None
    image_worker: "ImageWorker | None" = None
    audio_worker: "AudioWorker | None" = None
    workspace_root: Path | None = None


_context: RunContext | None = None


def get_context() -> RunContext | None:
    """Return the active run context, or None before init()."""
    return _context


def set_context(context: RunContext | None) -> None:
    """Replace the active run context; None clears it."""
    global _context
    _context = context
