"""Workspace database migrations."""

import sqlite3

from pydantic import ValidationError

from cloverboard.migrations.model import InvalidDatabase, Migration
from cloverboard.migrations.sqlite import quick_check
from cloverboard.server.models import PaletteLibrary, SavedView, WorkspaceSave, WorkspaceState

_REQUIRED_TABLES = {"workspace_state", "views", "palette_library"}


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
    except ValidationError as error:
        raise InvalidDatabase(f"Invalid workspace JSON: {error}") from error


def migrate_0_to_1(connection: sqlite3.Connection) -> None:
    # chart_layout and chart_card_style are additive payload fields: old JSON
    # remains valid because WorkspaceState supplies their defaults on read.
    validate_workspace(connection)


WORKSPACE_MIGRATIONS: dict[int, Migration] = {
    0: Migration(target=1, apply=migrate_0_to_1),
}
