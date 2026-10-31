"""Discover, preflight, back up, and migrate a log directory in one command."""

from collections.abc import Iterator
from contextlib import closing, contextmanager, nullcontext
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from cloverboard.migrations.model import (
    DatabaseKind, DatabasePlan, InvalidDatabase, Migration, MigrationError, MigrationReport,
    PlanStatus, UnsupportedSchema,
)
from cloverboard.migrations.runs import RUN_MIGRATIONS
from cloverboard.migrations.sqlite import (
    backup_database, checkpoint, connect_readonly, connect_writable, quick_check,
    read_user_version, transaction, write_user_version,
)
from cloverboard.migrations.versions import (
    MIN_SUPPORTED_RUN_SCHEMA, MIN_SUPPORTED_WORKSPACE_SCHEMA,
    RUN_SCHEMA_VERSION, WORKSPACE_SCHEMA_VERSION,
)
from cloverboard.migrations.workspace import WORKSPACE_MIGRATIONS, validate_workspace
from cloverboard.storage.paths import UnsafeLogPathError, resolve_log_path, validate_run_id


def inspect_logdir(logdir: str | Path) -> list[DatabasePlan]:
    """Inspect the workspace and every run database without modifying anything."""
    root = _logdir(logdir)
    plans: list[DatabasePlan] = []
    workspace = _workspace_path(root)
    if workspace is not None:
        plans.append(_inspect_workspace(workspace))
    plans.extend(_inspect_runs(root))
    return plans


def migrate_logdir(
    logdir: str | Path, *, apply: bool = False, backup_dir: str | Path | None = None,
) -> MigrationReport:
    """Plan and optionally apply all workspace and run migrations together.

    A dry run is the default. ``apply=True`` acquires a logdir lock, backs up
    every database that needs work, then migrates runs before the workspace.
    There is no cross-file transaction; failed runs can be retried safely.
    """
    root = _logdir(logdir)
    report = MigrationReport(dry_run=not apply)
    lock = _migration_lock(root) if apply else nullcontext()
    with lock:
        report.plans = inspect_logdir(root)
        _collect_preflight_errors(report)
        if not report.ok:
            return report
        pending = [plan for plan in report.plans if plan.needs_migration]
        if not apply or not pending:
            return report

        try:
            backup_root = _backup_root(root, backup_dir)
        except OSError as error:
            report.errors.append(f"Could not create backup directory: {error}")
            return report
        report.backup_root = backup_root

        try:
            for plan in pending:
                destination = backup_root / _relative_database_path(plan)
                backup_database(plan.path, destination)
                report.backups.append(destination)
        except (MigrationError, OSError, sqlite3.Error) as error:
            shutil.rmtree(backup_root, ignore_errors=True)
            report.backup_root = None
            report.errors.append(f"Backup failed: {error}")
            return report

        # Runs first; the workspace version is the completion marker.
        for plan in sorted(pending, key=lambda item: (item.kind == "workspace", item.label)):
            try:
                _apply_plan(plan)
            except (MigrationError, OSError, sqlite3.Error) as error:
                report.errors.append(f"{plan.label}: {error}")
                break
            plan.status = "current"
            plan.version = plan.target
            plan.detail = "migrated"
            report.applied.append(plan)
        return report


def upgrade_database(
    connection: sqlite3.Connection, version: int, target: int, migrations: dict[int, Migration],
) -> int:
    """Apply consecutive registered steps, committing each step independently."""
    while version < target:
        migration = migrations.get(version)
        if migration is None:
            raise UnsupportedSchema(f"No migration from schema {version} to {version + 1}")
        if migration.target != version + 1:
            raise MigrationError(
                f"Migration from schema {version} targets {migration.target}, expected {version + 1}"
            )
        with transaction(connection):
            migration.apply(connection)
            write_user_version(connection, migration.target)
        version = migration.target
    return version


def _logdir(logdir: str | Path) -> Path:
    root = Path(logdir).expanduser().resolve()
    if not root.is_dir():
        raise MigrationError(f"Log directory does not exist: {root}")
    return root


def _workspace_path(root: Path) -> Path | None:
    try:
        path = resolve_log_path(root, ".workspace", "workspace.sqlite")
    except UnsafeLogPathError:
        return None
    return path if path.is_file() else None


