"""Shared migration planning and reporting types."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
from typing import Literal

DatabaseKind = Literal["workspace", "run"]
PlanStatus = Literal["current", "migrate", "unsupported", "newer", "invalid"]
MigrationApply = Callable[[sqlite3.Connection], None]


class MigrationError(RuntimeError):
    """A database cannot be planned or migrated safely."""


class UnsupportedSchema(MigrationError):
    """The database version is outside the supported migration range."""


class InvalidDatabase(MigrationError):
    """The database is corrupt or contains invalid stored settings."""


@dataclass(frozen=True)
class Migration:
    """One step from the source version (the registry key) to ``target``."""

    target: int
    apply: MigrationApply


@dataclass
class DatabasePlan:
    kind: DatabaseKind
    path: Path
    version: int
    target: int
    status: PlanStatus
    label: str
    run_id: str | None = None
    detail: str = ""

    @property
    def needs_migration(self) -> bool:
        return self.status == "migrate"


@dataclass
class MigrationReport:
    dry_run: bool
    plans: list[DatabasePlan] = field(default_factory=list)
    backups: list[Path] = field(default_factory=list)
    backup_root: Path | None = None
    applied: list[DatabasePlan] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        blocked = {"unsupported", "newer", "invalid"}
        return not self.errors and all(plan.status not in blocked for plan in self.plans)
