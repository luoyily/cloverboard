"""SQLite implementations of the storage contracts."""

import json
import math
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np

from cloverboard.storage.downsample import select_indices
from cloverboard.storage.protocols import (
    RunMeta, RunNotFound,
    ImageRecord,
    AudioRecord, AudioPage, TextRecord,
    MediaNameSummary, MediaTimeline, ImagePage, RunNote, NoteConflict,
    RunDetails,
    FileAsset,
    RunSummary,
    ScalarPoint,
    ScalarBounds,
    ScalarRecord,
    ScalarSummary,
    StorageReader,
    StorageWriter,
)
from cloverboard.storage.schema import SCHEMA_VERSION, create_schema
from cloverboard.tags import normalize_tags
from cloverboard.storage.paths import UnsafeLogPathError, resolve_log_path, validate_run_id

_sampling_lock = Lock()


def read_run_meta(db_path: Path, run_id: str) -> RunMeta:
    """Validate an existing Run before opening it for resumed writes.

    This read must not create or repair a database: a wrong directory or schema
    should disable logging rather than overwrite unrelated data.
    """
    with _connect_readonly(db_path) as connection:
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("Unsupported Run database schema")
        rows = dict(connection.execute("SELECT key, value FROM meta"))
        if not {"name", "started_at", "config", "env"}.issubset(rows):
            raise ValueError("Run database is missing metadata")
        name, started_at, config, env = (json.loads(rows[key]) for key in ("name", "started_at", "config", "env"))
        if (name is not None and not isinstance(name, str)) or not isinstance(started_at, (int, float)) or not math.isfinite(started_at):
            raise ValueError("Invalid Run name or start time")
        if not isinstance(config, dict) or not isinstance(env, dict):
            raise ValueError("Invalid Run config or environment")
        tags = [row[0] for row in connection.execute("SELECT tag FROM tags ORDER BY tag")]
    return RunMeta(run_id, name, started_at, config, env, normalize_tags(tags))


