"""Shared HTTP client: per-host rate limiting, retry with backoff, on-disk cache.

Every network request in the pipeline goes through `HttpClient`. It sends no auth
headers, with one exception: Actions' own GITHUB_TOKEN to api.github.com (`TOKEN_HOSTS`).
Cache entries are either *final* (kept forever) or provisional (re-fetched after
`ttl_s`); callers decide which via `build.finality`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "f1-data/0.1 (+https://github.com/Shreyansh-Tripathi/f1-data)"
PROVISIONAL_TTL_S = 3600
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 5  # backoff 1, 2, 4, 8, 16 s

# Minimum seconds between requests to the same host.
MIN_INTERVAL_S = {
    "api.openf1.org": 2.0,  # observed ~30 req / min (429 + Retry-After: 60 when faster)
    "api.jolpi.ca": 0.5,  # ~4 req / s
    "api.multiviewer.app": 1.0,
    "api.github.com": 1.0,
    "github.com": 1.0,
}
# Rolling one-hour request budget per host.
HOURLY_BUDGET = {"api.jolpi.ca": 180}

# The only host that may receive a token: GitHub's API, with the workflow's built-in GITHUB_TOKEN.
TOKEN_HOSTS = {"api.github.com"}

OPENF1_HOST = "api.openf1.org"
OPENF1_EMPTY = "No results found."
OPENF1_LOCKED = "Live F1 session in progress"


class HttpError(Exception):
    def __init__(self, url: str, status: int, body: str = "") -> None:
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.url = url
        self.status = status


class OpenF1Locked(HttpError):
    """OpenF1 refuses unauthenticated requests, past sessions included, while a session is live."""


class CacheMiss(Exception):
    """Raised in offline mode when a URL is not in the cache."""


def full_url(url: str, params: dict[str, Any] | None = None) -> str:
    if not params:
        return url
    return f"{url}?{urlencode(sorted(params.items()))}"


class HttpClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        offline: bool = False,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        ttl_s: float = PROVISIONAL_TTL_S,
    ) -> None:
        self.cache_dir = Path(cache_dir) / "http"
        self.offline = offline
        self.ttl_s = ttl_s
        self._clock = clock
        self._sleep = sleep
        self._last_hit: dict[str, float] = {}
        self._window: dict[str, deque[float]] = {}
        self.request_counts: dict[str, int] = {}
        self._client = httpx.Client(
            timeout=120,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ public

    def get_json(
        self, url: str, params: dict[str, Any] | None = None, *, final: bool = False, token: str | None = None
    ) -> Any:
        """`token` is sent as a bearer token, and only to `TOKEN_HOSTS`; it is not part of the cache key."""
        key = full_url(url, params)
        if token and urlparse(key).hostname not in TOKEN_HOSTS:
            raise ValueError(f"refusing to send a token to {urlparse(key).hostname}")
        path = self._cache_path(key, ".json")
        cached = self._read_cache(path, final)
        if cached is not None:
            return cached["body"]
        resp = self._fetch(key, headers={"Authorization": f"Bearer {token}"} if token else None)
        if resp.status_code == 404 and urlparse(key).hostname == OPENF1_HOST and _is_openf1_empty(resp):
            log.warning("OpenF1 404 mapped to []: %s", key)
            body: Any = []
        elif resp.status_code == 401 and urlparse(key).hostname == OPENF1_HOST and _is_openf1_locked(resp):
            raise OpenF1Locked(key, resp.status_code, resp.text)
        elif not resp.is_success:
            raise HttpError(key, resp.status_code, resp.text)
        else:
            body = resp.json()
        self._write_cache(path, {"url": key, "fetchedAt": self._clock(), "final": final, "body": body})
        return body

    def get_bytes(self, url: str, *, final: bool = False) -> bytes:
        path = self._cache_path(url, ".bin")
        meta_path = path.with_suffix(".meta.json")
        meta = self._read_cache(meta_path, final)
        if meta is not None and path.exists():
            return path.read_bytes()
        resp = self._fetch(url)
        if not resp.is_success:
            raise HttpError(url, resp.status_code, resp.text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        self._write_cache(meta_path, {"url": url, "fetchedAt": self._clock(), "final": final, "body": None})
        return resp.content

    def redirect_target(self, url: str) -> str:
        """Where `url` redirects to (e.g. GitHub's "latest release" page to the tagged one), without following it.
        Cached for the provisional TTL."""
        path = self._cache_path(url, ".json")
        cached = self._read_cache(path, False)
        if cached is not None:
            return cached["body"]
        resp = self._fetch(url, follow_redirects=False)
        if not resp.is_redirect or "location" not in resp.headers:
            raise HttpError(url, resp.status_code, resp.text)
        target = urljoin(url, resp.headers["location"])
        self._write_cache(path, {"url": url, "fetchedAt": self._clock(), "final": False, "body": target})
        return target

    def cached(self, url: str, params: dict[str, Any] | None = None) -> tuple[float, Any] | None:
        """(fetchedAt, body) of the last cached response for `url`, however old; None if never fetched."""
        path = self._cache_path(full_url(url, params), ".json")
        if not path.exists():
            return None
        entry = json.loads(path.read_text())
        return entry["fetchedAt"], entry["body"]

    # ------------------------------------------------------------------ cache

    def _cache_path(self, key: str, suffix: str) -> Path:
        host = urlparse(key).hostname or "unknown"
        digest = hashlib.sha256(key.encode()).hexdigest()[:32]
        return self.cache_dir / host / f"{digest}{suffix}"

    def _read_cache(self, path: Path, want_final: bool) -> dict | None:
        if not path.exists():
            if self.offline:
                raise CacheMiss(str(path))
            return None
        entry = json.loads(path.read_text())
        if self.offline or entry["final"]:
            return entry
        # A provisional entry never satisfies a request for final data.
        if not want_final and self._clock() - entry["fetchedAt"] < self.ttl_s:
            return entry
        return None

    def _write_cache(self, path: Path, entry: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
        tmp.replace(path)

    # ------------------------------------------------------------------ network

    def _fetch(
        self, url: str, *, follow_redirects: bool = True, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        if self.offline:
            raise CacheMiss(url)
        host = urlparse(url).hostname or ""
        for attempt in range(MAX_RETRIES + 1):
            self._throttle(host)
            try:
                resp = self._client.get(url, follow_redirects=follow_redirects, headers=headers)
            except httpx.TransportError as exc:
                if attempt == MAX_RETRIES:
                    raise
                delay = 2.0**attempt
                log.warning("transport error %s for %s, retry in %.0fs", type(exc).__name__, url, delay)
                self._sleep(delay)
                continue
            if resp.status_code in RETRY_STATUS and attempt < MAX_RETRIES:
                delay = _retry_after(resp) or 2.0**attempt
                log.warning("HTTP %s for %s, retry in %.0fs", resp.status_code, url, delay)
                self._sleep(delay)
                continue
            return resp
        raise AssertionError("unreachable")

    def _throttle(self, host: str) -> None:
        now = self._clock()
        wait = MIN_INTERVAL_S.get(host, 0.0) - (now - self._last_hit.get(host, -1e9))
        if wait > 0:
            self._sleep(wait)
            now += wait
        budget = HOURLY_BUDGET.get(host)
        if budget:
            window = self._window.setdefault(host, deque())
            while window and now - window[0] >= 3600:
                window.popleft()
            if len(window) >= budget:
                wait = 3600 - (now - window[0])
                log.warning("hourly budget for %s reached (%d), waiting %.0fs", host, budget, wait)
                self._sleep(wait)
                now += wait
                window.popleft()
            window.append(now)
        self._last_hit[host] = now
        self.request_counts[host] = self.request_counts.get(host, 0) + 1


def _is_openf1_empty(resp: httpx.Response) -> bool:
    try:
        return resp.json().get("detail") == OPENF1_EMPTY
    except (ValueError, AttributeError):
        return False


def _is_openf1_locked(resp: httpx.Response) -> bool:
    try:
        return str(resp.json().get("detail", "")).startswith(OPENF1_LOCKED)
    except (ValueError, AttributeError):
        return False


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
