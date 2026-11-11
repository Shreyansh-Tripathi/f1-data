from datetime import UTC, datetime

import pytest

from f1data.build import ids
from f1data.build.drivers import TEAM_SHORT_NAMES, active_driver_ids, build_drivers, build_teams
from f1data.build.standings import build_driver_standings, build_team_standings
from f1data.sources import f1db, jolpica, openf1

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


@pytest.fixture(scope="module")
def db(shared_client):
    return f1db.download_json_single(shared_client, f1db.latest_release(shared_client))


@pytest.fixture(scope="module")
def drivers_out(season_data, latest, db):
    drivers, idmap = latest
    out = build_drivers(2026, season_data["known"], season_data["results"], drivers, idmap, db, NOW)
    return {d.id: d for d in out.drivers}, out


def test_drivers(drivers_out):
    by_id, out = drivers_out
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
    short = {t.id: t.short_name for t in out.teams}
    assert short == {
        "alpine": "Alpine",
        "aston_martin": "Aston Martin",
        "audi": "Audi",
        "cadillac": "Cadillac",
        "ferrari": "Ferrari",
        "haas": "Haas",
        "mclaren": "McLaren",
        "mercedes": "Mercedes",
        "rb": "Racing Bulls",
        "red_bull": "Red Bull",
        "williams": "Williams",
    }
    assert {t.id: t.name for t in out.teams}["red_bull"] == "Red Bull"  # full name unchanged (Jolpica)


def test_every_season_team_has_a_curated_short_name(season_data, latest):
    # Every team in this season's teams.json needs a TEAM_SHORT_NAMES entry, so a new team is noticed
    # here instead of silently falling back to its full name.
    drivers, idmap = latest
    out = build_teams(2026, season_data["results"], drivers, idmap, NOW)
    missing = sorted(t.id for t in out.teams if t.id not in TEAM_SHORT_NAMES)
    assert missing == [], f"add {missing} to TEAM_SHORT_NAMES"


def test_team_short_name_falls_back_to_full_name_for_unknown_ids():
    row = {
        "Driver": {"driverId": "someone"},
        "Constructor": {"constructorId": "brabham", "name": "Brabham F1 Team", "nationality": "British"},
        "number": "7",
        "positionText": "1",
    }
    out = build_teams(2026, [{"round": "1", "Results": [row]}], [], ids.IdMap({}, {}), NOW)
    assert [(t.id, t.name, t.short_name) for t in out.teams] == [("brabham", "Brabham F1 Team", "Brabham F1 Team")]


def test_identity_falls_back_to_f1db_when_jolpica_lacks_it(drivers_out, season_data):
    # Camara's first race was round 20: Jolpica has only his name, F1DB has the rest.
    assert season_data["known"]["rafael_camara"].code is None
    camara = drivers_out[0]["rafael_camara"]
    assert camara.code == "CAM"
    assert camara.nationality == "Brazilian"  # F1DB country id mapped to Jolpica's demonym style
    assert camara.date_of_birth.isoformat() == "2005-05-05"
    assert camara.number == 51  # OpenF1 race number
    assert camara.colour == "#9C9FA2"
    assert camara.headshot_url is None  # OpenF1 has no headshot for him yet


def test_jolpica_identity_wins_when_present(drivers_out, season_data):
    for driver_id, j in season_data["known"].items():
        d = drivers_out[0][driver_id]
        if j.code:
            assert d.code == j.code
        if j.nationality:
            assert d.nationality == j.nationality
        if j.date_of_birth:
            assert d.date_of_birth.isoformat() == j.date_of_birth


def test_no_identity_nulls(drivers_out):
    missing = [
        (d.id, k)
        for d in drivers_out[1].drivers
        for k in ("code", "nationality", "date_of_birth", "number")
        if getattr(d, k) is None
    ]
    assert missing == []


def test_inactive_drivers_keep_jolpica_number_without_openf1(drivers_out, season_data):
    for d in drivers_out[1].drivers:
        if not d.active:
            assert d.number == season_data["known"][d.id].number
