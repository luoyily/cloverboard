"""Optional workspace-relative metadata; runtime file access keeps its original paths."""

import os
from pathlib import Path


def metadata_path(value: str, workspace_root: Path | None) -> str:
    if workspace_root is None or "\n" in value or "\r" in value:
        return value
    path = Path(value)
    if not path.is_absolute():
        return value
    try:
        return Path(os.path.abspath(path)).relative_to(workspace_root).as_posix()
    except ValueError:
        return value


def relative_metadata(value, workspace_root: Path | None):
    if workspace_root is None:
        return value
    if isinstance(value, str):
        return metadata_path(value, workspace_root)
    if isinstance(value, dict):
        return {key: relative_metadata(item, workspace_root) for key, item in value.items()}
    if isinstance(value, list):
        return [relative_metadata(item, workspace_root) for item in value]
    return value


def metadata_argument(value: str, workspace_root: Path | None) -> str:
    option, separator, argument = value.partition("=")
    if separator and option.startswith("-"):
        return option + "=" + metadata_path(argument, workspace_root)
    return metadata_path(value, workspace_root)


def metadata_error(error: Exception, workspace_root: Path | None) -> str:
    if workspace_root is not None and isinstance(error, OSError) and error.filename:
        message = f"{error.strerror}: {metadata_path(str(error.filename), workspace_root)}"
        if error.filename2:
            message += f" -> {metadata_path(str(error.filename2), workspace_root)}"
        return message
    return str(error)
