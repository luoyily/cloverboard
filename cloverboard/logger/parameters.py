"""Explicit, one-shot parameter collection without importing training frameworks."""

import inspect
from functools import wraps

from cloverboard.logger.api import log_config
from cloverboard.logger.config import normalize_config


def track_params(*, prefix: str = "params", exclude: tuple[str, ...] = ()):
    """Record bound arguments and defaults on calls made after init()."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                arguments = signature.bind(*args, **kwargs)
                arguments.apply_defaults()
                ignored = {"self", "cls", "model", "optimizer", "tensor", *exclude}
                values = normalize_config({key: value for key, value in arguments.arguments.items() if key not in ignored})
                log_config({prefix: values} if prefix else values)
            except Exception:
                pass
            return function(*args, **kwargs)
        return wrapped
    return decorate


def watch(model=None, optimizer=None, *, prefix: str = "watch") -> None:
    """Collect model size and optimizer groups once; changing values belong in log()."""
    values = {}
    try:
        if model is not None:
            parameters = list(model.parameters())
            values["model"] = {"type": type(model).__name__, "parameters": sum(p.numel() for p in parameters),
                               "trainable_parameters": sum(p.numel() for p in parameters if p.requires_grad)}
        if optimizer is not None:
            values["optimizer"] = {"type": type(optimizer).__name__, "param_groups": [normalize_config({k: v for k, v in group.items() if k != "params"}) for group in optimizer.param_groups]}
        log_config({prefix: values} if prefix else values)
    except Exception:
        pass
