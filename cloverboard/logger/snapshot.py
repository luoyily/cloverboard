"""A bounded, standalone archive of explicitly selected project files."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import tomllib
import zipfile

from cloverboard.logger.context import get_context
from cloverboard.logger.paths import metadata_error, metadata_path
from cloverboard.storage.paths import resolve_log_path

_IGNORED = {".git", ".venv", "venv", "env", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache"}


def _pattern(pattern: str):
    pattern = pattern.replace("\\", "/")
    if not pattern or pattern.startswith("/") or ":" in pattern or ".." in pattern.split("/"):
        raise ValueError("Snapshot patterns must be relative to root")
    expression = re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return re.compile("^" + expression + "$")


def _link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def snapshot_code(root=None, *, include=None, exclude=(), config=None, max_files=2000, max_bytes=50 * 1024 * 1024, max_file_bytes=5 * 1024 * 1024, timeout=5.0) -> Path | None:
    """Capture selected files once per Run, before training starts.

    Patterns support *, ** and ?. A TOML [snapshot] section can supply root,
    include and exclude; its root is relative to that TOML file. Limits are
    checked between files. Partial/error manifests remain visible in the UI.
    """
    context = get_context()
    if context is None or not context.run_id:
        return None
    destination = None
    manifest = {"version": 1, "created_at": time.time(), "status": "complete", "files": [], "skipped": [], "total_bytes": 0}
    try:
        run_path = resolve_log_path(context.logdir.resolve(), context.run_id)
        destination = resolve_log_path(run_path, "snapshot")
        if destination.exists():
            return destination / "code.zip" if (destination / "code.zip").is_file() else None
        destination.mkdir()
        if config is not None:
            config_path = Path(config).resolve()
            options = tomllib.loads(config_path.read_text(encoding="utf-8"))["snapshot"]
            root = root if root is not None else config_path.parent / options.get("root", ".")
            include = include if include is not None else options.get("include")
            exclude = [*options.get("exclude", []), *exclude]
        if root is None or not include or isinstance(include, str) or isinstance(exclude, str):
            raise ValueError("Specify root and a nonempty include list")
        if min(max_files, max_bytes, max_file_bytes, timeout) <= 0:
            raise ValueError("Snapshot limits must be positive")
        source = Path(root).resolve()
        if not source.is_dir():
            raise ValueError("Snapshot root is not a directory")
        inclusions, exclusions = [_pattern(p) for p in include], [_pattern(p) for p in exclude]
        manifest.update(root=metadata_path(str(source), context.workspace_root), include=list(include), exclude=list(exclude))
        deadline = time.monotonic() + timeout
        stop = False
        with zipfile.ZipFile(destination / "code.zip.tmp", "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for folder, directories, names in os.walk(source, followlinks=False):
                if time.monotonic() > deadline:
                    manifest.update(status="partial", limit_reason="Time limit reached")
                    break
                for name in directories[:]:
                    path = Path(folder) / name
                    relative = path.relative_to(source).as_posix()
                    if name in _IGNORED or _link(path) or path.resolve().is_relative_to(context.logdir.resolve()) or any(p.fullmatch(relative) or p.fullmatch(relative + "/") for p in exclusions):
                        directories.remove(name)
                for name in sorted(names):
                    if time.monotonic() > deadline or len(manifest["files"]) >= max_files:
                        manifest["status"] = "partial"
                        manifest["limit_reason"] = "Time or file-count limit reached"
                        stop = True
                        break
                    path = Path(folder) / name
                    relative = path.relative_to(source).as_posix()
                    if not any(p.fullmatch(relative) for p in inclusions) or any(p.fullmatch(relative) for p in exclusions):
                        continue
                    try:
                        if _link(path) or not path.resolve().is_relative_to(source) or path.resolve().is_relative_to(context.logdir.resolve()):
                            raise ValueError("Link or log output excluded")
                        before = path.stat()
                        if before.st_size > max_file_bytes or manifest["total_bytes"] + before.st_size > max_bytes:
                            raise ValueError("Size limit exceeded")
                        with path.open("rb") as stream:
                            content = stream.read(max_file_bytes + 1)
                        after = path.stat()
                        if len(content) > max_file_bytes or manifest["total_bytes"] + len(content) > max_bytes:
                            raise ValueError("Size limit exceeded")
                        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                            raise ValueError("File changed during capture")
                        archive.writestr(relative, content)
                        manifest["files"].append({"path": relative, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
                        manifest["total_bytes"] += len(content)
                    except (OSError, ValueError) as error:
                        manifest["skipped"].append({"path": relative, "reason": metadata_error(error, context.workspace_root)})
                        manifest["status"] = "partial"
                if stop:
                    break
        (destination / "code.zip.tmp").replace(destination / "code.zip")
        manifest["archive_bytes"] = (destination / "code.zip").stat().st_size
    except Exception as error:
        manifest.update(status="error", error=metadata_error(error, context.workspace_root))
    if destination is None:
        return None
    try:
        temporary = destination / "manifest.json.tmp"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination / "manifest.json")
        return destination / "code.zip" if (destination / "code.zip").is_file() else None
    except OSError:
        return None
