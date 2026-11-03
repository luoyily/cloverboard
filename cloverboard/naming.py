"""Run display-name normalization shared by the API and storage."""

MAX_DISPLAY_NAME = 200


def normalize_display_name(value: str | None) -> str | None:
    """Trim and validate a display name; empty or whitespace values clear it."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Display name must be text")
    value = value.strip()
    if not value:
        return None
    if len(value) > MAX_DISPLAY_NAME:
        raise ValueError(f"Display names require 1–{MAX_DISPLAY_NAME} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Display names cannot contain control characters")
    return value
