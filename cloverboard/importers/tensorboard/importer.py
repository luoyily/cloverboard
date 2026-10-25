"""Poll local event files and upsert scalars into standard Run databases."""

from collections import Counter
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import sqlite3
import stat
from uuid import uuid4

from cloverboard.importers.tensorboard.events import EventCursor, FileStamp, scalar_metric, scalar_value
from cloverboard.storage.paths import resolve_log_path, validate_run_id
from cloverboard.storage.protocols import RunMeta, ScalarRecord
from cloverboard.storage.sqlite_backend import SqliteStorageWriter, read_run_meta


@dataclass(frozen=True)
class SourceRun:
    run_id: str
    directory: str
    files: list[tuple[Path, FileStamp]]


@dataclass
class RunCursor:
    files: dict[str, EventCursor] = field(default_factory=dict)
    owners: dict[tuple[str, int], str] = field(default_factory=dict)


@dataclass
class PollResult:
    runs: int = 0
    files: int = 0
    changed_files: int = 0
    replayed_runs: int = 0
    events: int = 0
    scalars: int = 0
    written: int = 0
    unsupported: Counter[str] = field(default_factory=Counter)
    incomplete_files: list[str] = field(default_factory=list)


def _directory_link(path: Path) -> bool:
    info = path.lstat()
    # Python 3.11 has no Path.is_junction(); Windows junctions are reparse points.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def discover_runs(source: Path) -> list[SourceRun]:
    """A source root or its immediate children can each represent a Run."""
    if not source.is_dir():
        raise ValueError(f"TensorBoard source directory does not exist: {source}")
    runs = []
    names = set()

    def scan_error(error):
        raise error

    for directory, subdirs, filenames in os.walk(source, onerror=scan_error):
        # Never follow directory links, including Windows junctions.
        subdirs[:] = sorted(name for name in subdirs if not _directory_link(Path(directory) / name))
        event_names = sorted(name for name in filenames if name.startswith("events.out.tfevents."))
        if not event_names:
            continue
        path = Path(directory)
        relative = path.relative_to(source)
        if len(relative.parts) > 1:
            raise ValueError(f"Nested TensorBoard Run {relative.as_posix()!r}; choose its parent as the source root")
        run_id = validate_run_id(path.name)
        if run_id.casefold() in names:
            raise ValueError(f"TensorBoard Run names collide across platforms: {run_id}")
        names.add(run_id.casefold())
        files = []
        for name in event_names:
            event_path = path / name
            if event_path.is_symlink() and not event_path.resolve().is_relative_to(source):
                raise ValueError(f"TensorBoard event file is outside the source: {event_path}")
            try:
                stamp = FileStamp.read(event_path)
            except FileNotFoundError:
                continue  # A writer may rotate files between listing and stat.
            files.append((event_path, stamp))
        if files:
            runs.append(SourceRun(run_id, relative.as_posix(), files))
    return sorted(runs, key=lambda run: run.run_id)


