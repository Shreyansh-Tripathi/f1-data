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
