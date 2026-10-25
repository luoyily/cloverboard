"""Run directory allocation: run_YYYYMMDD_NNN with collision auto-increment."""

import os
from datetime import datetime
from pathlib import Path


def create_run_dir(logdir: Path, started_at: datetime) -> tuple[str, Path]:
    """Create a unique run directory and return (run_id, path).

    The directory is created eagerly to claim its slot, so concurrent
    processes starting on the same day still get distinct ids.
    """
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
