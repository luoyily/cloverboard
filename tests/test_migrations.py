import json
import sqlite3
from contextlib import closing

import pytest
from fastapi import HTTPException

from cloverboard.migrations.model import Migration, MigrationError, UnsupportedSchema
from cloverboard.migrations.runner import inspect_logdir, migrate_logdir, upgrade_database
from cloverboard.migrations.versions import RUN_SCHEMA_VERSION, WORKSPACE_SCHEMA_VERSION
from cloverboard.server.models import WorkspaceSave, WorkspaceState
from cloverboard.server.workspace import _SCHEMA, WorkspaceStore
from cloverboard.storage.schema import create_schema


def _user_version(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("PRAGMA user_version").fetchone()[0]


def _workspace_payload(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT payload FROM workspace_state WHERE id=1").fetchone()[0]


def _make_workspace(root, *, version=0, state=None, view_state=None, raw_state=None):
    path = root / ".workspace" / "workspace.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(_SCHEMA)
        payload = raw_state if raw_state is not None else json.dumps({
            "state": state if state is not None else {"version": 1, "search": "legacy"},
            "saved_at": 10,
        })
        connection.execute("INSERT INTO workspace_state VALUES (1, ?)", (payload,))
        connection.execute(
            "INSERT INTO views VALUES (?, ?, ?)",
            ("a" * 32, "Legacy view", json.dumps(view_state or {"version": 1, "panel": "table"})),
        )
        connection.execute("INSERT INTO palette_library VALUES (1, ?)", (json.dumps({"palettes": []}),))
        connection.execute(f"PRAGMA user_version = {version}")
        connection.commit()
    finally:
        connection.close()
    return path


def _make_run(root, run_id="run_a", *, version=RUN_SCHEMA_VERSION):
    path = root / run_id / "run.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        create_schema(connection)
        if version != RUN_SCHEMA_VERSION:
            connection.execute(f"PRAGMA user_version = {version}")
            connection.commit()
    return path


def _plan_for(plans, kind, run_id=None):
    return next(plan for plan in plans if plan.kind == kind and plan.run_id == run_id)


def test_workspace_v0_is_stamped_without_rewriting_payload(tmp_path):
    path = _make_workspace(tmp_path)
    payload = _workspace_payload(path)

    plan, = inspect_logdir(tmp_path)
    assert plan.kind == "workspace"
    assert plan.version == 0
    assert plan.target == WORKSPACE_SCHEMA_VERSION
    assert plan.status == "migrate"

    report = migrate_logdir(tmp_path, apply=True)
    assert report.ok, report.errors
    assert len(report.applied) == 1
    assert len(report.backups) == 1
    assert report.backup_root is not None
    assert report.backups[0].is_relative_to(report.backup_root)
    assert _user_version(path) == WORKSPACE_SCHEMA_VERSION
    assert _workspace_payload(path) == payload
    assert _user_version(report.backups[0]) == 0

    snapshot = WorkspaceStore(tmp_path).snapshot()
    assert snapshot.state.search == "legacy"
    assert snapshot.state.chart_layout == "left"
    assert snapshot.state.chart_card_style == "normal"
    assert snapshot.views[0].state.panel == "table"


def test_dry_run_writes_nothing_and_creates_no_backup(tmp_path):
    path = _make_workspace(tmp_path)

    report = migrate_logdir(tmp_path)

    assert report.dry_run
    assert report.ok
    assert report.applied == []
    assert report.backups == []
    assert _user_version(path) == 0
    assert not (tmp_path / ".cloverboard-backups").exists()


def test_apply_is_idempotent(tmp_path):
    path = _make_workspace(tmp_path)
    assert migrate_logdir(tmp_path, apply=True).ok

    report = migrate_logdir(tmp_path, apply=True)

    assert report.ok
    assert report.applied == []
    assert report.backups == []
    assert _user_version(path) == WORKSPACE_SCHEMA_VERSION


def test_new_workspace_is_created_at_current_version(tmp_path):
    store = WorkspaceStore(tmp_path)
    store.save_state(WorkspaceSave(state=WorkspaceState(), saved_at=1))

    path = tmp_path / ".workspace" / "workspace.sqlite"
    assert _user_version(path) == WORKSPACE_SCHEMA_VERSION
    assert _plan_for(inspect_logdir(tmp_path), "workspace").status == "current"


def test_newer_workspace_blocks_all_writes_during_preflight(tmp_path):
    path = _make_workspace(tmp_path, version=WORKSPACE_SCHEMA_VERSION + 1)
    run_path = _make_run(tmp_path)

    report = migrate_logdir(tmp_path, apply=True)

    assert not report.ok
    assert report.errors
    assert report.backups == []
    assert report.applied == []
    assert _user_version(path) == WORKSPACE_SCHEMA_VERSION + 1
    assert _user_version(run_path) == RUN_SCHEMA_VERSION


def test_invalid_workspace_payload_blocks_backup_and_migration(tmp_path):
    path = _make_workspace(tmp_path, raw_state="{")

    report = migrate_logdir(tmp_path, apply=True)

    assert not report.ok
    assert "invalid" in report.errors[0]
    assert report.backups == []
    assert _user_version(path) == 0


def test_run_v7_is_current_and_run_v6_is_unsupported(tmp_path):
    current = _make_run(tmp_path, "run_current")
    legacy = _make_run(tmp_path, "run_legacy", version=6)

    plans = inspect_logdir(tmp_path)
    assert _plan_for(plans, "run", "run_current").status == "current"
    assert _plan_for(plans, "run", "run_legacy").status == "unsupported"

    report = migrate_logdir(tmp_path, apply=True)
    assert not report.ok
    assert any("run_legacy" in error for error in report.errors)
    assert report.backups == []
    assert _user_version(current) == RUN_SCHEMA_VERSION
    assert _user_version(legacy) == 6


def test_run_v7_only_is_a_clean_noop(tmp_path):
    path = _make_run(tmp_path)

    report = migrate_logdir(tmp_path, apply=True)

    assert report.ok
    assert report.backups == []
    assert report.applied == []
    assert _user_version(path) == RUN_SCHEMA_VERSION


def test_upgrade_database_applies_consecutive_steps_in_order():
    steps = []
    connection = sqlite3.connect(":memory:")
    connection.isolation_level = None
    try:
        connection.execute("PRAGMA user_version = 7")

        def migrate_7(conn):
            steps.append(7)
            conn.execute("CREATE TABLE seven (id INTEGER)")

        def migrate_8(conn):
            steps.append(8)

        migrations = {7: Migration(8, migrate_7), 8: Migration(9, migrate_8)}
        assert upgrade_database(connection, 7, 9, migrations) == 9

        assert steps == [7, 8]
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='seven'").fetchone() == ("seven",)
    finally:
        connection.close()


def test_upgrade_database_rejects_a_missing_step():
    connection = sqlite3.connect(":memory:")
    connection.isolation_level = None
    try:
        with pytest.raises(UnsupportedSchema):
            upgrade_database(connection, 7, 9, {})
    finally:
        connection.close()


def test_workspace_read_accepts_v0_without_stamping_and_rejects_newer(tmp_path):
    path = _make_workspace(tmp_path, version=0)

    snapshot = WorkspaceStore(tmp_path).snapshot()
    assert snapshot.state.search == "legacy"
    assert _user_version(path) == 0

    connection = sqlite3.connect(path)
    try:
        connection.execute(f"PRAGMA user_version = {WORKSPACE_SCHEMA_VERSION + 1}")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(HTTPException) as error:
        WorkspaceStore(tmp_path).snapshot()
    assert error.value.status_code == 503


def test_apply_refuses_an_existing_migration_lock(tmp_path):
    _make_workspace(tmp_path)
    (tmp_path / ".cloverboard-migrate.lock").write_text("busy", encoding="utf-8")

    with pytest.raises(MigrationError, match="Another migration"):
        migrate_logdir(tmp_path, apply=True)
