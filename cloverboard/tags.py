"""Single-level, case-sensitive run labels shared by the SDK and server."""

from collections.abc import Iterable

MAX_TAGS = 64


def normalize_tags(values: Iterable[str]) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("Tags must be a list of strings")
    tags = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("Each tag must be text")
        value = value.strip()
        if not value or len(value) > 80 or any(ord(char) < 32 for char in value):
            raise ValueError("Tags require 1–80 characters without control characters")
        if value not in tags:
            tags.append(value)
        if len(tags) > MAX_TAGS:
            raise ValueError("A run supports up to 64 tags")
    return sorted(tags)
