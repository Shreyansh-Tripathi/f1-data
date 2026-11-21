"""F1DB release download (GitHub releases of f1db/f1db)."""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any

from f1data.http import HttpClient

REPO = "https://github.com/f1db/f1db"
# Redirects to /releases/tag/<tag>. github.com pages aren't subject to the API's 60 requests / hour per IP,
# which shared Actions runners exhaust.
LATEST = f"{REPO}/releases/latest"
ASSET = "f1db-json-single.zip"
TAG = re.compile(r"/releases/tag/([^/?#]+)$")


def latest_tag(client: HttpClient) -> str:
    location = client.redirect_target(LATEST)
    m = TAG.search(location)
    if not m:
        raise ValueError(f"unexpected F1DB latest-release redirect: {location}")
    return m.group(1)


def asset_url(tag: str) -> str:
    return f"{REPO}/releases/download/{tag}/{ASSET}"


def download_json_single(client: HttpClient, tag: str) -> dict[str, Any]:
    """Download and unzip the single-file JSON database. Cached per release tag (URL contains the tag)."""
    blob = client.get_bytes(asset_url(tag), final=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return json.loads(zf.read("f1db.json"))
