"""Workspace database migrations."""

import sqlite3

from pydantic import ValidationError

from cloverboard.migrations.model import InvalidDatabase, Migration
from cloverboard.migrations.sqlite import quick_check
from cloverboard.server.models import (
    MetricTargets, PaletteLibrary, SavedView, TargetLibrarySave, WorkspaceSave, WorkspaceState,
)

_REQUIRED_TABLES = {"workspace_state", "views", "palette_library"}
_CREATE_TARGET_LIBRARY = """
CREATE TABLE IF NOT EXISTS target_library (
    id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL
)
"""


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def validate_workspace(connection: sqlite3.Connection) -> None:
    """Validate the current workspace SQL tables and every stored JSON payload."""
    tables = _table_names(connection)
    if not _REQUIRED_TABLES <= tables:
        missing = ", ".join(sorted(_REQUIRED_TABLES - tables))
        raise InvalidDatabase(f"Workspace database is missing tables: {missing}")
    quick_check(connection)
    try:
        rows = connection.execute("SELECT id, payload FROM workspace_state").fetchall()
        if len(rows) > 1 or (rows and rows[0][0] != 1):
            raise InvalidDatabase("workspace_state must contain at most the singleton row id=1")
        if rows:
            WorkspaceSave.model_validate_json(rows[0][1])
        for view_id, name, state in connection.execute("SELECT view_id, name, state FROM views ORDER BY rowid"):
            SavedView(view_id=view_id, name=name, state=WorkspaceState.model_validate_json(state))
        palettes = connection.execute("SELECT id, payload FROM palette_library").fetchall()
        if len(palettes) > 1 or (palettes and palettes[0][0] != 1):
            raise InvalidDatabase("palette_library must contain at most the singleton row id=1")
        if palettes:
            PaletteLibrary.model_validate_json(palettes[0][1])
        # Optional until schema 2 creates it; written workspaces may already have it.
        if "target_library" in tables:
            targets = connection.execute("SELECT id, payload FROM target_library").fetchall()
            if len(targets) > 1 or (targets and targets[0][0] != 1):
                raise InvalidDatabase("target_library must contain at most the singleton row id=1")
            if targets:
                TargetLibrarySave.model_validate_json(targets[0][1])
    except ValidationError as error:
        raise InvalidDatabase(f"Invalid workspace JSON: {error}") from error


def migrate_0_to_1(connection: sqlite3.Connection) -> None:
    # chart_layout and chart_card_style are additive payload fields: old JSON
    # remains valid because WorkspaceState supplies their defaults on read.
    validate_workspace(connection)


def migrate_1_to_2(connection: sqlite3.Connection) -> None:
    """Move per-View targets into the workspace-level target library."""
    validate_workspace(connection)
    merged: dict[str, MetricTargets] = {}
    # View snapshots contribute first so the current state wins per metric.
    for _, _, state in connection.execute("SELECT view_id, name, state FROM views ORDER BY rowid").fetchall():
        merged.update(WorkspaceState.model_validate_json(state).targets)
    saved_at = 0
    for payload, in connection.execute("SELECT payload FROM workspace_state").fetchall():
        saved = WorkspaceSave.model_validate_json(payload)
        saved_at = max(saved_at, saved.saved_at)
        merged.update(saved.state.targets)
    connection.execute(_CREATE_TARGET_LIBRARY)
    connection.execute(
        "INSERT OR REPLACE INTO target_library VALUES (1, ?)",
        (TargetLibrarySave(targets=merged, saved_at=saved_at).model_dump_json(),),
    )
    # Strip the moved targets only where they exist, leaving other payloads byte-identical.
    for payload, in connection.execute("SELECT payload FROM workspace_state").fetchall():
        saved = WorkspaceSave.model_validate_json(payload)
        if saved.state.targets:
            moved = saved.model_copy(update={"state": saved.state.model_copy(update={"targets": {}})})
            connection.execute("UPDATE workspace_state SET payload=? WHERE id=1", (moved.model_dump_json(),))
    for view_id, state in connection.execute("SELECT view_id, state FROM views").fetchall():
        parsed = WorkspaceState.model_validate_json(state)
        if parsed.targets:
            moved = parsed.model_copy(update={"targets": {}})
            connection.execute("UPDATE views SET state=? WHERE view_id=?", (moved.model_dump_json(), view_id))


WORKSPACE_MIGRATIONS: dict[int, Migration] = {
    0: Migration(target=1, apply=migrate_0_to_1),
    1: Migration(target=2, apply=migrate_1_to_2),
}
