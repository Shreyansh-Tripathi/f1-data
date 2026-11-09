"""Offline test harness: replay recorded responses (tests/fixtures/http/index.json) through HttpClient."""

from __future__ import annotations

import gzip
import io
import json
import zipfile
from functools import cache
from pathlib import Path
from typing import Any

import httpx
import pytest

from f1data.http import HttpClient, full_url

FIXTURES = Path(__file__).parent / "fixtures" / "http"


@cache
def _index() -> dict[str, dict[str, str]]:
    return json.loads((FIXTURES / "index.json").read_text())


def _read(file: str) -> bytes:
    raw = (FIXTURES / file).read_bytes()
    return gzip.decompress(raw) if file.endswith(".gz") else raw


def _respond(request: httpx.Request) -> httpx.Response:
    entry = _index().get(str(request.url))
    if entry is None:
        return httpx.Response(599, text=f"no fixture for {request.url}")
    body = _read(entry["file"])
    if entry["kind"] == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("f1db.json", body)
        body = buf.getvalue()
    return httpx.Response(200, content=body)


def load(url: str, params: dict[str, Any] | None = None) -> Any:
    """Recorded JSON body for a URL, as the source fetcher would see it."""
    return json.loads(_read(_index()[full_url(url, params)]["file"]))


def fixture_client(cache_dir: Path) -> HttpClient:
    """HttpClient that serves recorded fixtures, never touching the network or sleeping."""
    return HttpClient(cache_dir, transport=httpx.MockTransport(_respond), sleep=lambda s: None)


@pytest.fixture
def client(tmp_path) -> HttpClient:
    return fixture_client(tmp_path)


@pytest.fixture(scope="session")
def shared_client(tmp_path_factory) -> HttpClient:
    """One cached client for read-only builder tests."""
    return fixture_client(tmp_path_factory.mktemp("cache"))


@pytest.fixture(scope="session")
def season_data(shared_client) -> dict[str, Any]:
    from f1data.build import ids
    from f1data.sources import jolpica, openf1

    c = shared_client
    meetings = openf1.meetings(c, 2026)
    sessions = openf1.sessions(c, 2026)
    schedule = jolpica.schedule(c, 2026)
    results = jolpica.results(c, 2026)
    qualifying = jolpica.qualifying(c, 2026)
    sprint = jolpica.sprint(c, 2026)
    return {
        "meetings": meetings,
        "sessions": sessions,
        "schedule": schedule,
        "results": results,
        "qualifying": qualifying,
        "sprint": sprint,
        "weekends": ids.match_weekends(schedule, meetings, sessions),
        "known": ids.season_drivers(results, qualifying, sprint),
    }
