"""Workspace SQLite storage, independent of each run's training database."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from cloverboard.server.models import (
    SavedView, SavedViewInput, ViewRename, WorkspaceSave, WorkspaceSnapshot,
    WorkspaceState, ColorRegistry, ColorAllocation,
)
from cloverboard.storage.paths import resolve_log_path

router = APIRouter(prefix="/api/workspace", tags=["Workspace"])
_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_state (
    id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS views (
    view_id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS color_registry (
    id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL
);
"""


class WorkspaceStore:
    def __init__(self, logdir: str | Path | None = None, read_only: bool = False):
        self._root = Path(logdir).resolve() if logdir is not None else None
        identity = os.path.normcase(str(self._root)) if self._root is not None else uuid4().hex
        self.workspace_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.writable = not read_only
        self._lock = RLock()
        self._seed = uuid4().hex
        self._memory = sqlite3.connect(":memory:", check_same_thread=False) if self._root is None else None
        if self._memory is not None:
            self._memory.executescript(_SCHEMA)

    @contextmanager
    def _connection(self, write: bool = False):
        if write:
            self._require_writable()
        with self._lock:
            connection = self._memory
            try:
                if self._root is not None:
                    directory = resolve_log_path(self._root, ".workspace")
                    path = resolve_log_path(self._root, ".workspace", "workspace.sqlite")
                    for suffix in ("-wal", "-shm", "-journal"):
                        resolve_log_path(self._root, ".workspace", "workspace.sqlite" + suffix)
                    if not write and not path.is_file():
                        yield None
                        return
                    if write:
                        directory.mkdir(parents=True, exist_ok=True)
                        connection = sqlite3.connect(path, timeout=5)
                        connection.execute("PRAGMA journal_mode = WAL")
                        connection.executescript(_SCHEMA)
                    else:
                        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                yield connection
                if write:
                    connection.commit()
            except (sqlite3.Error, OSError) as error:
                if connection is not None:
                    connection.rollback()
                raise HTTPException(503, "Workspace database is unavailable") from error
            except BaseException:
                if write and connection is not None:
                    connection.rollback()
                raise
            finally:
                if connection is not None and connection is not self._memory:
                    connection.close()

    def _require_writable(self):
        if not self.writable:
            raise HTTPException(403, "Workspace is read-only")

    def colors(self, keys: list[str] | None = None) -> ColorRegistry:
        with self._connection(write=keys is not None) as connection:
            row = connection.execute("SELECT payload FROM color_registry WHERE id=1").fetchone() if connection else None
            try:
                colors = ColorRegistry.model_validate_json(row[0]) if row else ColorRegistry(seed=self._seed)
                slots = list(colors.slots.values())
                if len(set(slots)) != len(slots) or any(slot < 0 or slot > 100000 for slot in slots):
                    raise ValueError("Invalid color allocation")
            except (ValidationError, ValueError):
                colors = ColorRegistry(seed=self._seed)
            if keys is not None:
                slots = dict(colors.slots)
                next_slot = max(slots.values(), default=-1) + 1
                for key in sorted(set(keys)):
                    if key not in slots:
                        slots[key] = next_slot
                        next_slot += 1
                colors = colors.model_copy(update={"slots": slots})
                connection.execute("INSERT OR REPLACE INTO color_registry VALUES (1, ?)", (colors.model_dump_json(),))
            return colors

    def snapshot(self) -> WorkspaceSnapshot:
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM workspace_state WHERE id=1").fetchone() if connection else None
            try:
                saved = WorkspaceSave.model_validate_json(row[0]) if row else None
            except ValidationError:
                saved = None
            rows = connection.execute("SELECT view_id, name, state FROM views ORDER BY rowid").fetchall() if connection else []
            views = []
            for row in rows:
                try:
                    views.append(SavedView(view_id=row[0], name=row[1], state=WorkspaceState.model_validate_json(row[2])))
                except ValidationError:
                    continue
            state = saved.state if saved else None
            if state and state.active_view_id and not any(view.view_id == state.active_view_id for view in views):
                state = state.model_copy(update={"active_view_id": None})
            return WorkspaceSnapshot(
                workspace_id=self.workspace_id, writable=self.writable,
                state=state, saved_at=saved.saved_at if saved else 0, views=views,
            )

    def save_state(self, payload: WorkspaceSave) -> WorkspaceSnapshot:
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT payload FROM workspace_state WHERE id=1").fetchone()
            try:
                previous = WorkspaceSave.model_validate_json(row[0]) if row else None
            except ValidationError:
                previous = None
            if previous is None or previous.saved_at <= payload.saved_at:
                connection.execute("INSERT OR REPLACE INTO workspace_state VALUES (1, ?)", (payload.model_dump_json(),))
        return self.snapshot()

    @staticmethod
    def _check_name(connection, name: str, view_id: str | None):
        if any(row[0] != view_id and row[1].casefold() == name.casefold() for row in connection.execute("SELECT view_id, name FROM views")):
            raise HTTPException(409, "A view with this name already exists")

    def save_view(self, payload: SavedViewInput, view_id: str | None = None) -> SavedView:
        with self._connection(write=True) as connection:
            if view_id and not connection.execute("SELECT 1 FROM views WHERE view_id=?", (view_id,)).fetchone():
                raise HTTPException(404, "View not found")
            name = payload.name.strip()
            self._check_name(connection, name, view_id)
            view = SavedView(view_id=view_id or uuid4().hex, name=name, state=payload.state.model_copy(update={"active_view_id": None}))
            connection.execute(
                "INSERT INTO views VALUES (?, ?, ?) ON CONFLICT(view_id) DO UPDATE SET name=excluded.name, state=excluded.state",
                (view.view_id, view.name, view.state.model_dump_json()),
            )
            return view

    def rename_view(self, view_id: str, name: str) -> SavedView:
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT state FROM views WHERE view_id=?", (view_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "View not found")
            name = name.strip()
            self._check_name(connection, name, view_id)
            connection.execute("UPDATE views SET name=? WHERE view_id=?", (name, view_id))
            return SavedView(view_id=view_id, name=name, state=WorkspaceState.model_validate_json(row[0]))

    def delete_view(self, view_id: str) -> SavedView:
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT name, state FROM views WHERE view_id=?", (view_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "View not found")
            connection.execute("DELETE FROM views WHERE view_id=?", (view_id,))
            return SavedView(view_id=view_id, name=row[0], state=WorkspaceState.model_validate_json(row[1]))


