"""MultiViewer circuit info, reached via OpenF1 `meetings.circuit_info_url`."""

from __future__ import annotations

from typing import Any

from f1data.http import HttpClient

ALLOWED_PREFIX = "https://api.multiviewer.app/"


def circuit(client: HttpClient, url: str) -> dict[str, Any]:
    """Fetched once per circuit per season and cached permanently."""
    if not url.startswith(ALLOWED_PREFIX):
        raise ValueError(f"refusing circuit_info_url outside {ALLOWED_PREFIX}: {url}")
    return client.get_json(url, final=True)
