from __future__ import annotations

from f1data.models.base import Model, OutputFile, Utc


class ManifestEntry(Model):
    hash: str
    updated_at: Utc


class Manifest(OutputFile):
    files: dict[str, ManifestEntry]
