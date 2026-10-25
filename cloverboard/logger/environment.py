"""Best-effort environment snapshot recorded at init().

Every probe degrades silently: a missing package, GPU or command never
interrupts the training process.
"""

import importlib.metadata
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from cloverboard.logger.paths import metadata_argument


def collect_environment(capture_hardware: bool = True, workspace_root: Path | None = None) -> dict[str, Any]:
    """Return base runtime info and, optionally, detected GPU names."""
    environment: dict[str, Any] = {
        "argv": [metadata_argument(value, workspace_root) for value in sys.argv],
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "pytorch_version": _installed_version("torch"),
    }
    if capture_hardware:
        gpus = _query_gpus()
        if gpus:
            environment["gpus"] = gpus
    return environment


def _installed_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _query_gpus() -> list[str]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return []
    try:
        completed = subprocess.run(
            [executable, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]
