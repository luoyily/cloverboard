"""Config normalization.

Accepts argparse.Namespace, dataclasses, mappings and config file paths
(JSON / TOML / YAML), then keeps only JSON-serializable values so that
passing locals() or framework objects cannot corrupt the snapshot.
"""

import argparse
import dataclasses
import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

_UNSERIALIZABLE = object()
_CONFIG_SUFFIXES = {".json", ".toml", ".yaml", ".yml"}


def normalize_config(obj: Any) -> dict[str, Any]:
    """Convert a config-like object into a flat JSON-safe dict.

    Returns an empty dict when the object type is unsupported or a config
    file cannot be parsed; never raises for user-supplied config content.
    """
    source = _config_source(obj)
    if source is None:
        return {}

    result: dict[str, Any] = {}
    for key, value in source.items():
        converted = _to_jsonable(value)
        if converted is not _UNSERIALIZABLE:
            result[str(key)] = converted
    return result


def _config_source(obj: Any) -> Mapping[str, Any] | None:
    if isinstance(obj, argparse.Namespace):
        return vars(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, Mapping):
        return obj
    if isinstance(obj, (str, os.PathLike)):
        return _load_config_file(Path(obj))
    return None


def _load_config_file(path: Path) -> Mapping[str, Any] | None:
    if path.suffix.lower() not in _CONFIG_SUFFIXES or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix == ".toml":
            data: Any = tomllib.loads(text)
        elif suffix in (".yaml", ".yml"):
            data = _load_yaml(text)
        else:
            data = json.loads(text)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, Mapping) else None


def _load_yaml(text: str) -> Any:
    try:
        import yaml
    except ImportError:
        return None
    return yaml.safe_load(text)


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, os.PathLike):
        return str(value)
    if isinstance(value, Mapping):
        nested: dict[str, Any] = {}
        for key, item in value.items():
            converted = _to_jsonable(item)
            if converted is not _UNSERIALIZABLE:
                nested[str(key)] = converted
        return nested
    if isinstance(value, (list, tuple)):
        nested_list: list[Any] = []
        for item in value:
            converted = _to_jsonable(item)
            if converted is not _UNSERIALIZABLE:
                nested_list.append(converted)
        return nested_list
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return _UNSERIALIZABLE
    return value
