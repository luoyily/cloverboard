"""Log directory boundaries shared by experiment and workspace storage."""

from pathlib import Path


class UnsafeLogPathError(ValueError):
    """A requested path is invalid or resolves outside the log directory."""


def validate_run_id(run_id: str) -> str:
    if (
        not run_id or run_id.startswith(".") or run_id != run_id.rstrip(" .")
        or any(character in run_id for character in "/\\:\0")
    ):
        raise UnsafeLogPathError("Invalid run path")
    return run_id


def resolve_log_path(root: Path, *parts: str) -> Path:
    try:
        candidate = root.joinpath(*parts).resolve()
    except (OSError, ValueError, RuntimeError) as error:
        raise UnsafeLogPathError("Invalid log path") from error
    if not candidate.is_relative_to(root):
        raise UnsafeLogPathError("Path is outside the log directory")
    return candidate