def get_workspace(request: Request) -> WorkspaceStore:
    return request.app.state.workspace


@router.get("", response_model=WorkspaceSnapshot)
def read_workspace(store: WorkspaceStore = Depends(get_workspace)):
    """Read current Web viewing state and named views; writable applies to workspace persistence."""
    return store.snapshot()


@router.get("/colors", response_model=ColorRegistry)
def read_colors(store: WorkspaceStore = Depends(get_workspace)):
    """Read the stable identity-to-color-slot registry without allocating slots."""
    return store.colors()


@router.put("/colors", response_model=ColorRegistry)
def allocate_colors(payload: ColorAllocation, store: WorkspaceStore = Depends(get_workspace)):
    """Allocate missing keys without replacing existing slots; return the full registry."""
    return store.colors(payload.keys)


@router.put("/state", response_model=WorkspaceSnapshot)
def save_workspace(payload: WorkspaceSave, store: WorkspaceStore = Depends(get_workspace)):
    """Save Web viewing state if saved_at is not older; client timestamps use milliseconds.

    Return the current full snapshot even when an older write is ignored.
    """
    return store.save_state(payload)


@router.post("/views", response_model=SavedView, status_code=201)
def create_view(payload: SavedViewInput, store: WorkspaceStore = Depends(get_workspace)):
    """Create a named viewing-state snapshot; names are unique ignoring case."""
    return store.save_view(payload)


@router.put("/views/{view_id}", response_model=SavedView)
def update_view(view_id: str, payload: SavedViewInput, store: WorkspaceStore = Depends(get_workspace)):
    """Replace a named view's name and viewing-state snapshot."""
    return store.save_view(payload, view_id)


@router.patch("/views/{view_id}", response_model=SavedView)
def rename_view(view_id: str, payload: ViewRename, store: WorkspaceStore = Depends(get_workspace)):
    """Rename a view without changing its viewing-state snapshot."""
    return store.rename_view(view_id, payload.name)


@router.delete("/views/{view_id}", response_model=SavedView)
def delete_view(view_id: str, store: WorkspaceStore = Depends(get_workspace)):
    """Delete a view and return the deleted snapshot."""
    return store.delete_view(view_id)
