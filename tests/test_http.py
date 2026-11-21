import logging

import httpx
import pytest

from f1data.http import MIN_INTERVAL_S, CacheMiss, HttpClient, HttpError, OpenF1Locked


class FakeTime:
    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def make(tmp_path, handler, **kw):
    t = FakeTime()
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = HttpClient(tmp_path, transport=httpx.MockTransport(wrapped), clock=t.clock, sleep=t.sleep, **kw)
    return client, t, seen


def test_retries_on_429_honouring_retry_after(tmp_path):
    responses = iter([httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(200, json=[1])])
    client, t, seen = make(tmp_path, lambda r: next(responses))
    assert client.get_json("https://api.openf1.org/v1/laps", {"session_key": 1}) == [1]
    assert len(seen) == 2
    assert 7 in t.sleeps


def test_exponential_backoff_on_5xx_then_raises(tmp_path):
    client, t, seen = make(tmp_path, lambda r: httpx.Response(503, text="down"))
    with pytest.raises(HttpError) as err:
        client.get_json("https://example.org/x")
    assert err.value.status == 503
    assert len(seen) == 6
    assert [s for s in t.sleeps if s >= 1] == [1, 2, 4, 8, 16]


def test_rate_limit_spaces_requests_per_host(tmp_path):
    client, t, _ = make(tmp_path, lambda r: httpx.Response(200, json=[]))
    client.get_json("https://api.openf1.org/v1/a")
    client.get_json("https://api.openf1.org/v1/b")
    assert t.sleeps == [pytest.approx(MIN_INTERVAL_S["api.openf1.org"])]


def test_jolpica_hourly_budget_blocks(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("f1data.http").http.HOURLY_BUDGET, "api.jolpi.ca", 2)
    client, t, _ = make(tmp_path, lambda r: httpx.Response(200, json={}))
    for i in range(3):
        client.get_json(f"https://api.jolpi.ca/ergast/f1/{i}.json")
    assert max(t.sleeps) > 3000


def test_provisional_cache_expires_after_ttl(tmp_path):
    client, t, seen = make(tmp_path, lambda r: httpx.Response(200, json=[len(seen)]))
    url = "https://api.openf1.org/v1/laps"
    assert client.get_json(url) == [1]
    t.now += 600
    assert client.get_json(url) == [1]  # within 1 h TTL
    t.now += 3600
    assert client.get_json(url) == [2]  # expired


def test_final_cache_never_expires_and_provisional_never_satisfies_final(tmp_path):
    client, t, seen = make(tmp_path, lambda r: httpx.Response(200, json=[len(seen)]))
    url = "https://api.openf1.org/v1/laps"
    client.get_json(url)  # provisional
    assert client.get_json(url, final=True) == [2]  # refetched as final
    t.now += 10 * 365 * 86400
    assert client.get_json(url, final=True) == [2]
    assert len(seen) == 2


def test_openf1_404_maps_to_empty_and_logs(tmp_path, caplog):
    client, _, _ = make(tmp_path, lambda r: httpx.Response(404, json={"detail": "No results found."}))
    with caplog.at_level(logging.WARNING, logger="f1data.http"):
        assert client.get_json("https://api.openf1.org/v1/starting_grid", {"session_key": 9}) == []
    assert "starting_grid?session_key=9" in caplog.text


def test_other_404_raises(tmp_path):
    client, _, _ = make(tmp_path, lambda r: httpx.Response(404, text="nope"))
    with pytest.raises(HttpError):
        client.get_json("https://api.jolpi.ca/ergast/f1/1900.json")


def test_never_sends_auth_headers(tmp_path):
    client, _, seen = make(tmp_path, lambda r: httpx.Response(200, json=[]))
    client.get_json("https://api.openf1.org/v1/x")
    client.get_bytes("https://github.com/f1db/f1db/releases/download/v1/a.zip")
    for req in seen:
        assert "authorization" not in {k.lower() for k in req.headers}
        assert "cookie" not in {k.lower() for k in req.headers}


def test_offline_serves_stale_cache_and_raises_on_miss(tmp_path):
    client, t, _ = make(tmp_path, lambda r: httpx.Response(200, json=[42]))
    client.get_json("https://api.openf1.org/v1/x")
    offline = HttpClient(tmp_path, offline=True, clock=lambda: t.now + 10**9)
    assert offline.get_json("https://api.openf1.org/v1/x") == [42]
    with pytest.raises(CacheMiss):
        offline.get_json("https://api.openf1.org/v1/missing")


def test_get_bytes_caches(tmp_path):
    client, _, seen = make(tmp_path, lambda r: httpx.Response(200, content=b"zip"))
    url = "https://github.com/f1db/f1db/releases/download/v1/a.zip"
    assert client.get_bytes(url, final=True) == b"zip"
    assert client.get_bytes(url, final=True) == b"zip"
    assert len(seen) == 1


LOCKED = {"detail": "Live F1 session in progress. Global API access (including past sessions) is restricted."}


def test_openf1_live_lockout_raises_openf1_locked_without_retry_or_cache(tmp_path):
    client, _, seen = make(tmp_path, lambda r: httpx.Response(401, json=LOCKED))
    for _ in range(2):
        with pytest.raises(OpenF1Locked) as err:
            client.get_json("https://api.openf1.org/v1/meetings", {"year": 2026})
        assert err.value.status == 401
    assert len(seen) == 2  # not retried within a call, not cached between calls


def test_other_401_is_a_plain_http_error(tmp_path):
    client, _, _ = make(tmp_path, lambda r: httpx.Response(401, json={"detail": "nope"}))
    with pytest.raises(HttpError) as err:
        client.get_json("https://api.openf1.org/v1/meetings")
    assert not isinstance(err.value, OpenF1Locked)


LATEST = "https://github.com/f1db/f1db/releases/latest"
TAGGED = "https://github.com/f1db/f1db/releases/tag/v2026.21.0"


def test_redirect_target_returns_location_without_following_and_caches(tmp_path):
    client, t, seen = make(tmp_path, lambda r: httpx.Response(302, headers={"location": TAGGED}))
    assert client.redirect_target(LATEST) == TAGGED
    assert client.redirect_target(LATEST) == TAGGED
    assert [str(r.url) for r in seen] == [LATEST]
    t.now += 3601  # provisional TTL
    client.redirect_target(LATEST)
    assert len(seen) == 2


def test_redirect_target_falls_back_to_last_known_target(tmp_path, caplog):
    responses = iter([httpx.Response(302, headers={"location": TAGGED}), httpx.Response(403, text="rate limited")])
    client, t, _ = make(tmp_path, lambda r: next(responses))
    client.redirect_target(LATEST)
    t.now += 3601
    with caplog.at_level(logging.WARNING):
        assert client.redirect_target(LATEST) == TAGGED
    assert "last known target" in caplog.text


def test_redirect_target_raises_without_a_known_target(tmp_path):
    client, _, _ = make(tmp_path, lambda r: httpx.Response(403, text="rate limited"))
    with pytest.raises(HttpError):
        client.redirect_target(LATEST)