class SqliteStorageWriter:
    """Writes one run.sqlite; all calls must be serialized by the caller."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        create_schema(self._conn)

    def init_run(self, meta: RunMeta) -> None:
        rows = {
            "name": _dump(meta.name),
            "started_at": _dump(meta.started_at),
            "config": _dump(meta.config),
            "env": _dump(meta.env),
        }
        if self._conn is None:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                rows.items(),
            )
            self._conn.executemany("INSERT OR IGNORE INTO tags VALUES (?)", [(tag,) for tag in normalize_tags(meta.tags)])

    def update_config(self, config_delta: Mapping[str, Any]) -> None:
        if self._conn is None or not config_delta:
            return
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'config'").fetchone()
        config = _loads(row[0], {}) if row else {}
        config.update(config_delta)
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('config', ?)",
                (_dump(config),),
            )

    def _touch_last_recorded(self, wall_time: float) -> None:
        """Keep the newest record timestamp in meta so summaries avoid table scans."""
        if self._conn is None or not math.isfinite(wall_time):
            return
        row = self._conn.execute("SELECT value FROM meta WHERE key='last_recorded_at'").fetchone()
        current = _loads(row[0], None) if row else None
        if not isinstance(current, (int, float)) or wall_time > current:
            self._conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_recorded_at', ?)", (_dump(wall_time),))

    def append_scalars(self, batch: list[ScalarRecord]) -> None:
        if self._conn is None or not batch:
            return
        with self._conn:
            origin = next((record for record in batch if record.step == 0 and math.isfinite(record.wall_time)), None)
            first = origin or next((record for record in batch if math.isfinite(record.wall_time)), None)
            kind = self._conn.execute("SELECT value FROM meta WHERE key = 'time_origin_kind'").fetchone()
            if first and (kind is None or (origin and _loads(kind[0], None) != "step_zero")):
                self._conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", [
                    ("time_origin", _dump(first.wall_time)),
                    ("time_origin_kind", _dump("step_zero" if origin else "first_sample")),
                ])
            newest = max((record.wall_time for record in batch if math.isfinite(record.wall_time)), default=None)
            if newest is not None:
                self._touch_last_recorded(newest)
            self._conn.executemany(
                "INSERT INTO scalars(metric, step, wall_time, value) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(metric, step) DO UPDATE SET wall_time=excluded.wall_time, value=excluded.value",
                [(record.metric, record.step, record.wall_time, record.value) for record in batch],
            )

    def append_image(self, image: ImageRecord) -> None:
        if self._conn is None:
            return
        with self._conn:
            self._touch_last_recorded(image.wall_time)
            self._conn.execute("INSERT INTO images VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (image.image_id, image.name, image.step, image.wall_time, image.path, image.width, image.height, image.format, image.caption))

    def flush_and_close(self) -> None:
        if self._conn is None:
            return
        connection, self._conn = self._conn, None
        try:
            connection.commit()
        finally:
            connection.close()

    def append_audio(self, audio: AudioRecord) -> None:
        if self._conn is not None:
            with self._conn:
                self._touch_last_recorded(audio.wall_time)
                self._conn.execute("INSERT INTO audio VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                   (audio.audio_id, audio.name, audio.step, audio.wall_time, audio.path, audio.sample_rate, audio.channels, audio.frames, audio.caption))

    def update_tags(self, add, remove) -> list[str]:
        if self._conn is None:
            return []
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            return _update_tags(self._conn, add, remove)


    def append_text(self, text: TextRecord) -> None:
        if self._conn is not None:
            with self._conn:
                self._touch_last_recorded(text.wall_time)
                self._conn.execute("INSERT INTO texts VALUES (?, ?, ?, ?) ON CONFLICT(name,step) "
                    "DO UPDATE SET wall_time=excluded.wall_time, text=excluded.text",
                    (text.name, text.step, text.wall_time, text.text))


class SqliteStorageReader:
    """Reads runs from a logdir of ``run_*/run.sqlite`` folders."""

    def __init__(self, logdir: str | Path) -> None:
        self._logdir = Path(logdir).resolve()
        self._summary_cache: dict[str, tuple[tuple, RunSummary, float]] = {}
        self._runs_lock = Lock()

    def list_runs(self) -> list[RunSummary]:
        with self._runs_lock:
            return self._list_runs()

    def _list_runs(self) -> list[RunSummary]:
        if not self._logdir.is_dir():
            self._summary_cache.clear()
            return []

        summaries: list[RunSummary] = []
        cached: dict[str, tuple[tuple, RunSummary, float]] = {}
        for entry in sorted(self._logdir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            try:
                db_path = self._run_db_path(entry.name)
            except (UnsafeLogPathError, RunNotFound):
                continue
            try:
                stamp = _summary_stamp(db_path)
            except FileNotFoundError:
                continue
            previous = self._summary_cache.get(entry.name)
            checked_at = time.monotonic()
            # A live WAL may reuse pages without a reliable file timestamp update.
            live_wal = stamp[1] is not None and stamp[1][1] > 0
            reusable = previous and previous[0] == stamp and (not live_wal or checked_at - previous[2] < 1)
            summary = previous[1] if reusable else self._read_summary(entry.name, db_path)
            summaries.append(summary)
            cached[entry.name] = (stamp, summary, previous[2] if reusable else checked_at)
        self._summary_cache = cached
        return summaries

    def get_scalar_series(
        self, run_id: str, metric: str, max_points: int = 800,
        step_min: int | None = None, step_max: int | None = None, smoothing: float = 0,
    ) -> list[ScalarPoint]:
        return self.get_scalar_series_batch(run_id, [metric], max_points, step_min, step_max, smoothing)[metric]

    def get_scalar_series_batch(
        self, run_id: str, metrics: list[str], max_points: int = 800,
        step_min: int | None = None, step_max: int | None = None, smoothing: float = 0,
    ) -> dict[str, list[ScalarPoint]]:
        # SQLite row decoding and tiny NumPy reductions contend on the GIL.
        # Other reader operations remain independent of this sampling queue.
        with _sampling_lock:
            return self._read_scalar_batch(run_id, metrics, max_points, step_min, step_max, smoothing)

    def _read_scalar_batch(
        self, run_id: str, metrics: list[str], max_points: int,
        step_min: int | None, step_max: int | None, smoothing: float,
    ) -> dict[str, list[ScalarPoint]]:
        metrics = list(dict.fromkeys(metrics))
        db_path = self._run_db_path(run_id)
        with _connect_readonly(db_path) as connection:
            connection.execute("BEGIN")
            clauses = []
            arguments: list[Any] = []
            # EMA needs the prefix before the visible range, otherwise zoom changes its meaning.
            if step_min is not None and smoothing == 0:
                clauses.append("step >= ?")
                arguments.append(step_min)
            if step_max is not None:
                clauses.append("step <= ?")
                arguments.append(step_max)
            suffix = (" AND " + " AND ".join(clauses)) if clauses else ""
            rows = {}
            for metric in metrics:
                rows[metric] = connection.execute(
                    "SELECT step, value, wall_time FROM scalars WHERE metric = ?" + suffix + " ORDER BY step, rowid",
                    [metric, *arguments],
                ).fetchall()
        return {metric: _sample_scalar_rows(values, max_points, step_min, smoothing) for metric, values in rows.items()}

    def get_scalar_summary(
        self, run_id: str, metric: str, target: float | None = None, direction: str = "gte",
    ) -> ScalarSummary:
        db_path = self._run_db_path(run_id)
        with _connect_readonly(db_path) as connection:
            latest = connection.execute(
                "SELECT step, value, wall_time FROM scalars WHERE metric = ? ORDER BY step DESC LIMIT 1", (metric,),
            ).fetchone()
            low, high = connection.execute(
                "SELECT MIN(value), MAX(value) FROM scalars WHERE metric = ? AND value > ? AND value < ?",
                (metric, float("-inf"), float("inf")),
            ).fetchone()
            hit = None
            if target is not None:
                comparison = ">=" if direction == "gte" else "<="
                hit = connection.execute(
                    f"SELECT step, value, wall_time FROM scalars WHERE metric = ? AND value {comparison} ? "
                    "AND value > ? AND value < ? ORDER BY step, rowid LIMIT 1",
                    (metric, target, float("-inf"), float("inf")),
                ).fetchone()
        def point(row):
            return ScalarPoint(row[0], float("nan") if row[1] is None else row[1], wall_time=row[2]) if row else None
        return ScalarSummary(point(latest), low, high, point(hit))

    def list_metrics(self, run_id: str) -> list[str]:
        db_path = self._run_db_path(run_id)
        with _connect_readonly(db_path) as connection:
            rows = connection.execute(
                "SELECT DISTINCT metric FROM scalars ORDER BY metric"
            ).fetchall()
        return [row[0] for row in rows]

    def get_scalar_bounds(self, run_id: str, metric: str, step_min: int | None = None, step_max: int | None = None) -> ScalarBounds:
        db_path = self._run_db_path(run_id)
        clauses = ["metric = ?", "value > ?", "value < ?"]
        arguments: list[Any] = [metric, float("-inf"), float("inf")]
        if step_min is not None:
            clauses.append("step >= ?")
            arguments.append(step_min)
        if step_max is not None:
            clauses.append("step <= ?")
            arguments.append(step_max)
        with _connect_readonly(db_path) as connection:
            low, high, positive = connection.execute("SELECT MIN(value), MAX(value), MIN(CASE WHEN value > 0 THEN value END) FROM scalars WHERE " + " AND ".join(clauses), arguments).fetchone()
            return ScalarBounds(low, high, positive)

    def _read_summary(self, run_id: str, db_path: Path) -> RunSummary:
        with _connect_readonly(db_path) as connection:
            meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
            tags = [row[0] for row in connection.execute("SELECT tag FROM tags ORDER BY tag")]
            note_row = connection.execute("SELECT body FROM run_notes WHERE id=1").fetchone()
            has_scalars = connection.execute("SELECT EXISTS(SELECT 1 FROM scalars)").fetchone()[0]
            # Writers maintain this stamp in meta; it replaces four MAX(wall_time) scans.
            last_recorded = _loads(meta.get("last_recorded_at"), None)
        return RunSummary(
            run_id=run_id,
            name=_loads(meta.get("name"), None),
            started_at=float(_loads(meta.get("started_at"), 0.0)),
            config=_loads(meta.get("config"), {}),
            time_origin=_loads(meta.get("time_origin"), None),
            time_origin_kind=_loads(meta.get("time_origin_kind"), None),
            has_notes=bool(note_row and note_row[0].strip()),
            note_excerpt=note_row[0][:160] if note_row else "",
            tags=tags,
            has_scalars=bool(has_scalars),
            last_recorded_at=last_recorded if last_recorded is not None and math.isfinite(last_recorded) else None,
        )

    def update_run_tags(self, run_id, add, remove) -> list[str]:
        path = self._run_db_path(run_id)
        for suffix in ("-wal", "-shm", "-journal"):
            resolve_log_path(self._logdir, run_id, "run.sqlite" + suffix)
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=5)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            return _update_tags(connection, add, remove)

    def scalar_count(self, run_id, metric, step_min=None, step_max=None) -> int:
        path = self._run_db_path(run_id)
        clauses, arguments = ["metric=?"], [metric]
        if step_min is not None:
            clauses.append("step>=?")
            arguments.append(step_min)
        if step_max is not None:
            clauses.append("step<=?")
            arguments.append(step_max)
        with _connect_readonly(path) as connection:
            return connection.execute("SELECT COUNT(*) FROM scalars WHERE " + " AND ".join(clauses), arguments).fetchone()[0]

    def scalar_at_step(self, run_id, metric, step) -> ScalarPoint | None:
        path = self._run_db_path(run_id)
        with _connect_readonly(path) as connection:
            row = connection.execute("SELECT step, value, wall_time FROM scalars WHERE metric=? AND step<=? ORDER BY step DESC, rowid DESC LIMIT 1", (metric, step)).fetchone()
        return ScalarPoint(row[0], float("nan") if row[1] is None else row[1], wall_time=row[2]) if row else None

    def image_names(self, run_id: str) -> list[MediaNameSummary]:
        return self._media_names(run_id, "images")

    def audio_names(self, run_id: str) -> list[MediaNameSummary]:
        return self._media_names(run_id, "audio")

    def text_names(self, run_id: str) -> list[MediaNameSummary]:
        return self._media_names(run_id, "texts")

    def _media_names(self, run_id, table):
        path = self._run_db_path(run_id)
        with _connect_readonly(path) as connection:
            rows = connection.execute(f"SELECT name, COUNT(*), COUNT(DISTINCT step), MIN(step), MAX(step) FROM {table} GROUP BY name ORDER BY name").fetchall()
        return [MediaNameSummary(*row) for row in rows]

    def image_timeline(self, run_id: str, name: str, max_steps: int = 200) -> MediaTimeline:
        return self._media_timeline(run_id, name, max_steps, "images")

    def audio_timeline(self, run_id, name, max_steps=200) -> MediaTimeline:
        return self._media_timeline(run_id, name, max_steps, "audio")

    def text_timeline(self, run_id, name, max_steps=200) -> MediaTimeline:
        return self._media_timeline(run_id, name, max_steps, "texts")

    def get_text(self, run_id, name, step=None) -> TextRecord | None:
        path = self._run_db_path(run_id)
        with _connect_readonly(path) as connection:
            row = connection.execute("SELECT name, step, wall_time, text FROM texts WHERE name=?" +
                (" AND step<=?" if step is not None else "") + " ORDER BY step DESC LIMIT 1",
                (name, step) if step is not None else (name,)).fetchone()
        return TextRecord(*row) if row else None

    def _media_timeline(self, run_id, name, max_steps, table):
        path = self._run_db_path(run_id)
        max_steps = max(2, min(500, max_steps))
        with _connect_readonly(path) as connection:
            count = connection.execute(f"SELECT COUNT(DISTINCT step) FROM {table} WHERE name=?", (name,)).fetchone()[0]
            if not count:
                return MediaTimeline()
            if count <= max_steps:
                rows = connection.execute(f"SELECT DISTINCT step FROM {table} WHERE name=? ORDER BY step", (name,)).fetchall()
            else:
                ordinals = [round(i * (count - 1) / (max_steps - 1)) + 1 for i in range(max_steps)]
                placeholders = ",".join("?" for _ in ordinals)
                rows = connection.execute(
                    "SELECT step FROM (SELECT step, ROW_NUMBER() OVER (ORDER BY step) AS n "
                    f"FROM (SELECT DISTINCT step FROM {table} WHERE name=?)) "
                    f"WHERE n IN ({placeholders}) ORDER BY step", [name, *ordinals],
                ).fetchall()
        return MediaTimeline([row[0] for row in rows], count)

    def image_page(self, run_id: str, name: str, step: int | None = None, limit: int = 12, offset: int = 0) -> ImagePage:
        path = self._run_db_path(run_id)
        with _connect_readonly(path) as connection:
            row = connection.execute("SELECT MAX(step) FROM images WHERE name=?" + (" AND step<=?" if step is not None else ""), (name, step) if step is not None else (name,)).fetchone()
            actual = row[0]
            if actual is None:
                return ImagePage()
            total = connection.execute("SELECT COUNT(*) FROM images WHERE name=? AND step=?", (name, actual)).fetchone()[0]
            rows = connection.execute(
                "SELECT image_id, name, step, wall_time, path, width, height, format, caption "
                "FROM images WHERE name=? AND step=? ORDER BY wall_time, image_id LIMIT ? OFFSET ?",
                (name, actual, max(1, min(24, limit)), max(0, offset)),
            ).fetchall()
        next_offset = offset + len(rows)
        return ImagePage([ImageRecord(*row) for row in rows], actual, total, next_offset if next_offset < total else None)

    def audio_page(self, run_id, name, step=None, limit=12, offset=0) -> AudioPage:
        path = self._run_db_path(run_id)
        with _connect_readonly(path) as connection:
            actual = connection.execute("SELECT MAX(step) FROM audio WHERE name=?" + (" AND step<=?" if step is not None else ""), (name, step) if step is not None else (name,)).fetchone()[0]
            if actual is None:
                return AudioPage()
            total = connection.execute("SELECT COUNT(*) FROM audio WHERE name=? AND step=?", (name, actual)).fetchone()[0]
            rows = connection.execute("SELECT audio_id, name, step, wall_time, path, sample_rate, channels, frames, caption FROM audio WHERE name=? AND step=? ORDER BY wall_time, audio_id LIMIT ? OFFSET ?", (name, actual, max(1, min(24, limit)), max(0, offset))).fetchall()
        next_offset = offset + len(rows)
        return AudioPage([AudioRecord(*row) for row in rows], actual, total, next_offset if next_offset < total else None)

    def get_run_note(self, run_id: str) -> RunNote:
        path = self._run_db_path(run_id)
        with _connect_readonly(path) as connection:
            row = connection.execute("SELECT body, updated_at, revision FROM run_notes WHERE id=1").fetchone()
        return RunNote(*row) if row else RunNote()

    def save_run_note(self, run_id: str, body: str, revision: int) -> RunNote:
        path = self._run_db_path(run_id)
        for suffix in ("-wal", "-shm", "-journal"):
            resolve_log_path(self._logdir, run_id, "run.sqlite" + suffix)
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=5)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT revision FROM run_notes WHERE id=1").fetchone()
            current = row[0] if row else 0
            if current != revision:
                raise NoteConflict("This note changed in another tab. Reload it before saving.")
            note = RunNote(body, time.time(), current + 1)
            connection.execute("INSERT OR REPLACE INTO run_notes VALUES (1, ?, ?, ?)", (note.body, note.updated_at, note.revision))
        return note

    def get_run_details(self, run_id: str) -> RunDetails:
        path = self._run_db_path(run_id)
        with _connect_readonly(path) as connection:
            meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
        manifest = resolve_log_path(path.parent, "snapshot", "manifest.json")
        snapshot = None
        if manifest.is_file():
            try:
                snapshot = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                snapshot = {"status": "error", "error": "Snapshot manifest is unreadable"}
        return RunDetails(_loads(meta.get("config"), {}), _loads(meta.get("env"), {}), snapshot)

    def get_asset(self, run_id: str, asset_id: str, thumbnail: bool = False) -> FileAsset | None:
        db_path = self._run_db_path(run_id)
        if asset_id == "snapshot":
            path = resolve_log_path(db_path.parent, "snapshot", "code.zip")
            return FileAsset("application/zip", "code.zip", path) if path.is_file() else None
        with _connect_readonly(db_path) as connection:
            row = connection.execute("SELECT path, format FROM images WHERE image_id = ?", (asset_id,)).fetchone()
            if row is None:
                audio = connection.execute("SELECT path FROM audio WHERE audio_id=?", (asset_id,)).fetchone()
                row = (audio[0], "wav") if audio else None
        if row is None or row[1] not in {"png", "jpeg", "wav"}:
            return None
        media_root = resolve_log_path(db_path.parent, "media")
        path = resolve_log_path(db_path.parent, row[0])
        if not path.is_relative_to(media_root):
            raise UnsafeLogPathError("Image is outside the media directory")
        return FileAsset("audio/wav" if row[1] == "wav" else f"image/{row[1]}", path.name, path) if path.is_file() else None

    def _run_db_path(self, run_id: str) -> Path:
        validate_run_id(run_id)
        candidate = resolve_log_path(self._logdir, run_id, "run.sqlite")
        if not candidate.is_file():
            raise RunNotFound("Run not found")
        return candidate


def _summary_stamp(db_path: Path) -> tuple:
    stamps = []
    for path in (db_path, db_path.with_name(db_path.name + "-wal")):
        try:
            info = path.stat()
            stamps.append((info.st_mtime_ns, info.st_size, info.st_ino))
        except FileNotFoundError:
            stamps.append(None)
    return tuple(stamps)


def _sample_scalar_rows(rows: list[tuple], max_points: int, step_min: int | None, smoothing: float) -> list[ScalarPoint]:
    if not rows:
        return []
    steps = np.array([row[0] for row in rows], dtype=np.int64)
    times = np.array([row[2] for row in rows], dtype=np.float64)
    values = np.array([np.nan if row[1] is None else row[1] for row in rows], dtype=np.float64)
    smoothed = values
    if smoothing > 0:
        smoothed = np.empty_like(values)
        previous = None
        for index, value in enumerate(values):
            if not math.isfinite(value):
                previous = None
                smoothed[index] = np.nan
            else:
                previous = value if previous is None else smoothing * previous + (1 - smoothing) * value
                smoothed[index] = previous
    if step_min is not None:
        mask = steps >= step_min
        steps, values, smoothed, times = steps[mask], values[mask], smoothed[mask], times[mask]
    indices = select_indices(steps, values, max_points)
    return [ScalarPoint(step=int(steps[index]), value=float(values[index]), smoothed=float(smoothed[index]), wall_time=float(times[index])) for index in indices]


@contextmanager
def _connect_readonly(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        yield connection
    finally:
        # SQLite's connection context manager commits/rolls back but does not close.
        connection.close()


def _update_tags(connection, add, remove) -> list[str]:
    additions, removals = normalize_tags(add), normalize_tags(remove)
    connection.executemany("DELETE FROM tags WHERE tag=?", [(tag,) for tag in removals])
    connection.executemany("INSERT OR IGNORE INTO tags VALUES (?)", [(tag,) for tag in additions])
    tags = [row[0] for row in connection.execute("SELECT tag FROM tags ORDER BY tag")]
    normalize_tags(tags)
    return tags


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None, default: Any) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default