def _inspect_workspace(path: Path) -> DatabasePlan:
    target = WORKSPACE_SCHEMA_VERSION
    version = -1
    try:
        with closing(connect_readonly(path)) as connection:
            version = read_user_version(connection)
            if version > target:
                return _plan("workspace", path, version, target, "newer",
                             detail=f"schema {version} is newer than {target}")
            if version < MIN_SUPPORTED_WORKSPACE_SCHEMA:
                return _plan("workspace", path, version, target, "unsupported",
                             detail=f"schema {version} predates the supported baseline")
            if version == target:
                return _plan("workspace", path, version, target, "current")
            if not _can_upgrade(version, target, WORKSPACE_MIGRATIONS):
                return _plan("workspace", path, version, target, "unsupported",
                             detail=f"no migration path to schema {target}")
            validate_workspace(connection)
            return _plan("workspace", path, version, target, "migrate")
    except (InvalidDatabase, OSError, sqlite3.Error) as error:
        return _plan("workspace", path, version, target, "invalid", detail=str(error))


def _inspect_runs(root: Path) -> list[DatabasePlan]:
    plans: list[DatabasePlan] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        try:
            validate_run_id(entry.name)
            path = resolve_log_path(root, entry.name, "run.sqlite")
        except UnsafeLogPathError:
            continue
        if path.is_file():
            plans.append(_inspect_run(path, entry.name))
    return plans


def _inspect_run(path: Path, run_id: str) -> DatabasePlan:
    target = RUN_SCHEMA_VERSION
    version = -1
    try:
        with closing(connect_readonly(path)) as connection:
            version = read_user_version(connection)
            if version > target:
                return _plan("run", path, version, target, "newer", run_id=run_id,
                             detail=f"schema {version} is newer than {target}")
            if version < MIN_SUPPORTED_RUN_SCHEMA:
                return _plan("run", path, version, target, "unsupported", run_id=run_id,
                             detail=f"schema {version} predates the supported baseline")
            if version == target:
                return _plan("run", path, version, target, "current", run_id=run_id)
            if not _can_upgrade(version, target, RUN_MIGRATIONS):
                return _plan("run", path, version, target, "unsupported", run_id=run_id,
                             detail=f"no migration path to schema {target}")
            quick_check(connection)
            return _plan("run", path, version, target, "migrate", run_id=run_id)
    except (InvalidDatabase, OSError, sqlite3.Error) as error:
        return _plan("run", path, version, target, "invalid", run_id=run_id, detail=str(error))


def _plan(
    kind: DatabaseKind, path: Path, version: int, target: int, status: PlanStatus,
    *, run_id: str | None = None, detail: str = "",
) -> DatabasePlan:
    label = "workspace" if kind == "workspace" else f"run {run_id or path.parent.name}"
    return DatabasePlan(
        kind=kind, path=path, version=version, target=target, status=status,
        label=label, run_id=run_id, detail=detail,
    )


def _can_upgrade(version: int, target: int, migrations: dict[int, Migration]) -> bool:
    seen: set[int] = set()
    while version < target:
        if version in seen:
            return False
        seen.add(version)
        migration = migrations.get(version)
        if migration is None or migration.target != version + 1:
            return False
        version = migration.target
    return version == target


def _collect_preflight_errors(report: MigrationReport) -> None:
    for plan in report.plans:
        if plan.status in {"unsupported", "newer", "invalid"}:
            report.errors.append(f"{plan.label}: {plan.detail}")


def _backup_root(root: Path, backup_dir: str | Path | None) -> Path:
    base = Path(backup_dir).expanduser().resolve() if backup_dir is not None else root / ".cloverboard-backups"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = base / f"{stamp}-{uuid4().hex[:8]}"
    destination.mkdir(parents=True, exist_ok=False)
    return destination


def _relative_database_path(plan: DatabasePlan) -> Path:
    if plan.kind == "workspace":
        return Path(".workspace") / "workspace.sqlite"
    return Path(plan.run_id or plan.path.parent.name) / "run.sqlite"


def _apply_plan(plan: DatabasePlan) -> None:
    migrations = WORKSPACE_MIGRATIONS if plan.kind == "workspace" else RUN_MIGRATIONS
    minimum = MIN_SUPPORTED_WORKSPACE_SCHEMA if plan.kind == "workspace" else MIN_SUPPORTED_RUN_SCHEMA
    with closing(connect_writable(plan.path)) as connection:
        version = read_user_version(connection)
        if version > plan.target:
            raise UnsupportedSchema(f"schema {version} is newer than {plan.target}")
        if version < minimum:
            raise UnsupportedSchema(f"schema {version} predates the supported baseline")
        if version == plan.target:
            return
        if not _can_upgrade(version, plan.target, migrations):
            raise UnsupportedSchema(f"no migration path from schema {version} to {plan.target}")
        upgrade_database(connection, version, plan.target, migrations)
        checkpoint(connection)


@contextmanager
def _migration_lock(root: Path) -> Iterator[None]:
    path = root / ".cloverboard-migrate.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise MigrationError(f"Another migration is already running (lock: {path})") from error
    except OSError as error:
        raise MigrationError(f"Could not create migration lock {path}: {error}") from error
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode())
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)
