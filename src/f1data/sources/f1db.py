"""F1DB release download (GitHub releases of f1db/f1db)."""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import re
import zipfile
from typing import Any

import httpx

from f1data.http import HttpClient, HttpError

log = logging.getLogger(__name__)

REPO = "https://github.com/f1db/f1db"
API_LATEST = "https://api.github.com/repos/f1db/f1db/releases/latest"
# Redirects to /releases/tag/<tag>. github.com pages aren't subject to the API's 60 unauthenticated requests
# per hour per IP, which shared Actions runners exhaust.
LATEST = f"{REPO}/releases/latest"
ASSET = "f1db-json-single.zip"
TAG = re.compile(r"/releases/tag/([^/?#]+)$")
FAILURES = (HttpError, httpx.HTTPError, KeyError, ValueError)


def latest_tag(client: HttpClient) -> str:
    """Latest release tag: the API with Actions' GITHUB_TOKEN when set, else (or if that fails) the
    releases/latest redirect, else the most recently cached answer from either."""
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        try:
            return client.get_json(API_LATEST, token=token)["tag_name"]
        except FAILURES as exc:
            log.warning("F1DB release via API failed, trying the releases/latest redirect: %s", exc)
    try:
        return _tag(client.redirect_target(LATEST))
    except FAILURES as exc:
        cached = _cached_tag(client)
        if cached is None:
            raise
        log.warning("F1DB releases/latest redirect failed, using cached release %s: %s", cached, exc)
        return cached


def _tag(location: str) -> str:
    m = TAG.search(location)
    if not m:
        raise ValueError(f"unexpected F1DB latest-release redirect: {location}")
    return m.group(1)


def _cached_tag(client: HttpClient) -> str | None:
    found = []
    if (api := client.cached(API_LATEST)) and isinstance(api[1], dict) and api[1].get("tag_name"):
        found.append((api[0], api[1]["tag_name"]))
    if redirect := client.cached(LATEST):
        with contextlib.suppress(ValueError):
            found.append((redirect[0], _tag(redirect[1])))
    return max(found)[1] if found else None


def asset_url(tag: str) -> str:
    return f"{REPO}/releases/download/{tag}/{ASSET}"


def download_json_single(client: HttpClient, tag: str) -> dict[str, Any]:
    """Download and unzip the single-file JSON database. Cached per release tag (URL contains the tag)."""
    blob = client.get_bytes(asset_url(tag), final=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return json.loads(zf.read("f1db.json"))
