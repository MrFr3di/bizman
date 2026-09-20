from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load curated knowledge file {path}: {exc}") from exc


def require_mapping(value: object, *, source: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{source}: expected JSON object")
    return value


def require_string(value: object, *, source: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: {field} must be a non-empty string")
    return value.strip()


def non_negative_int(value: object, *, source: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{source}: {field} must be a non-negative integer")
    return value


def string_list(value: object, *, source: str, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{source}: {field} must be an array of non-empty strings")
    return tuple(value)


def safe_child(
    root: Path,
    relative: object,
    *,
    source: str,
    field: str = "part path",
) -> Path:
    text = require_string(relative, source=source, field=field)
    candidate = (root / text).resolve(strict=True)
    resolved_root = root.resolve(strict=True)
    if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
        raise ValueError(f"{source}: {field} escapes dataset directory")
    return candidate


__all__ = [
    "load_json",
    "non_negative_int",
    "require_mapping",
    "require_string",
    "safe_child",
    "string_list",
]
