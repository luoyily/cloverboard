import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from cloverboard.server.app import create_app
from scripts.migrate_workspace_20261028 import MigrationError, migrate


RUN_KEY = 'run:["local","baseline"]'
VIEW_ID = "a" * 32
PALETTE = {
    "id": "custom:" + "b" * 32,
    "name": "Ocean",
    "colors": ["#abcdef", "#3276b8"],
}
OLD_SCHEMA = """
CREATE TABLE workspace_state (id INTEGER PRIMARY KEY CHECK (id=1), payload TEXT NOT NULL);
CREATE TABLE views (view_id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE color_registry (id INTEGER PRIMARY KEY CHECK (id=1), payload TEXT NOT NULL);
"""


def old_workspace(logdir, *, state=None, view=None, palettes=None):
    directory = logdir / ".workspace"
    directory.mkdir(parents=True)
    path = directory / "workspace.sqlite"
    original = {
        "version": 1,
        "selected_ids": ["baseline", "trial"],
        "baseline_id": "baseline",
        "active_view_id": VIEW_ID,
        "smoothing": .25,
        "tooltip_decimals": 5,
        "targets": {"accuracy": {"value": .9, "direction": "gte"}},
        "chart_zoom": {"train/loss": {"x_min": 2, "x_max": 20}},
        "colors": {
            "mode": "random",
            "palette": {"kind": "custom", **PALETTE},
            "overrides": {RUN_KEY: "#AB12EF"},
            "assignments": {'run:["local","trial"]': 10},
            "top_k": {"enabled": True, "metric": "accuracy", "k": 2},
        },
    }
    original = original if state is None else state
    saved = {"state": original, "saved_at": 123456789}
    view_state = original if view is None else view
    registry = {
        "version": 1,
        "seed": "old-seed",
        "slots": {RUN_KEY: 0, 'run:["local","trial"]': 1},
        "palettes": [PALETTE] if palettes is None else palettes,
    }
    with sqlite3.connect(path) as connection:
        connection.executescript(OLD_SCHEMA)
        connection.execute("INSERT INTO workspace_state VALUES (1, ?)", (json.dumps(saved),))
        connection.execute("INSERT INTO views VALUES (?, ?, ?)", (VIEW_ID, "Comparison", json.dumps(view_state)))
        connection.execute("INSERT INTO color_registry VALUES (1, ?)", (json.dumps(registry),))
    return path


def tables(path):
    with sqlite3.connect(path) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def payload(path, table):
    with sqlite3.connect(path) as connection:
        return json.loads(connection.execute(f"SELECT payload FROM {table} WHERE id=1").fetchone()[0])


def test_dry_run_reports_changes_without_writing_or_creating_backups(tmp_path):
    path = old_workspace(tmp_path)
    before = path.read_bytes()
    existing = set(path.parent.iterdir())

    result = migrate(tmp_path)

    assert result["changed"] is True
    assert result["applied"] is False
    assert result["backup"] is None
    assert path.read_bytes() == before
    assert set(path.parent.iterdir()) == existing


def test_apply_preserves_settings_and_named_snapshots_readable_by_current_api(tmp_path):
    path = old_workspace(tmp_path)
    training = tmp_path / "baseline" / "run.sqlite"
    training.parent.mkdir()
    training.write_bytes(b"training data must not be opened or modified")

    result = migrate(tmp_path, apply=True)

    assert result["changed"] is True and result["applied"] is True
    assert "palette_library" in tables(path)
    assert "color_registry" not in tables(path)
    assert training.read_bytes() == b"training data must not be opened or modified"
    with TestClient(create_app(logdir=tmp_path), base_url="http://127.0.0.1") as client:
        response = client.get("/api/workspace")
        assert response.status_code == 200
        snapshot = response.json()
        assert snapshot["saved_at"] == 123456789
        assert snapshot["state"]["active_view_id"] == VIEW_ID
        assert snapshot["views"][0]["view_id"] == VIEW_ID
        assert snapshot["views"][0]["name"] == "Comparison"
        for state in (snapshot["state"], snapshot["views"][0]["state"]):
            assert state["selected_ids"] == ["baseline", "trial"]
            assert state["baseline_id"] == "baseline"
            assert state["smoothing"] == .25
            assert state["tooltip_decimals"] == 5
            assert state["colors"]["runs"] == {RUN_KEY: {"color": "#ab12ef", "fixed": True}}
            assert state["colors"]["palette"] == {"kind": "custom", **PALETTE}
            assert state["colors"]["top_k"]["enabled"] is True
            assert "overrides" not in state["colors"]
            assert "assignments" not in state["colors"]
            assert "chart_zoom" not in state
            assert state["targets"]["accuracy"] == {
                "items": [{"id": "migrated", "name": "Target", "chart_label": "", "value": .9, "direction": "gte"}],
                "active_id": "migrated",
            }
        assert client.get("/api/workspace/palettes").json() == [PALETTE]


def test_backup_keeps_original_payloads_and_second_apply_is_a_no_op(tmp_path):
    path = old_workspace(tmp_path)
    previous_state = payload(path, "workspace_state")
    previous_registry = payload(path, "color_registry")
    with sqlite3.connect(path) as connection:
        previous_view = connection.execute("SELECT * FROM views").fetchall()

    first = migrate(path, apply=True)

    backup = Path(first["backup"])
    assert backup.is_file() and backup != path
    assert "color_registry" in tables(backup)
    assert "palette_library" not in tables(backup)
    assert payload(backup, "workspace_state") == previous_state
    assert payload(backup, "color_registry") == previous_registry
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT * FROM views").fetchall() == previous_view
    before = path.read_bytes()
    existing = set(path.parent.iterdir())

    second = migrate(path, apply=True)

    assert second["changed"] is False
    assert second["applied"] is False
    assert second["backup"] is None
    assert path.read_bytes() == before
    assert set(path.parent.iterdir()) == existing


