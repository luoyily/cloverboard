"""Plain text records share the scalar/config write queue; no separate media files."""

import time

from cloverboard.logger.context import get_context
from cloverboard.logger.images import _warning
from cloverboard.storage.protocols import TextRecord

MAX_TEXT_BYTES = 256 * 1024


def log_text(name: str, text: str, step: int) -> None:
    """Record a plain string (up to 256 KiB UTF-8); the same name/Step overwrites.

    Text is preserved verbatim, including newlines and empty strings. Invalid
    inputs warn and skip the record without interrupting training.
    """
    context = get_context()
    if context is None or context.worker is None:
        return
    try:
        if not isinstance(name, str) or not name.strip() or len(name) > 500:
            raise ValueError("Use a nonempty name up to 500 characters")
        if not isinstance(step, int) or isinstance(step, bool) or abs(step) > 2**53 - 1:
            raise ValueError("Use a safe integer step")
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("Use a plain string up to 256 KiB UTF-8")
        context.worker.put_text(TextRecord(name, step, time.time(), text))
    except Exception as error:
        _warning(f"Cloverboard text skipped: {error}")
