from __future__ import annotations

from f1data.models.base import Model, OutputFile, Utc


class ManifestEntry(Model):
    hash: str
    updated_at: Utc


class Manifest(OutputFile):
    files: dict[str, ManifestEntry]
    last_full_build_at: Utc | None  # last whole-season build without failures (drives the 24 h fallback)