def test_backup_includes_committed_changes_still_in_wal(tmp_path):
    path = old_workspace(tmp_path)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("PRAGMA wal_autocheckpoint=0")
        saved = payload(path, "workspace_state")
        saved["saved_at"] = 987654321
        saved["state"]["search"] = "committed WAL state"
        connection.execute("UPDATE workspace_state SET payload=?", (json.dumps(saved),))
        connection.commit()
        assert Path(str(path) + "-wal").stat().st_size > 0

        result = migrate(path, apply=True)

        backup = Path(result["backup"])
        assert payload(backup, "workspace_state") == saved
        assert "color_registry" in tables(backup)
        assert payload(path, "workspace_state")["state"]["search"] == "committed WAL state"
    finally:
        connection.close()


@pytest.mark.parametrize("invalid", ["not json", json.dumps({"future_setting": True})])
def test_invalid_view_rejects_entire_migration_without_partial_writes(tmp_path, invalid):
    path = old_workspace(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE views SET state=?", (invalid,))
    before = path.read_bytes()
    existing = set(path.parent.iterdir())

    with pytest.raises(MigrationError):
        migrate(path, apply=True)

    assert path.read_bytes() == before
    assert set(path.parent.iterdir()) == existing
    assert "color_registry" in tables(path)
    assert "palette_library" not in tables(path)


def test_write_failure_rolls_back_created_table_and_all_updates_with_backup(tmp_path):
    path = old_workspace(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TRIGGER block_view_updates BEFORE UPDATE ON views
            BEGIN SELECT RAISE(ABORT, 'blocked migration view update'); END;
        """)
        original = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("workspace_state", "views", "color_registry")
        }

    with pytest.raises(MigrationError, match="blocked migration view update"):
        migrate(path, apply=True)

    assert "palette_library" not in tables(path)
    assert "color_registry" in tables(path)
    with sqlite3.connect(path) as connection:
        for table, rows in original.items():
            assert connection.execute(f"SELECT * FROM {table}").fetchall() == rows
    backups = list(path.parent.glob("workspace.sqlite.bak-*"))
    assert len(backups) == 1
    assert "palette_library" not in tables(backups[0])
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        for table, rows in original.items():
            assert connection.execute(f"SELECT * FROM {table}").fetchall() == rows


def test_mixed_database_keeps_current_palette_and_run_color_over_old_versions(tmp_path):
    newer = {**PALETTE, "name": "Updated ocean", "colors": ["#123456", "#654321"]}
    additional = {"id": "custom:" + "c" * 32, "name": "Spring", "colors": ["#00aaff", "#ffbb00"]}
    path = old_workspace(tmp_path, palettes=[PALETTE, additional])
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE palette_library (id INTEGER PRIMARY KEY CHECK (id=1), payload TEXT NOT NULL)")
        connection.execute("INSERT INTO palette_library VALUES (1, ?)", (json.dumps({"palettes": [newer]}),))
        saved = json.loads(connection.execute("SELECT payload FROM workspace_state").fetchone()[0])
        saved["state"]["colors"]["runs"] = {RUN_KEY: {"color": "#123456", "fixed": False}}
        connection.execute("UPDATE workspace_state SET payload=?", (json.dumps(saved),))

    migrate(path, apply=True)

    library = payload(path, "palette_library")["palettes"]
    assert {palette["id"]: palette for palette in library} == {newer["id"]: newer, additional["id"]: additional}
    restored = payload(path, "workspace_state")["state"]
    assert restored["colors"]["runs"][RUN_KEY] == {"color": "#123456", "fixed": False}
    assert restored["colors"]["palette"] == {"kind": "custom", **PALETTE}


def test_palette_name_conflict_is_not_silently_overwritten(tmp_path):
    path = old_workspace(tmp_path)
    conflict = {**PALETTE, "id": "custom:" + "c" * 32}
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE palette_library (id INTEGER PRIMARY KEY CHECK (id=1), payload TEXT NOT NULL)")
        connection.execute("INSERT INTO palette_library VALUES (1, ?)", (json.dumps({"palettes": [conflict]}),))
    before = path.read_bytes()

    with pytest.raises(MigrationError):
        migrate(path, apply=True)

    assert path.read_bytes() == before


def test_training_database_and_missing_path_are_rejected_without_creating_files(tmp_path):
    run = tmp_path / "run.sqlite"
    with sqlite3.connect(run) as connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO meta VALUES ('name', 'baseline')")
    before = run.read_bytes()
    for path in (run, tmp_path / "missing"):
        with pytest.raises(MigrationError):
            migrate(path, apply=True)
    assert run.read_bytes() == before
    assert set(tmp_path.iterdir()) == {run}


def test_existing_named_targets_stay_intact(tmp_path):
    target = {
        "items": [{"id": "goal", "name": "Goal", "chart_label": "G", "value": .9, "direction": "gte"}],
        "active_id": "goal",
    }
    path = old_workspace(tmp_path, state={"targets": {"accuracy": target}, "colors": {"assignments": {}}})

    migrate(path, apply=True)

    assert payload(path, "workspace_state")["state"]["targets"]["accuracy"] == target
