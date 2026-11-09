from datetime import UTC, datetime

import pytest

from f1data.build import ids
from f1data.build.drivers import active_driver_ids, build_drivers, build_teams
from f1data.build.standings import build_driver_standings, build_team_standings
from f1data.sources import jolpica, openf1

NOW = datetime(2026, 11, 10, tzinfo=UTC)


@pytest.fixture(scope="module")
def latest(season_data, shared_client):
    race = next(r for r in season_data["results"] if r["round"] == "20")
    drivers = openf1.drivers(shared_client, 11412)
    return drivers, ids.session_ids(drivers, ids.round_entries(race), season_data["known"])


def test_driver_standings_keep_inactive_with_flag(season_data, shared_client):
    per_round = {rnd: jolpica.driver_standings(shared_client, 2026, rnd) for rnd in (19, 20)}
    active = active_driver_ids(season_data["results"])
    s = build_driver_standings(2026, per_round, active, NOW)
    r20 = s.rounds[-1]
    assert [r.round for r in s.rounds] == [19, 20]
    assert len(r20.entries) == 25
    assert sum(e.active for e in r20.entries) == 22
    inactive = [e for e in r20.entries if not e.active]
    assert {e.driver_id for e in inactive} == {e["Driver"]["driverId"] for e in per_round[20]} - active
    assert r20.entries[0].position == 1 and r20.entries[0].points > 0


def test_team_standings(shared_client):
    per_round = {20: jolpica.constructor_standings(shared_client, 2026, 20)}
    s = build_team_standings(2026, per_round, NOW)
    assert len(s.rounds[0].entries) == 11
    assert s.rounds[0].entries[0].position == 1


def test_drivers(season_data, latest):
    drivers, idmap = latest
    out = build_drivers(2026, season_data["known"], season_data["results"], drivers, idmap, NOW)
    by_id = {d.id: d for d in out.drivers}
    norris = by_id["norris"]
    assert norris.number == 1 and norris.code == "NOR" and norris.team_id == "mclaren"
    assert norris.colour == "#F47600"
    assert norris.headshot_url.startswith("https://media.formula1.com/")
    assert sum(d.active for d in out.drivers) == 22


def test_teams(season_data, latest):
    drivers, idmap = latest
    out = build_teams(2026, season_data["results"], drivers, idmap, NOW)
    assert len(out.teams) == 11
    mclaren = next(t for t in out.teams if t.id == "mclaren")
    assert mclaren.colour == "#F47600"
    assert "norris" in mclaren.driver_ids
    assert all(len(t.driver_ids) == 2 for t in out.teams)
