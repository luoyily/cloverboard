"""Allocate a new Run or open an explicitly named experiment directory."""

import os
from datetime import datetime
from pathlib import Path

from cloverboard.storage.paths import resolve_log_path, validate_run_id


def create_run_dir(logdir: Path, started_at: datetime, run_dir: str | None = None) -> tuple[str, Path]:
    """Return (run_id, path), reusing a named directory or allocating a new one.

    Automatically named directories claim their slots eagerly so concurrent
    processes get distinct IDs. Explicit names must stay inside logdir.
    """
    if run_dir is not None:
        validate_run_id(run_dir)
        root = logdir.expanduser().resolve()
        run_path = resolve_log_path(root, run_dir)
        run_path.mkdir(parents=True, exist_ok=True)
        return run_dir, run_path
    logdir.mkdir(parents=True, exist_ok=True)
    prefix = f"run_{started_at:%Y%m%d}_"
    next_index = _next_index(logdir, prefix)

    while True:
        run_id = f"{prefix}{next_index:03d}"
        run_path = logdir / run_id
        try:
            run_path.mkdir()
        except FileExistsError:
            next_index += 1
            continue
        return run_id, run_path


def _next_index(logdir: Path, prefix: str) -> int:
    highest = 0
    for name in os.listdir(logdir):
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix):]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest + 1
