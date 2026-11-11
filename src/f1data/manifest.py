"""manifest.json: sha256 and last-change time of every published file."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from f1data.io import read_json, write_model
from f1data.models.manifest import Manifest, ManifestEntry

MANIFEST = "manifest.json"


def build_manifest(out_dir: Path, now: datetime, full_build: bool = False) -> Manifest:
    previous = read_json(out_dir / MANIFEST) or {}
    old_files = previous.get("files", {})
    files = {}
    for path in sorted(out_dir.rglob("*.json")):
        rel = path.relative_to(out_dir).as_posix()
        if rel == MANIFEST:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        old = old_files.get(rel)
        updated = old["updatedAt"] if old and old.get("hash") == digest else now
        files[rel] = ManifestEntry(hash=digest, updated_at=updated)
    last_full = now if full_build else previous.get("lastFullBuildAt")
    return Manifest(generated_at=now, files=files, last_full_build_at=last_full)


def write_manifest(out_dir: Path, now: datetime, full_build: bool = False) -> bool:
    return write_model(out_dir / MANIFEST, build_manifest(out_dir, now, full_build))
