import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from cloverboard.logger.config import normalize_config


@dataclass
class ModelConfig:
    hidden: int = 512
    dropout: float = 0.1


def test_normalize_namespace():
    namespace = argparse.Namespace(lr=0.1, name="demo")

    assert normalize_config(namespace) == {"lr": 0.1, "name": "demo"}


def test_normalize_dataclass():
    assert normalize_config(ModelConfig()) == {"hidden": 512, "dropout": 0.1}


def test_normalize_mapping():
    assert normalize_config({"lr": 0.1, "layers": (1, 2)}) == {"lr": 0.1, "layers": [1, 2]}


def test_normalize_filters_unserializable_values():
    payload = {
        "lr": 0.1,
        "epochs": 10,
        "module": sys,
        "callback": len,
        "model": object(),
        "path": Path("runs"),
        "nested": {"ok": 1, "bad": object()},
        "items": [1, "x", object()],
    }

    assert normalize_config(payload) == {
        "lr": 0.1,
        "epochs": 10,
        "path": "runs",
        "nested": {"ok": 1},
        "items": [1, "x"],
    }


def test_normalize_numpy_scalars():
    assert normalize_config({"lr": np.float32(0.5), "steps": np.int64(10)}) == {
        "lr": 0.5,
        "steps": 10,
    }


def test_normalize_json_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"lr": 0.1, "model": {"hidden": 512}}', encoding="utf-8")

    assert normalize_config(path) == {"lr": 0.1, "model": {"hidden": 512}}


def test_normalize_toml_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[model]\nhidden = 512\n", encoding="utf-8")

    assert normalize_config(path) == {"model": {"hidden": 512}}


def test_normalize_yaml_file(tmp_path):
    pytest.importorskip("yaml")
    path = tmp_path / "config.yaml"
    path.write_text("lr: 0.1\nmodel:\n  hidden: 512\n", encoding="utf-8")

    assert normalize_config(path) == {"lr": 0.1, "model": {"hidden": 512}}


def test_normalize_unsupported_object_returns_empty():
    assert normalize_config(42) == {}
    assert normalize_config("not/a/file.yaml") == {}


def test_normalize_missing_file_returns_empty(tmp_path):
    assert normalize_config(tmp_path / "missing.json") == {}
