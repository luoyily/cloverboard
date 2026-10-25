"""SQLite schema for a single run database.

One run = one run.sqlite. The schema is the storage contract:
- meta is a key-value table; config and env are each stored as one JSON string.
- scalars stores points directly under the metric name, with an index on
  (metric, step) for per-curve reads.
Images/audio index files; text bodies are stored directly in SQLite.
"""

import sqlite3

SCHEMA_VERSION = 7

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scalars (
    metric    TEXT    NOT NULL,
    step      INTEGER NOT NULL,
    wall_time REAL    NOT NULL,
    value     REAL,
    UNIQUE(metric, step)
);

CREATE TABLE IF NOT EXISTS images (
    image_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    step INTEGER NOT NULL,
    wall_time REAL NOT NULL,
    path TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    format TEXT NOT NULL,
    caption TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_images_name_step ON images(name, step);

CREATE TABLE IF NOT EXISTS run_notes (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    body TEXT NOT NULL,
    updated_at REAL NOT NULL,
    revision INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (tag TEXT PRIMARY KEY);

CREATE TABLE IF NOT EXISTS audio (
    audio_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    step INTEGER NOT NULL,
    wall_time REAL NOT NULL,
    path TEXT NOT NULL,
    sample_rate INTEGER NOT NULL,
    channels INTEGER NOT NULL,
    frames INTEGER NOT NULL,
    caption TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audio_name_step ON audio(name, step);

CREATE TABLE IF NOT EXISTS texts (
    name TEXT NOT NULL,
    step INTEGER NOT NULL,
    wall_time REAL NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY(name, step)
);
"""

# SQLite stores NaN as NULL, so the value column is nullable by design;
# the reader maps NULL back to NaN to keep the non-finite break visible.


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes; idempotent."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        raise ValueError("Unsupported database schema; recreate the local test dataset")
    conn.executescript(_SCHEMA_SQL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
