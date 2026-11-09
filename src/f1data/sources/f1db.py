"""F1DB release download (GitHub releases of f1db/f1db)."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from f1data.http import HttpClient

LATEST_RELEASE = "https://api.github.com/repos/f1db/f1db/releases/latest"
ASSET = "f1db-json-single.zip"


def latest_release(client: HttpClient) -> dict[str, Any]:
    return client.get_json(LATEST_RELEASE)


def download_json_single(client: HttpClient, release: dict[str, Any]) -> dict[str, Any]:
    """Download and unzip the single-file JSON database. Cached per release tag (URL contains the tag)."""
    asset = next(a for a in release["assets"] if a["name"] == ASSET)
    blob = client.get_bytes(asset["browser_download_url"], final=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return json.loads(zf.read("f1db.json"))
