"""Public SDK API: init / log / log_config.

Every call degrades to a no-op instead of raising, so that logging can never
interrupt a training run.
"""

import atexit
import os
import time
import warnings
from collections.abc import Mapping
from datetime import datetime
from numbers import Real
from pathlib import Path
from typing import Any

from cloverboard.logger.async_writer import AsyncWriteWorker
from cloverboard.logger.config import normalize_config
from cloverboard.logger.context import RunContext, get_context, set_context
from cloverboard.logger.environment import collect_environment
from cloverboard.logger.run_dir import create_run_dir
from cloverboard.logger.paths import relative_metadata
from cloverboard.storage.paths import resolve_log_path
from cloverboard.storage.protocols import RunMeta, ScalarRecord
from cloverboard.storage.sqlite_backend import SqliteStorageWriter, read_run_meta
from cloverboard.metrics import metric_field
from cloverboard.tags import normalize_tags


def init(
    exp_name: str | None = None,
    logdir: str | Path = "./runs",
    config: Any = None,
    capture_git: bool = True,
    capture_stdout: bool = True,
    capture_hardware: bool = True,
    tags: list[str] | None = None,
    workspace_root: str | Path | None = None,
    *,
    run_dir: str | None = None,
) -> None:
    """Start or continue a run (one global singleton per process).

    Opens the directory, initializes new Run metadata or merges explicit
    updates into an existing Run, then starts the background batch writer.

    Params:
        exp_name: display name in the UI; generated when omitted.
        logdir: root directory holding all run folders and .workspace state.
        run_dir: immediate subdirectory name. Create it when absent or append
            to its existing Run database; omitted means allocate a new Run.
            Resuming preserves name, start time and environment. Explicit
            config keys merge and supplied tags are added incrementally.
        config: any common config object (Namespace / dict / dataclass / YAML
            path); equivalent to calling log_config() right after init.
        capture_git / capture_stdout: compatibility arguments; no capture is
            performed. Use snapshot_code() for an explicit source archive.
        capture_hardware: query GPU names via nvidia-smi at startup.
        workspace_root: optional base for absolute paths recorded in config,
            argv and snapshot metadata. Paths outside it remain unchanged.

    Exceptions: never raised to the caller; failed initialization warns and
    disables logging. Training checkpoint and Step restoration are caller-owned.
    """
    _shutdown()
    try:
        _start_run(
            exp_name=exp_name,
            logdir=logdir,
            config=config,
            capture_hardware=capture_hardware,
            tags=normalize_tags(tags or []),
            workspace_root=workspace_root,
            run_dir=run_dir,
        )
    except Exception as error:
        # Logging must never break the training loop.
        set_context(RunContext(run_id="", logdir=Path(logdir), exp_name=exp_name))
        try:
            warnings.warn(f"CloverBoard could not initialize logging: {error}", RuntimeWarning, stacklevel=2)
        except Warning:
            pass


def _start_run(
    exp_name: str | None,
    logdir: str | Path,
    config: Any,
    capture_hardware: bool,
    tags: list[str],
    workspace_root: str | Path | None,
    run_dir: str | None,
) -> None:
    logdir_path = Path(logdir).expanduser()
    started_at = time.time()
    run_id, _ = create_run_dir(
        logdir_path,
        datetime.fromtimestamp(started_at),
        run_dir=run_dir,
    )
    metadata_root = Path(os.path.abspath(Path(workspace_root).expanduser())) if workspace_root is not None else None
    config_dict = relative_metadata(normalize_config(config), metadata_root)
    storage_root = logdir_path.resolve()
    db_path = resolve_log_path(storage_root, run_id, "run.sqlite")
    for suffix in ("-wal", "-shm", "-journal"):
        resolve_log_path(storage_root, run_id, "run.sqlite" + suffix)
    previous = read_run_meta(db_path, run_id) if db_path.exists() else None
    environment = previous.env if previous else collect_environment(capture_hardware=capture_hardware, workspace_root=metadata_root)

    writer = SqliteStorageWriter(db_path)
    try:
        if previous:
            writer.update_config(config_dict)
            if tags:
                writer.update_tags(tags, [])
            config_dict = {**previous.config, **config_dict}
            exp_name = previous.name
        else:
            writer.init_run(
                RunMeta(
                    run_id=run_id,
                    name=exp_name,
                    started_at=started_at,
                    config=config_dict,
                    env=environment,
                    tags=tags,
                )
            )
    except Exception:
        writer.flush_and_close()
        raise

    worker = AsyncWriteWorker(writer)
    worker.start()
    set_context(
        RunContext(
            run_id=run_id,
            logdir=logdir_path,
            exp_name=exp_name,
            config=config_dict,
            writer=writer,
            worker=worker,
            workspace_root=metadata_root,
        )
    )


def log(metrics: Mapping[str, float], step: int, *, group: str | None = None) -> None:
    """Record a group of scalar metrics.

    Params:
        metrics: metric name -> real number, e.g. {"train/loss": 0.12};
            non-numeric values are dropped instead of being converted.
        step: training step or epoch, used as the curve x-axis.
        group: optional single-level namespace. Field names may contain subpaths with this
            argument, or explicit "group/field" keys without it.

    Exceptions: never raised to the caller; silently ignored before init(),
    and returns immediately after the points are queued.
    """
    context = get_context()
    if context is None or context.worker is None:
        return

    try:
        wall_time = time.time()
        records = []
        for name, value in metrics.items():
            if not isinstance(value, Real):
                continue
            try:
                field = metric_field(name, group)
            except ValueError as error:
                try:
                    warnings.warn(str(error), RuntimeWarning, stacklevel=2)
                except Warning:
                    pass
                continue
            records.append(
                ScalarRecord(
                    metric=field.metric,
                    step=int(step),
                    wall_time=wall_time,
                    value=float(value),
                )
            )
        if records:
            context.worker.put_scalars(records)
    except Exception:
        return


def log_config(obj: Any) -> None:
    """Append config / hyperparameters to the current run incrementally.

    Params:
        obj: argparse.Namespace, dict, dataclass, a YAML/JSON/TOML file path,
            or locals(); non-serializable values (Tensor, DataLoader, ...) are
            filtered out.

    Exceptions: never raised to the caller; silently ignored before init().
    """
    context = get_context()
    if context is None or context.worker is None:
        return
    try:
        delta = relative_metadata(normalize_config(obj), context.workspace_root)
    except Exception:
        return
    if not delta:
        return
    context.config.update(delta)
    context.worker.put_config(delta)


def _shutdown() -> None:
    """Flush and close the active run; safe to call repeatedly."""
    context = get_context()
    if context is None:
        return
    if context.image_worker is not None:
        context.image_worker.close()
    if context.audio_worker is not None:
        context.audio_worker.close()
    if context.worker is not None:
        try:
            context.worker.close()
        except Exception:
            pass
    set_context(None)


atexit.register(_shutdown)


def add_tags(tags: list[str]) -> None:
    """Add exact, case-sensitive labels without replacing existing UI labels."""
    _queue_tags(tags, [])


def remove_tags(tags: list[str]) -> None:
    """Remove matching labels; absent labels and calls before init are harmless."""
    _queue_tags([], tags)


def _queue_tags(add, remove):
    context = get_context()
    if context is None or context.worker is None:
        return
    try:
        context.worker.put_tags(normalize_tags(add), normalize_tags(remove))
    except Exception as error:
        try:
            warnings.warn(str(error), RuntimeWarning, stacklevel=3)
        except Warning:
            pass
