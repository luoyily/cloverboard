import sqlite3
import pytest

from cloverboard.storage.protocols import (
    RunMeta,
    StorageReader,
    StorageWriter,
)
from cloverboard.storage.schema import SCHEMA_VERSION, create_schema
from tests.fakes import FakeStorageReader, FakeStorageWriter


def test_schema_creates_tables_and_index():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)

    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"meta", "scalars"} <= tables
    assert any(row[2] for row in conn.execute("PRAGMA index_list(scalars)"))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_schema_rejects_other_versions_without_automatic_migration():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA user_version=5")
    with pytest.raises(ValueError, match="recreate"):
        create_schema(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 5


def test_schema_is_idempotent():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    create_schema(conn)


def test_scalars_table_roundtrip():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    conn.execute(
        "INSERT INTO scalars VALUES (?, ?, ?, ?)",
        ("train/loss", 1, 123.0, 0.5),
    )
    row = conn.execute("SELECT metric, step, value FROM scalars").fetchone()
    assert row == ("train/loss", 1, 0.5)


def test_fake_backends_satisfy_protocols():
    assert isinstance(FakeStorageWriter(), StorageWriter)
    assert isinstance(FakeStorageReader(), StorageReader)


def test_fake_writer_merges_config():
    writer = FakeStorageWriter()
    writer.init_run(
        RunMeta(run_id="r1", name="demo", started_at=0.0, config={"lr": 0.1})
    )

    writer.update_config({"bs": 32})

    assert writer.config == {"lr": 0.1, "bs": 32}
