"""Deterministic, validated writes of output models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from f1data.models.base import OutputFile


def dumps(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_model(path: Path, model: OutputFile) -> bool:
    """Write `model` as sorted, compact JSON. Returns False (and leaves the file untouched) when only
    `generatedAt` would change, so unchanged data keeps an unchanged hash."""
    data = type(model).model_validate(model.model_dump()).model_dump(mode="json", by_alias=True)
    if path.exists():
        try:
            old = json.loads(path.read_bytes())
        except ValueError:
            old = None
        if isinstance(old, dict) and _without_generated(old) == _without_generated(data):
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(dumps(data))
    tmp.replace(path)
    return True


def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_bytes())
    except (FileNotFoundError, ValueError):
        return None


def is_complete(session_dir: Path) -> bool:
    """A session is done when its summary exists and is not provisional."""
    summary = read_json(session_dir / "summary.json")
    return isinstance(summary, dict) and summary.get("provisional") is False


def _without_generated(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k != "generatedAt"}