def _claim_output(output: Path, source: Path) -> sqlite3.Connection:
    workspace = resolve_log_path(output, ".workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    lock_path = resolve_log_path(output, ".workspace", "tensorboard-import.sqlite")
    connection = sqlite3.connect(lock_path, timeout=0, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS source (id INTEGER PRIMARY KEY CHECK (id=1), path TEXT NOT NULL)")
        row = connection.execute("SELECT path FROM source WHERE id=1").fetchone()
        if row and Path(row[0]) != source:
            raise ValueError("This output is bound to another TensorBoard source; choose another output directory")
        connection.execute("INSERT OR IGNORE INTO source VALUES (1, ?)", (str(source),))
        connection.commit()
        # A separate database keeps the process lock away from Web workspace writes.
        # SQLite releases it on crashes as well as normal shutdown on both platforms.
        connection.execute("BEGIN IMMEDIATE")
        return connection
    except sqlite3.OperationalError as error:
        connection.close()
        if "locked" in str(error).lower():
            raise ValueError("Another TensorBoard importer is using this output directory") from error
        raise
    except BaseException:
        connection.close()
        raise


class TensorBoardImporter:
    """Single-producer scalar import with in-memory event cursors.

    Source and output must be disjoint. Use as a context manager, then call
    poll() once or periodically. Restarting replays the source with upserts;
    byte offsets are intentionally not persisted. Errors require a fresh poll.
    """

    def __init__(self, source: str | Path, output: str | Path):
        self.source = Path(source).expanduser().resolve()
        self.output = Path(output).expanduser().resolve()
        if self.source.is_relative_to(self.output) or self.output.is_relative_to(self.source):
            raise ValueError("Source and output directories must not overlap")
        if not self.source.is_dir():
            raise ValueError(f"TensorBoard source directory does not exist: {self.source}")
        self._runs: dict[str, RunCursor] = {}
        self._lease = _claim_output(self.output, self.source)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._runs.clear()
        if self._lease is not None:
            self._lease.close()
            self._lease = None

    def poll(self, *, allow_incomplete: bool = True) -> PollResult:
        if self._lease is None:
            raise ValueError("The TensorBoard importer is closed")
        try:
            return self._poll(allow_incomplete)
        except BaseException:
            # A failed batch may have advanced a decoder; replay before retrying.
            self._runs.clear()
            raise

    def _poll(self, allow_incomplete: bool) -> PollResult:
        runs = discover_runs(self.source)
        result = PollResult(runs=len(runs), files=sum(len(run.files) for run in runs))
        destinations = {path.name.casefold(): path.name for path in self.output.iterdir() if path.is_dir()}
        present = {run.run_id for run in runs}
        for run_id in list(self._runs):
            if run_id not in present:
                del self._runs[run_id]
        for run in runs:
            previous_name = destinations.get(run.run_id.casefold())
            if previous_name is not None and previous_name != run.run_id:
                raise ValueError(f"Output Run names collide across platforms: {run.run_id} / {previous_name}")
            cursor = self._runs.setdefault(run.run_id, RunCursor())
            current = {path.name: stamp for path, stamp in run.files}
            removed = cursor.files.keys() - current.keys()
            replaced = any(
                name in cursor.files and cursor.files[name].stamp is not None and stamp.replaces(cursor.files[name].stamp)
                for name, stamp in current.items()
            )
            if removed or replaced:
                # Replaying this Run restores filename precedence when a file loses
                # records. No target rows are deleted, including source-only history.
                cursor = self._runs[run.run_id] = RunCursor()
                result.replayed_runs += 1
            changed = [(path, stamp) for path, stamp in run.files
                       if path.name not in cursor.files or cursor.files[path.name].stamp != stamp]
            if changed:
                self._import_run(run, cursor, changed, result, allow_incomplete)
            for path, _ in run.files:
                if cursor.files[path.name].incomplete:
                    if not allow_incomplete:
                        raise ValueError(f"Incomplete or corrupt event file: {path}; use --follow to wait for an unfinished tail")
                    result.incomplete_files.append(path.relative_to(self.source).as_posix())
        return result

    def _import_run(
        self, run: SourceRun, cursor: RunCursor, changed: list[tuple[Path, FileStamp]],
        result: PollResult, allow_incomplete: bool,
    ) -> None:
        directory = resolve_log_path(self.output, run.run_id)
        directory.mkdir(exist_ok=True)
        db_path = resolve_log_path(self.output, run.run_id, "run.sqlite")
        for suffix in ("-wal", "-shm", "-journal"):
            resolve_log_path(self.output, run.run_id, "run.sqlite" + suffix)
        existing = db_path.exists()
        if existing:
            meta = read_run_meta(db_path, run.run_id)
            origin = meta.env.get("tensorboard_import", {})
            if not isinstance(origin, dict) or origin.get("directory") != run.directory:
                raise ValueError(f"Output Run {run.run_id!r} was not imported from this TensorBoard directory")
        temporary = directory / f".tensorboard-{uuid4().hex}.sqlite" if not existing else None
        writer = None
        try:
            writer = SqliteStorageWriter(temporary or db_path)
            batch: list[ScalarRecord] = []
            started_at = None
            tag_map = {}
            for path, stamp in changed:
                if path.name not in cursor.files:
                    cursor.files[path.name] = EventCursor(path)
                event_cursor = cursor.files[path.name]
                result.changed_files += 1
                for event in event_cursor.read(stamp):
                    result.events += 1
                    if math.isfinite(event.wall_time) and event.wall_time > 0:
                        started_at = min(started_at or event.wall_time, event.wall_time)
                    for value in event.summary.value:
                        number = scalar_value(value)
                        if number is None:
                            result.unsupported[value.WhichOneof("value") or "no_value"] += 1
                            continue
                        if not math.isfinite(event.wall_time):
                            raise ValueError(f"Non-finite scalar timestamp: {path}, step {event.step}")
                        result.scalars += 1
                        metric = scalar_metric(value.tag)
                        tag_map[value.tag] = metric
                        key = (metric, event.step)
                        owner = cursor.owners.get(key)
                        if owner is not None and owner > path.name:
                            continue
                        cursor.owners[key] = path.name
                        batch.append(ScalarRecord(metric, event.step, event.wall_time, number))
                        result.written += 1
                        if len(batch) >= 4096:
                            writer.append_scalars(batch)
                            batch.clear()
                if event_cursor.incomplete and not allow_incomplete:
                    raise ValueError(f"Incomplete or corrupt event file: {path}; use --follow to wait for an unfinished tail")
            writer.append_scalars(batch)
            if not existing:
                writer.init_run(RunMeta(run.run_id, run.run_id, started_at or 0, env={
                    "tensorboard_import": {"directory": run.directory, "tag_map": tag_map},
                }, tags=["tensorboard-import"]))
            writer.flush_and_close()
            if temporary is not None:
                if db_path.exists():
                    raise ValueError(f"Output Run appeared during import: {run.run_id}")
                temporary.replace(db_path)
        finally:
            if writer is not None:
                writer.flush_and_close()
            if temporary is not None:
                for suffix in ("", "-wal", "-shm", "-journal"):
                    temporary.with_name(temporary.name + suffix).unlink(missing_ok=True)
