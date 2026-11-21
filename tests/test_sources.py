import pytest

from f1data.sources import f1db, jolpica, multiviewer, openf1


def test_jolpica_season_pages_merge_split_races(client):
    races = jolpica.results(client, 2026)
    assert [int(r["round"]) for r in races] == list(range(1, 21))
    # Every race has its full classification even when split across 100-row pages.
    assert all(len(r["Results"]) == 22 for r in races)
    assert races[-1]["raceName"] == "Brazilian Grand Prix"


def test_jolpica_sprint_and_standings(client):
    sprints = jolpica.sprint(client, 2026)
    assert len(sprints) == 6
    standings = jolpica.driver_standings(client, 2026, 20)
    assert standings[0]["position"] == "1"


def test_openf1_starting_grid_by_meeting(client):
    grid = openf1.starting_grid(client, 1299)
    assert len(grid) == 22
    assert {g["session_key"] for g in grid} == {11408}  # the qualifying session


def test_multiviewer_rejects_foreign_urls(client):
    with pytest.raises(ValueError):
        multiviewer.circuit(client, "https://evil.example/api/v1/circuits/14/2026")


def test_f1db_subset_roundtrip(client):
    tag = f1db.latest_tag(client)
    assert tag == "v2026.20.0"
    db = f1db.download_json_single(client, tag)
    assert {"drivers", "seasons"} <= set(db)
    assert any(d["id"] == "fernando-alonso" for d in db["drivers"])


def test_f1db_latest_tag_rejects_unexpected_redirect(tmp_path):
    import httpx
    import pytest

    from f1data.http import HttpClient

    transport = httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "https://github.com/login"}))
    client = HttpClient(tmp_path, transport=transport, sleep=lambda s: None)
    with pytest.raises(ValueError, match="unexpected F1DB"):
        f1db.latest_tag(client)


class FakeGitHub:
    """api.github.com and github.com responses keyed by host; records requests."""

    def __init__(self, api, web):
        self.responses = {"api.github.com": api, "github.com": web}
        self.seen = []

    def __call__(self, request):
        import httpx

        self.seen.append(request)
        status, body = self.responses[request.url.host]
        if status == 200:
            return httpx.Response(200, json=body)
        if status == 302:
            return httpx.Response(302, headers={"location": body})
        return httpx.Response(status, text="rate limited")


def github_client(tmp_path, fake, clock=None):
    import httpx

    from f1data.http import HttpClient

    kw = {"clock": clock} if clock else {}
    return HttpClient(tmp_path, transport=httpx.MockTransport(fake), sleep=lambda s: None, **kw)


TAG_URL = "https://github.com/f1db/f1db/releases/tag/v2026.21.0"


def test_f1db_tag_uses_api_with_github_token(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t0k")
    fake = FakeGitHub((200, {"tag_name": "v2026.21.0"}), (302, TAG_URL))
    assert f1db.latest_tag(github_client(tmp_path, fake)) == "v2026.21.0"
    assert [(r.url.host, r.headers.get("authorization")) for r in fake.seen] == [("api.github.com", "Bearer t0k")]


def test_f1db_tag_falls_back_to_redirect_when_api_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t0k")
    fake = FakeGitHub((403, None), (302, TAG_URL))
    assert f1db.latest_tag(github_client(tmp_path, fake)) == "v2026.21.0"
    assert "authorization" not in fake.seen[-1].headers  # never to github.com


def test_f1db_tag_without_token_skips_the_api(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    fake = FakeGitHub((200, {"tag_name": "wrong"}), (302, TAG_URL))
    assert f1db.latest_tag(github_client(tmp_path, fake)) == "v2026.21.0"
    assert [r.url.host for r in fake.seen] == ["github.com"]


def test_f1db_tag_falls_back_to_cached_release(tmp_path, monkeypatch):
    import pytest

    now = [1_000_000.0]
    monkeypatch.setenv("GITHUB_TOKEN", "t0k")
    ok = FakeGitHub((200, {"tag_name": "v2026.20.0"}), (302, TAG_URL))
    assert f1db.latest_tag(github_client(tmp_path, ok, lambda: now[0])) == "v2026.20.0"
    now[0] += 7200  # cache expired
    down = FakeGitHub((403, None), (500, None))
    assert f1db.latest_tag(github_client(tmp_path, down, lambda: now[0])) == "v2026.20.0"
    with pytest.raises(Exception):  # noqa: B017 - nothing cached anywhere
        f1db.latest_tag(github_client(tmp_path / "empty", down))
