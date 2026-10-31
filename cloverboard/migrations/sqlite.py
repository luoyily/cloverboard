"""SQLite helpers shared by migration steps and the runner."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from cloverboard.migrations.model import InvalidDatabase, MigrationError


def connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)


def connect_writable(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5)
    connection.isolation_level = None
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def read_user_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def write_user_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(f"PRAGMA user_version = {int(version)}")


def quick_check(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA quick_check").fetchall()
    if result != [("ok",)]:
        raise InvalidDatabase(f"SQLite integrity check failed: {result!r}")


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def checkpoint(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.commit()
    except sqlite3.Error:
        pass


def backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise MigrationError(f"Backup already exists: {destination}")
    source_connection = connect_readonly(source)
    try:
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        source_connection.close()
